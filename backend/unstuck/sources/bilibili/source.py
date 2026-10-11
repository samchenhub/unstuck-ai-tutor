import functools
import logging
import time
from pathlib import Path

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse
from unstuck.sources.bilibili.client import HEADERS, BiliClient
from unstuck.sources.bilibili.download import download_ranges

log = logging.getLogger(__name__)

NAME = "bilibili"


@functools.lru_cache(maxsize=4)
def _client(cookies_browser: str) -> BiliClient:
    return BiliClient(cookies_browser)


def client_for(settings: Settings) -> BiliClient:
    return _client(settings.bilibili_cookies_browser)


class BilibiliSource:
    name = NAME

    def __init__(self, client: BiliClient, clock=time.time, sleep=time.sleep):
        self.client = client
        self._clock = clock
        self._sleep = sleep

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

    def download_video(self, video_id: str, out_dir: Path) -> Path:
        """Download the 720p H.264+AAC MP4 (resumable, fresh URL after repeated failures); reused if present."""
        dest = out_dir / "video.mp4"
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        def mirrors() -> list[str]:
            playback = self.playback(video_id)
            return [playback.url, *playback.backup_urls]

        path = download_ranges(mirrors, dest, headers=HEADERS, sleep=self._sleep)
        # Transcription is finished by the time the MP4 is fetched (download stage); drop its audio-only copy.
        (out_dir / "audio.m4a").unlink(missing_ok=True)
        return path

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        """Only the DASH audio track (about a quarter of the MP4), so transcription starts sooner; the MP4 for
        keyframes is downloaded later. An already downloaded MP4 is reused (it carries the AAC track)."""
        video = out_dir / "video.mp4"
        if video.is_file() and video.stat().st_size > 0:
            return video
        dest = out_dir / "audio.m4a"
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        info = self.fetch_info(video_id)

        def audio_urls() -> list[str]:
            urls = parse.dash_audio_urls(self.client.playurl_dash(info["bvid"], info["cid"]))
            if not urls:
                raise IngestError("PLAYBACK_UNAVAILABLE", "B站没有返回音频地址")
            return urls

        try:
            urls = iter([audio_urls()])  # the downloader asks for URLs up front and again after repeated failures
            return download_ranges(lambda: next(urls, None) or audio_urls(), dest, headers=HEADERS, sleep=self._sleep)
        except IngestError as exc:
            # The MP4 route is slower but proven; any DASH problem (none offered, lookup or download failed) uses it.
            log.warning("DASH audio failed for %s (%s), downloading the MP4 instead", video_id, exc.code)
            return self.download_video(video_id, out_dir)

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        info = self.fetch_info(video_id)
        data = self.client.playurl_mp4(info["bvid"], info["cid"])
        return parse.mp4_playback(data, info["duration"], now=self._clock())

    def parts(self, video_id: str) -> list[Part]:
        bvid, _part = parse.split_video_id(video_id)
        return parse.parts_from_view(self.client.view(bvid), bvid)
