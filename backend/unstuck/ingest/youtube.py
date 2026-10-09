import json
import re
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from unstuck.errors import IngestError
from unstuck.ingest.captions import parse_json3
from unstuck.models import Cue
from unstuck.timefmt import fmt_ts

_ID = r"[A-Za-z0-9_-]{11}"
_URL_ID_RE = re.compile(rf"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)({_ID})")
_BARE_ID_RE = re.compile(rf"^{_ID}$")

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
    if _BARE_ID_RE.match(text):
        return text
    match = _URL_ID_RE.search(text)
    if not match:
        raise IngestError("INVALID_URL", "不是有效的 YouTube 视频链接")
    return match.group(1)


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


def pick_caption_track(info: dict) -> tuple[str, str] | None:
    """Pick the best json3 caption track: manual in the video's language, then the
    original-language auto track, then any manual track."""
    lang = info.get("language") or "en"
    base = lang.split("-")[0]
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}

    for key in dict.fromkeys([lang, base, "en"]):
        if url := _json3_url(manual, key):
            return url, "manual"
    auto_keys = [f"{lang}-orig", lang, f"{base}-orig", base]
    auto_keys += [k for k in auto if k.endswith("-orig")]
    for key in dict.fromkeys(auto_keys):
        if url := _json3_url(auto, key):
            return url, "auto"
    for key in manual:
        if url := _json3_url(manual, key):
            return url, "manual"
    return None


def fetch_info(video_id: str) -> dict:
    try:
        with YoutubeDL({**_QUIET, "skip_download": True}) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
            return ydl.sanitize_info(info)
    except DownloadError as exc:
        message = str(exc)
        if "Private video" in message:
            raise IngestError("VIDEO_UNAVAILABLE", "这是私有视频，无法导入") from exc
        raise IngestError("VIDEO_UNAVAILABLE", f"无法获取视频信息：{message[:200]}") from exc


def save_info(info: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def load_info(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_captions(info: dict) -> list[Cue] | None:
    track = pick_caption_track(info)
    if track is None:
        return None
    url, _kind = track
    with YoutubeDL(_QUIET) as ydl:
        data = json.loads(ydl.urlopen(url).read().decode("utf-8"))
    return parse_json3(data) or None


def _download(video_id: str, out_dir: Path, fmt: str, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {**_QUIET, "format": fmt, "outtmpl": str(out_dir / f"{stem}.%(ext)s")}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(watch_url(video_id), download=True)
        return Path(ydl.prepare_filename(info))


def download_video(video_id: str, out_dir: Path) -> Path:
    try:
        return _download(video_id, out_dir, _VIDEO_FORMAT, "video")
    except DownloadError as exc:
        raise IngestError("DOWNLOAD_FAILED", f"视频下载失败：{str(exc)[:200]}") from exc


def download_audio(video_id: str, out_dir: Path) -> Path:
    try:
        return _download(video_id, out_dir, _AUDIO_FORMAT, "audio")
    except DownloadError as exc:
        raise IngestError("DOWNLOAD_FAILED", f"音频下载失败：{str(exc)[:200]}") from exc


def find_video_file(video_dir: Path) -> Path | None:
    for path in sorted(video_dir.glob("video.*")):
        if path.suffix not in (".part", ".ytdl") and path.is_file():
            return path
    return None
