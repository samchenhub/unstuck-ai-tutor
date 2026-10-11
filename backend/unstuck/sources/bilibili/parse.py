"""Pure Bilibili logic: links, ids, WBI signing and API response parsing (no network)."""

import hashlib
import re
import urllib.parse

from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.timefmt import fmt_ts

BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_VIDEO_ID_RE = re.compile(r"(BV[0-9A-Za-z]{10})_p([1-9][0-9]{0,3})")
_SITE_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
SHORT_HOSTS = frozenset({"b23.tv", "www.b23.tv"})

INVALID_MESSAGE = "不是有效的 B站视频链接"
COLLECTION_MESSAGE = "暂不支持合集、收藏夹或 UP 主主页，请粘贴单个视频链接"
_UNSUPPORTED_SECTIONS = {
    "cheese": "暂不支持 B站付费课程",
    "bangumi": "暂不支持番剧和影视，请粘贴单个视频链接",
    "list": COLLECTION_MESSAGE,
    "medialist": COLLECTION_MESSAGE,
    "festival": COLLECTION_MESSAGE,
}

# Fixed permutation used by Bilibili's web client to derive the WBI mixin key.
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
    12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
]
_WBI_STRIPPED = set("!'()*")

SUBTITLE_END_TOLERANCE_S = 5.0
SUBTITLE_MIN_END_RATIO = 0.8
DEFAULT_URL_LIFETIME_S = 3600.0


def is_bilibili_host(host: str) -> bool:
    return host in _SITE_HOSTS or host in SHORT_HOSTS or host.endswith(".bilibili.com")


def make_video_id(bvid: str, part: int) -> str:
    return f"{bvid}_p{part}"


def split_video_id(video_id: str) -> tuple[str, int]:
    match = _VIDEO_ID_RE.fullmatch(video_id)
    if not match:
        raise ValueError(f"not a Bilibili video id: {video_id!r}")
    return match.group(1), int(match.group(2))


