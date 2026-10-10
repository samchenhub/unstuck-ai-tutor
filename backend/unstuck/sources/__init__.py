import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Video
from unstuck.sources import youtube
from unstuck.sources.base import Source, VideoRef
from unstuck.sources.bilibili import parse as bili
from unstuck.sources.bilibili.client import USER_AGENT
from unstuck.sources.bilibili.source import NAME as BILIBILI, BilibiliSource, client_for

INVALID_MESSAGE = "不是有效的 YouTube 或 B站视频链接"


def _host(text: str) -> str:
    try:
        return (urlparse(text if "://" in text else f"https://{text}").hostname or "").lower()
    except ValueError:
        return ""


def _follow_redirect(url: str) -> str:
    """Resolve a b23.tv short link to the bilibili.com page it redirects to."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.geturl()
    except Exception as exc:
        raise IngestError("INVALID_URL", "B站短链接解析失败，请粘贴完整的视频链接") from exc


def resolve(url: str, settings: Settings, follow_redirect: Callable[[str], str] = _follow_redirect) -> VideoRef:
    """Turn a pasted link into a VideoRef; raises IngestError (INVALID_URL / UNSUPPORTED_URL)."""
    text = url.strip()
    if bili.BVID_RE.fullmatch(text):
        text = bili.watch_url(text, 1)
    host = _host(text)
    if host in bili.SHORT_HOSTS:
        text = follow_redirect(text if "://" in text else f"https://{text}")
        host = _host(text)
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
    return {"logged_in": bool(browser) and client_for(settings).is_logged_in(force=True), "cookies_browser": browser}
