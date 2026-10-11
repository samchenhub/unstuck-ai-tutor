import re
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Video
from unstuck.sources import youtube
from unstuck.sources.base import Source, VideoRef
from unstuck.sources.bilibili import parse as bili
from unstuck.sources.bilibili.client import USER_AGENT
from unstuck.sources.bilibili.source import NAME as BILIBILI, BilibiliSource, client_for

INVALID_MESSAGE = "不是有效的 YouTube 或 B站视频链接"
SHORT_LINK_MESSAGE = "B站短链接解析失败，请粘贴完整的视频链接"
# App share text wraps the link in a title ("【标题-哔哩哔哩】 https://b23.tv/…"); URLs are ASCII, so CJK ends them.
_URL_IN_TEXT_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_BVID_IN_TEXT_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])")


def _host(text: str) -> str:
    try:
        return (urlparse(text if "://" in text else f"https://{text}").hostname or "").lower()
    except ValueError:
        return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # surface the 30x as an HTTPError so its Location can be checked, not fetched


def _no_redirect_opener():
    return urllib.request.build_opener(_NoRedirect)


def _short_link_target(url: str) -> str:
    """Read where a b23.tv short link points (one HEAD request; the target itself is never fetched)."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with _no_redirect_opener().open(request, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if 300 <= exc.code < 400 else None
        if location:
            return urljoin(url, location)
        raise IngestError("INVALID_URL", SHORT_LINK_MESSAGE) from exc
    except Exception as exc:
        raise IngestError("INVALID_URL", SHORT_LINK_MESSAGE) from exc
    raise IngestError("INVALID_URL", SHORT_LINK_MESSAGE)


def _extract_link(text: str) -> str:
    """Pull the link (or bare BV id) out of pasted share text; plain links pass through unchanged."""
    if text.isascii() and not re.search(r"\s", text):
        return text
    if match := _URL_IN_TEXT_RE.search(text):
        return match.group(0)
    if match := _BVID_IN_TEXT_RE.search(text):
        return match.group(0)
    return text


def resolve(url: str, settings: Settings, follow_redirect: Callable[[str], str] = _short_link_target) -> VideoRef:
    """Turn a pasted link (or share text) into a VideoRef; raises IngestError (INVALID_URL / UNSUPPORTED_URL)."""
    text = _extract_link(url.strip())
    if bili.BVID_RE.fullmatch(text):
        text = bili.watch_url(text, 1)
    host = _host(text)
    if host in bili.SHORT_HOSTS:
        text = follow_redirect(text if "://" in text else f"https://{text}")
        host = _host(text)
        if not (host and bili.is_bilibili_host(host)) or host in bili.SHORT_HOSTS:
            raise IngestError("INVALID_URL", SHORT_LINK_MESSAGE)
    if host and bili.is_bilibili_host(host):
        bvid, part = bili.parse_url(text)
        return VideoRef(source=BILIBILI, video_id=bili.make_video_id(bvid, part),
                        url=bili.watch_url(bvid, part), course_id=bvid, part=part)
    try:
        return youtube.parse(text)
    except IngestError as exc:
        raise IngestError("INVALID_URL", INVALID_MESSAGE) from exc


def for_name(name: str, settings: Settings) -> Source:
    if name == youtube.NAME:
        return youtube.YouTubeSource()
    if name == BILIBILI:
        return BilibiliSource(client_for(settings))
    raise ValueError(f"unknown video source {name!r}")


def for_video(video: Video, settings: Settings) -> Source:
    return for_name(video.source, settings)


def bilibili_status(settings: Settings) -> dict:
    browser = settings.bilibili_cookies_browser
    return {"logged_in": bool(browser) and client_for(settings).check_login(), "cookies_browser": browser}
