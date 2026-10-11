"""Bilibili QR-code login (the web passport flow), so login does not depend on reading a browser's cookies.

On Windows, Chrome's app-bound cookie encryption makes its cookies unreadable to other programs; scanning a QR
code with the Bilibili app works everywhere. The cookies the login sets are saved locally in Netscape format
(the file yt-dlp loads) and never logged.
"""

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


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:  # URLError and timeouts are OSErrors; ValueError: not JSON
        raise IngestError("BILIBILI_ERROR", "连接 B站登录服务失败，请稍后重试") from exc


def _data(body: dict) -> dict:
    if body.get("code") != 0:
        raise IngestError("BILIBILI_ERROR", f"B站登录接口返回错误（{body.get('code')}）")
    return body.get("data") or {}


def start(fetch: Callable[[str], dict] = _fetch_json) -> QrStart:
    data = _data(fetch(f"{PASSPORT}/generate"))
    if not data.get("url") or not data.get("qrcode_key"):
        raise IngestError("BILIBILI_ERROR", "B站登录接口没有返回二维码")
    qr = segno.make(data["url"], error="m").svg_data_uri(scale=6, border=2, dark="#000", light="#fff")
    return QrStart(key=data["qrcode_key"], qr=qr)


def poll(key: str, cookie_file: Path, fetch: Callable[[str], dict] = _fetch_json) -> str:
    """'waiting', 'scanned', 'expired', 'done' (cookies saved to `cookie_file`) or 'failed'."""
    data = _data(fetch(f"{PASSPORT}/poll?{urllib.parse.urlencode({'qrcode_key': key})}"))
    code = data.get("code")
    if code in _STATES:
        return _STATES[code]
    if code != 0:
        raise IngestError("BILIBILI_ERROR", f"B站登录失败（{code}）")
    # The cookies come back as query parameters of a cross-domain URL (as well as Set-Cookie headers).
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(data.get("url") or "").query, keep_blank_values=True))
    cookies = {name: urllib.parse.quote(params[name], safe="%") for name in COOKIE_NAMES if params.get(name)}
    if "SESSDATA" not in cookies:
        log.warning("Bilibili QR login finished without a SESSDATA cookie")
        return "failed"
    expires = params.get("Expires", "")
    write_cookie_file(cookie_file, cookies, int(expires) if expires.isdigit() else int(time.time()) + DEFAULT_LIFETIME_S)
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
