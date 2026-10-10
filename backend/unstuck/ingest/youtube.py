import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, YoutubeDLError

from unstuck.errors import IngestError
from unstuck.ingest.captions import parse_json3
from unstuck.ingest.files import find_video_file, load_info, save_info  # noqa: F401  (re-exported)
from unstuck.models import Cue
from unstuck.timefmt import fmt_ts

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_ID_PATH_PREFIXES = ("shorts", "embed", "live")

_QUIET = {"quiet": True, "no_warnings": True, "noprogress": True}
# Prefer H.264 so OpenCV (scene detection) can always decode it; video-only, no merge needed.
_VIDEO_FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b"
_AUDIO_FORMAT = "ba[ext=m4a]/ba/b"


@dataclass(frozen=True)
class VideoMeta:
    id: str
    title: str
    channel: str
    duration: float


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_video_id(url: str) -> str:
    text = url.strip()
    if _ID_RE.fullmatch(text):
        return text
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise IngestError("INVALID_URL", "不是有效的 YouTube 视频链接") from exc
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/")
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 2 and parts[0] in _ID_PATH_PREFIXES:
            candidate = parts[1]
        elif parts == ["watch"] or not parts:
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    if candidate is None or not _ID_RE.fullmatch(candidate):
        raise IngestError("INVALID_URL", "不是有效的 YouTube 视频链接")
    return candidate


def check_video_info(info: dict, max_duration_s: int) -> VideoMeta:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise IngestError("LIVE_NOT_SUPPORTED", "不支持直播或尚未开始的直播")
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise IngestError("VIDEO_UNAVAILABLE", "无法获取视频时长，视频可能不可用")
    if duration > max_duration_s:
        raise IngestError(
            "TOO_LONG",
            f"视频时长 {fmt_ts(duration)} 超过上限 {fmt_ts(max_duration_s)}，暂不支持导入",
        )
    return VideoMeta(
        id=info["id"],
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=duration,
    )


def _json3_url(tracks: dict, key: str) -> str | None:
    for fmt in tracks.get(key) or []:
        if fmt.get("ext") == "json3":
            return fmt.get("url")
    return None


def _spoken_language(info: dict, auto: dict) -> str | None:
    if info.get("language"):
        return info["language"]
    for key in auto:
        if key.endswith("-orig"):
            return key.removesuffix("-orig")
    return None


def pick_caption_track(info: dict) -> tuple[str, str] | None:
    """Pick the best json3 caption track, as (url, "manual" | "auto").

    YouTube lists machine-translated auto tracks for every language, plus one
    `<lang>-orig` track for the spoken language, so we first work out the spoken
    language (`info["language"]`, else the `-orig` auto key). With it known:
    manual in that language, then auto in that language (the `-orig` track first),
    then any manual track. Unknown: manual "en", any manual track, then English auto.
    """
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    spoken = _spoken_language(info, auto)

    if spoken:
        base = spoken.split("-")[0]
        orders = [
            (manual, "manual", [spoken, base]),
            (auto, "auto", [f"{spoken}-orig", spoken, f"{base}-orig", base]),
            (manual, "manual", list(manual)),
        ]
    else:
        orders = [
            (manual, "manual", ["en"]),
            (manual, "manual", list(manual)),
            (auto, "auto", ["en-orig", "en"]),
        ]
    for tracks, kind, keys in orders:
        for key in dict.fromkeys(keys):
            if url := _json3_url(tracks, key):
                return url, kind
    return None


def fetch_info(video_id: str) -> dict:
    # ignore_no_formats_error: metadata and captions must load even when media formats
    # are blocked, so the video can still degrade to transcript-only.
    opts = {**_QUIET, "skip_download": True, "ignore_no_formats_error": True}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
            return ydl.sanitize_info(info)
    except DownloadError as exc:
        message = str(exc)
        log.warning("yt-dlp could not fetch info for %s: %s", video_id, message)
        if "Private video" in message:
            raise IngestError("VIDEO_UNAVAILABLE", "这是私有视频，无法导入") from exc
        if any(s in message for s in ("live event will begin", "Premieres in", "This live event")):
            raise IngestError("LIVE_NOT_SUPPORTED", "不支持直播或尚未开始的直播") from exc
        raise IngestError(
            "VIDEO_UNAVAILABLE", "无法获取视频信息：视频可能已删除、需要会员或有地区限制"
        ) from exc


def fetch_captions(info: dict) -> list[Cue] | None:
    """Download and parse the chosen caption track; None (never an exception) lets the
    transcript stage fall back to Whisper."""
    track = pick_caption_track(info)
    if track is None:
        return None
    url, _kind = track
    try:
        with YoutubeDL(_QUIET) as ydl, ydl.urlopen(url) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return parse_json3(data) or None
    except Exception as exc:
        log.warning("caption download/parse failed, falling back to transcription: %s", exc)
        return None


def _download(video_id: str, out_dir: Path, fmt: str, stem: str, failure: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {**_QUIET, "format": fmt, "outtmpl": str(out_dir / f"{stem}.%(ext)s")}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=True)
            return Path(ydl.prepare_filename(info))
    except (DownloadError, OSError, YoutubeDLError) as exc:
        log.warning("%s (%s): %s", failure, video_id, exc)
        raise IngestError("DOWNLOAD_FAILED", failure) from exc


def download_video(video_id: str, out_dir: Path) -> Path:
    return _download(video_id, out_dir, _VIDEO_FORMAT, "video", "视频下载失败")


def download_audio(video_id: str, out_dir: Path) -> Path:
    return _download(video_id, out_dir, _AUDIO_FORMAT, "audio", "音频下载失败")
