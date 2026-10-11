"""Bilibili web-API client: login cookies from the user's browser (via yt-dlp), backoff, WBI signing."""

import json
import logging
import threading
import time
import urllib.parse
from collections.abc import Callable

from yt_dlp import YoutubeDL
from yt_dlp.cookies import CookieLoadError
from yt_dlp.networking.exceptions import TransportError

from unstuck.errors import IngestError
from unstuck.sources.bilibili import parse

log = logging.getLogger(__name__)

API = "https://api.bilibili.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {"Referer": "https://www.bilibili.com/", "User-Agent": USER_AGENT}
BACKOFF_S = (2.0, 4.0)
LOGIN_CACHE_S = 300.0
_RETRY_STATUS = {412, 429, 500, 502, 503, 504}
_UNAVAILABLE_CODES = {-404, 62002, 62004, 62012}
_BLOCKED_CODES = {-352, -412}
BLOCKED_MESSAGE = "B站暂时限制了访问，请稍后重试"
UNAVAILABLE_MESSAGE = "视频不存在或不可访问（可能已删除、仅会员可见或有地区限制）"


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status", None) or getattr(exc, "code", None)


def _retryable(exc: Exception) -> bool:
    return _status(exc) in _RETRY_STATUS or isinstance(exc, (TransportError, TimeoutError, ConnectionError))


class BiliClient:
    def __init__(
        self,
        cookies_browser: str = "",
        fetch_json: Callable[[str], dict] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.cookies_browser = cookies_browser
        self._fetch_json = fetch_json or self._ydl_fetch_json
        self._sleep = sleep
        self._clock = clock
        self._ydl = None
        self._lock = threading.Lock()
        self._mixin: str | None = None
        self._login: tuple[float, bool] | None = None
        self.cookies_unavailable = False

    def ydl_options(self) -> dict:
        opts = {"quiet": True, "no_warnings": True, "noprogress": True, "http_headers": HEADERS}
        if self.cookies_browser and not self.cookies_unavailable:
            opts["cookiesfrombrowser"] = (self.cookies_browser,)
        return opts

    def _ydl_urlopen_json(self, url: str) -> dict:
        if self._ydl is None:
            self._ydl = YoutubeDL(self.ydl_options())
        with self._ydl.urlopen(url) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _ydl_fetch_json(self, url: str) -> dict:
        with self._lock:  # one shared cookie jar; Bilibili calls are few, so serialising them is fine
            try:
                return self._ydl_urlopen_json(url)
            except CookieLoadError:
                # yt-dlp reads browser cookies lazily on the first request; if that fails (browser
                # running/locked, no permission), carry on anonymously instead of failing every call.
                if self.cookies_unavailable or not self.cookies_browser:
                    raise
                log.warning("cannot read %s cookies; continuing without Bilibili login", self.cookies_browser,
                            exc_info=True)
                self.cookies_unavailable = True
                self._ydl = None
                return self._ydl_urlopen_json(url)

    def _drop_cookie_jar(self) -> None:
        with self._lock:
            self._ydl = None

    def get(self, url: str) -> dict:
        """GET a JSON document, backing off on risk control and network errors."""
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                return self._fetch_json(url)
            except Exception as exc:
                if not _retryable(exc):
                    log.warning("bilibili request failed: %s (%s)", url.split("?")[0], exc)
                    raise IngestError("BILIBILI_ERROR", "访问 B站接口失败，请稍后重试") from exc
                if attempt == len(BACKOFF_S):
                    raise IngestError("BILIBILI_BLOCKED", BLOCKED_MESSAGE) from exc
                self._sleep(BACKOFF_S[attempt])
        raise AssertionError("unreachable")

    def _data(self, body: dict) -> dict:
        code = body.get("code", 0)
        if code == 0:
            return body.get("data") or {}
        if code in _BLOCKED_CODES:
            raise IngestError("BILIBILI_BLOCKED", BLOCKED_MESSAGE)
        if code in _UNAVAILABLE_CODES:
            raise IngestError("VIDEO_UNAVAILABLE", UNAVAILABLE_MESSAGE)
        raise IngestError("BILIBILI_ERROR", f"B站接口返回错误（{code}）")

    def _url(self, path: str, params: dict) -> str:
        return f"{API}{path}?{urllib.parse.urlencode(params)}"

    def _nav_body(self) -> dict:
        return self.get(f"{API}/x/web-interface/nav")

    def nav(self) -> dict:
        # nav answers code -101 when logged out but still carries the WBI keys, so no _data() here.
        return self._nav_body().get("data") or {}

    def _mixin_key(self, refresh: bool = False) -> str:
        if self._mixin is None or refresh:
            body = self._nav_body()
            wbi = (body.get("data") or {}).get("wbi_img") or {}
            img_url, sub_url = wbi.get("img_url"), wbi.get("sub_url")
            if not img_url or not sub_url:
                if body.get("code") in _BLOCKED_CODES:
                    raise IngestError("BILIBILI_BLOCKED", BLOCKED_MESSAGE)
                raise IngestError("BILIBILI_ERROR", "访问 B站接口失败，请稍后重试")
            self._mixin = parse.mixin_key(img_url, sub_url)
        return self._mixin

    def _signed(self, path: str, params: dict) -> dict:
        for refresh in (False, True):
            signed = parse.wbi_sign(params, self._mixin_key(refresh), int(self._clock()))
            body = self.get(self._url(path, signed))  # network/risk-control failures are not a stale key
            try:
                return self._data(body)
            except IngestError as exc:
                if refresh or exc.code != "BILIBILI_ERROR":
                    raise
                log.info("signed call to %s returned code %s; refreshing WBI key", path, body.get("code"))
        raise AssertionError("unreachable")

    def is_logged_in(self, force: bool = False) -> bool:
        now = self._clock()
        if not force and self._login and now - self._login[0] < LOGIN_CACHE_S:
            return self._login[1]
        if force:
            self.cookies_unavailable = False  # let a fresh login / unlocked browser be noticed
        self._drop_cookie_jar()  # re-read browser cookies: the user may have just logged in
        try:
            logged_in = bool(self.nav().get("isLogin"))
        except Exception:
            log.warning("bilibili login check failed", exc_info=True)
            logged_in = False
        self._login = (now, logged_in)
        return logged_in

    def check_login(self) -> bool:
        """Login state for the UI: trust a cached login, re-check a logout (the user may have just logged in),
        but never re-read cookies that failed to load (on macOS that re-opens the keychain prompt)."""
        if self._login and self._login[1] and self._clock() - self._login[0] < LOGIN_CACHE_S:
            return True
        return self.is_logged_in(force=not self.cookies_unavailable)

    def view(self, bvid: str) -> dict:
        return self._data(self.get(self._url("/x/web-interface/view", {"bvid": bvid})))

    def player(self, bvid: str, cid: int) -> dict:
        return self._signed("/x/player/wbi/v2", {"bvid": bvid, "cid": cid})

    def playurl_mp4(self, bvid: str, cid: int) -> dict:
        params = {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 1, "platform": "html5", "high_quality": 1}
        return self._signed("/x/player/wbi/playurl", params)

    def playurl_dash(self, bvid: str, cid: int) -> dict:
        """DASH streams (separate audio/video; the CDN requires the bilibili Referer)."""
        return self._signed("/x/player/wbi/playurl", {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 16, "fourk": 0})

    def subtitle(self, url: str) -> dict:
        return self.get(url)
