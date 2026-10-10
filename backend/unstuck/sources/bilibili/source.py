import functools
import logging
import time
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, YoutubeDLError

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse
from unstuck.sources.bilibili.client import BiliClient

log = logging.getLogger(__name__)

NAME = "bilibili"
# Analysis only needs pictures; H.264 keeps OpenCV scene detection happy.
_VIDEO_FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b"
_AUDIO_FORMAT = "ba/b"


def _http_retry_sleep(n: int) -> float:
    return min(2 ** n, 30)


# B站 CDN cuts long single-connection downloads and rate-limits bursts: fetch in 1 MB ranges, retry with backoff.
_ROBUST_DOWNLOAD_OPTS = {
    "http_chunk_size": 1 << 20,
    "retries": 10,
    "fragment_retries": 10,
    "retry_sleep_functions": {"http": _http_retry_sleep},
}


@functools.lru_cache(maxsize=4)
def _client(cookies_browser: str) -> BiliClient:
    return BiliClient(cookies_browser)


def client_for(settings: Settings) -> BiliClient:
    return _client(settings.bilibili_cookies_browser)


class BilibiliSource:
    name = NAME

    def __init__(self, client: BiliClient, clock=time.time):
        self.client = client
        self._clock = clock

    def fetch_info(self, video_id: str) -> dict:
        bvid, part = parse.split_video_id(video_id)
        return parse.info_from_view(self.client.view(bvid), bvid, part)

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta:
        return parse.meta_from_info(info, max_duration_s)

    def fetch_captions(self, info: dict) -> list[Cue] | None:
        """Bilibili AI/uploader subtitles; None (never an exception) falls back to Whisper."""
        try:
            subtitles = ((self.client.player(info["bvid"], info["cid"]).get("subtitle") or {}).get("subtitles")) or []
            url = parse.pick_subtitle_url(subtitles)
            if url is None:
                log.info("no Bilibili subtitles for %s", info["bvid"])
                return None
            cues = parse.subtitle_to_cues(self.client.subtitle(url).get("body") or [])
        except Exception:
            log.warning("Bilibili subtitles failed for %s, falling back to transcription", info.get("bvid"), exc_info=True)
            return None
        if not parse.subtitle_matches(cues, float(info["duration"])):
            log.warning("Bilibili subtitles do not match %s (wrong video?); using transcription", info["bvid"])
            return None
        return cues

    def _download(self, video_id: str, out_dir: Path, fmt: str, stem: str, failure: str) -> Path:
        bvid, part = parse.split_video_id(video_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        opts = {**self.client.ydl_options(), **_ROBUST_DOWNLOAD_OPTS, "format": fmt, "noplaylist": True,
                "outtmpl": str(out_dir / f"{stem}.%(ext)s")}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(parse.watch_url(bvid, part), download=True)
                return Path(ydl.prepare_filename(info))
        except (DownloadError, OSError, YoutubeDLError) as exc:
            log.warning("%s (%s): %s", failure, video_id, exc)
            raise IngestError("DOWNLOAD_FAILED", failure) from exc

    def download_video(self, video_id: str, out_dir: Path) -> Path:
        return self._download(video_id, out_dir, _VIDEO_FORMAT, "video", "视频下载失败")

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        return self._download(video_id, out_dir, _AUDIO_FORMAT, "audio", "音频下载失败")

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        info = self.fetch_info(video_id)
        data = self.client.playurl_mp4(info["bvid"], info["cid"])
        return parse.mp4_playback(data, info["duration"], now=self._clock())

    def parts(self, video_id: str) -> list[Part]:
        bvid, _part = parse.split_video_id(video_id)
        return parse.parts_from_view(self.client.view(bvid), bvid)