def watch_url(bvid: str, part: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={part}"


def parse_url(url: str) -> tuple[str, int]:
    """Parse a full bilibili.com video link into (bvid, part). b23.tv links must be resolved first."""
    text = url.strip()
    try:
        parsed = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise IngestError("INVALID_URL", INVALID_MESSAGE) from exc
    segments = [s for s in parsed.path.split("/") if s]
    if host == "space.bilibili.com":
        raise IngestError("UNSUPPORTED_URL", COLLECTION_MESSAGE)
    if segments and segments[0] in _UNSUPPORTED_SECTIONS:
        raise IngestError("UNSUPPORTED_URL", _UNSUPPORTED_SECTIONS[segments[0]])
    if host not in _SITE_HOSTS or len(segments) < 2 or segments[0] != "video" or not BVID_RE.fullmatch(segments[1]):
        raise IngestError("INVALID_URL", INVALID_MESSAGE)
    page = (urllib.parse.parse_qs(parsed.query).get("p") or ["1"])[0]
    if not (page.isascii() and page.isdigit()) or not 1 <= int(page) <= 9999:
        raise IngestError("INVALID_URL", INVALID_MESSAGE)
    return segments[1], int(page)


def _wbi_file_key(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0]


def mixin_key(img_url: str, sub_url: str) -> str:
    raw = _wbi_file_key(img_url) + _wbi_file_key(sub_url)
    return "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def wbi_sign(params: dict, key: str, wts: int) -> dict:
    """Return params plus `wts` and `w_rid`, all stringified, as Bilibili's WBI endpoints expect."""
    values = {**params, "wts": wts}
    cleaned = {k: "".join(ch for ch in str(v) if ch not in _WBI_STRIPPED) for k, v in sorted(values.items())}
    query = urllib.parse.urlencode(cleaned)
    return {**cleaned, "w_rid": hashlib.md5((query + key).encode()).hexdigest()}


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


def info_from_view(view: dict, bvid: str, part: int) -> dict:
    pages = view.get("pages") or []
    page = next((p for p in pages if p.get("page") == part), None)
    if page is None:
        raise IngestError("VIDEO_UNAVAILABLE", f"这个视频没有第 {part} 集")
    return {
        "source": "bilibili",
        "bvid": bvid,
        "part": part,
        "cid": page["cid"],
        "title": view.get("title") or "",
        "channel": (view.get("owner") or {}).get("name") or "",
        "duration": float(page.get("duration") or 0),
        "part_title": (page.get("part") or "") if len(pages) > 1 else "",
        "parts_total": len(pages),
        "thumbnail": _https(view.get("pic") or ""),
    }


def meta_from_info(info: dict, max_duration_s: int) -> VideoMeta:
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise IngestError("VIDEO_UNAVAILABLE", "无法获取视频时长，视频可能不可用")
    if duration > max_duration_s:
        raise IngestError("TOO_LONG", f"视频时长 {fmt_ts(duration)} 超过上限 {fmt_ts(max_duration_s)}，暂不支持导入")
    return VideoMeta(
        title=info["title"], channel=info["channel"], duration=duration,
        part_title=info["part_title"], parts_total=info["parts_total"], thumbnail=info["thumbnail"],
    )


def pick_subtitle_url(subtitles: list[dict]) -> str | None:
    """Prefer an uploader-made Chinese track, then the AI Chinese one; other languages fall back to Whisper."""

    def rank(sub: dict) -> int:
        lan = sub.get("lan") or ""
        if lan.startswith("zh"):
            return 0
        if lan.startswith("ai-zh"):
            return 1
        return 2

    usable = sorted((s for s in subtitles if s.get("subtitle_url") and rank(s) < 2), key=rank)
    return _https(usable[0]["subtitle_url"]) if usable else None


def subtitle_to_cues(body: list[dict]) -> list[Cue]:
    cues = [Cue(float(item["from"]), float(item["to"]), str(item.get("content") or "").strip()) for item in body]
    return [c for c in cues if c.text and c.end >= c.start]


def subtitle_matches(cues: list[Cue], duration: float) -> bool:
    """Reject subtitles that cannot belong to this video (Bilibili sometimes serves another video's)."""
    if not cues or duration <= 0:
        return False
    last_end = max(c.end for c in cues)
    return SUBTITLE_MIN_END_RATIO * duration <= last_end <= duration + SUBTITLE_END_TOLERANCE_S


def mp4_playback(data: dict, duration: float, now: float) -> Playback:
    durl = data.get("durl") or []
    if not durl or not durl[0].get("url"):
        raise IngestError("PLAYBACK_UNAVAILABLE", "B站没有返回可播放的视频地址")
    url = _https(durl[0]["url"])
    deadline = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("deadline") or [""])[0]
    expires_at = float(deadline) if deadline.isascii() and deadline.isdigit() else now + DEFAULT_URL_LIFETIME_S
    backups = tuple(_https(u) for u in durl[0].get("backup_url") or [] if u)
    return Playback(kind="mp4", url=url, expires_at=expires_at, duration=duration, backup_urls=backups)


def dash_audio_urls(data: dict) -> list[str]:
    """The smallest DASH audio track (plenty for speech-to-text: a 1 h 45 min lecture is ~52 MB vs ~216 MB MP4),
    as its main URL followed by its mirrors; empty when there is no DASH audio."""
    tracks = [a for a in ((data.get("dash") or {}).get("audio") or []) if a.get("baseUrl") or a.get("base_url")]
    if not tracks:
        return []
    track = min(tracks, key=lambda a: a.get("bandwidth") or 0)
    backups = track.get("backupUrl") or track.get("backup_url") or []
    return [_https(u) for u in [track.get("baseUrl") or track["base_url"], *backups] if u]


def parts_from_view(view: dict, bvid: str) -> list[Part]:
    return [
        Part(part=p["page"], title=p.get("part") or "", duration=float(p.get("duration") or 0),
             video_id=make_video_id(bvid, p["page"]))
        for p in view.get("pages") or []
    ]
