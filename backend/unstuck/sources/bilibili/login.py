"""Bilibili QR-code login (the web passport flow), so login does not depend on reading a browser's cookies.

On Windows, Chrome's app-bound cookie encryption makes its cookies unreadable to other programs; scanning a QR
code with the Bilibili app works everywhere. The cookies the login sets are saved locally in Netscape format
(the file yt-dlp loads) and never logged.
"""

import http.cookiejar
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import segno

from unstuck.errors import IngestError
from unstuck.sources.bilibili.client import HEADERS

log = logging.getLogger(__name__)

PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode"
COOKIE_NAMES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
DEFAULT_LIFETIME_S = 180 * 86400
# poll's data.code: 0 logged in, 86101 not scanned yet, 86090 scanned (waiting for confirmation), 86038 expired
_STATES = {86101: "waiting", 86090: "scanned", 86038: "expired"}


@dataclass(frozen=True)
class QrStart:
    key: str
    qr: str  # the login URL as an SVG data URI, ready for <img src>


@dataclass(frozen=True)
class Reply:
    body: dict  # the JSON reply ({} when the page is not JSON, e.g. a redirect page)
    cookies: dict[str, str]  # cookies the response (and its redirects) set, values as sent (URL-encoded)
    expires: int | None = None  # when SESSDATA expires, if it was set


def _fetch_json(url: str) -> Reply:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except OSError as exc:  # URLError and timeouts are OSErrors
        raise IngestError("BILIBILI_ERROR", "连接 B站登录服务失败，请稍后重试") from exc
    try:
        body = json.loads(raw) if raw.lstrip().startswith("{") else {}
    except ValueError:
        body = {}
    cookies = {c.name: c.value for c in jar if c.value}
    expires = next((c.expires for c in jar if c.name == "SESSDATA" and c.expires), None)
    return Reply(body, cookies, expires)


def _as_reply(reply: Reply | dict) -> Reply:
    return reply if isinstance(reply, Reply) else Reply(reply, {})


def _data(body: dict) -> dict:
    if body.get("code") != 0:
        raise IngestError("BILIBILI_ERROR", f"B站登录接口返回错误（{body.get('code')}）")
    return body.get("data") or {}


def start(fetch: Callable[[str], Reply | dict] = _fetch_json) -> QrStart:
    data = _data(_as_reply(fetch(f"{PASSPORT}/generate")).body)
    if not data.get("url") or not data.get("qrcode_key"):
        raise IngestError("BILIBILI_ERROR", "B站登录接口没有返回二维码")
    qr = segno.make(data["url"], error="m").svg_data_uri(scale=6, border=2, dark="#000", light="#fff")
    return QrStart(key=data["qrcode_key"], qr=qr)


def poll(key: str, cookie_file: Path, fetch: Callable[[str], Reply | dict] = _fetch_json) -> str:
    """'waiting', 'scanned', 'expired', 'done' (cookies saved to `cookie_file`) or 'failed'."""
    reply = _as_reply(fetch(f"{PASSPORT}/poll?{urllib.parse.urlencode({'qrcode_key': key})}"))
    data = _data(reply.body)
    code = data.get("code")
    if code in _STATES:
        return _STATES[code]
    if code != 0:
        raise IngestError("BILIBILI_ERROR", f"B站登录失败（{code}）")
    # Where Bilibili puts the login has varied: Set-Cookie on this reply, the query of data.url (a
    # cross-domain URL), or cookies set when that URL is opened. Take whichever carries SESSDATA.
    cross_url = data.get("url") or ""
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cross_url).query, keep_blank_values=True))
    from_url = {name: urllib.parse.quote(params[name], safe="%") for name in COOKIE_NAMES if params.get(name)}
    cookies = {**from_url, **{n: v for n, v in reply.cookies.items() if n in COOKIE_NAMES}}
    expires = reply.expires or (int(params["Expires"]) if params.get("Expires", "").isdigit() else None)
    seen = {"reply": sorted(reply.cookies), "url": sorted(params)}
    if "SESSDATA" not in cookies and cross_url.startswith("https://"):
        followed = _as_reply(fetch(cross_url))
        cookies.update({n: v for n, v in followed.cookies.items() if n in COOKIE_NAMES})
        expires = expires or followed.expires
        seen["followed"] = sorted(followed.cookies)
    if "SESSDATA" not in cookies:
        log.warning("Bilibili QR login finished without SESSDATA; cookie/parameter names seen: %s", seen)
        return "failed"
    write_cookie_file(cookie_file, cookies, expires or int(time.time()) + DEFAULT_LIFETIME_S)
    log.info("Bilibili QR login saved")
    return "done"


def write_cookie_file(path: Path, cookies: dict[str, str], expires: int) -> None:
    lines = ["# Netscape HTTP Cookie File", "# Bilibili login saved by Unstuck (QR-code login). Keep this private.", ""]
    lines += [f".bilibili.com\tTRUE\t/\tFALSE\t{expires}\t{name}\t{value}" for name, value in cookies.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _owner_only(tmp)
    tmp.replace(path)


def _owner_only(path: Path) -> None:
    """The cookies log into the user's Bilibili account: keep other local users out."""
    if os.name == "posix":
        path.chmod(0o600)
        return
    user = os.environ.get("USERNAME")
    if os.name == "nt" and user:
        try:  # drop inherited ACLs (e.g. Authenticated Users on a drive root) and grant only this user
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                           capture_output=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            log.warning("could not restrict access to the Bilibili login file")


def logout(cookie_file: Path) -> None:
    cookie_file.unlink(missing_ok=True)
