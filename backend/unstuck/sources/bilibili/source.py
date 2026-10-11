import functools
import logging
import math
import threading
import time
from pathlib import Path

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import AudioFeed, Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse
from unstuck.sources.bilibili.client import HEADERS, BiliClient
from unstuck.sources.bilibili.download import download_ranges

log = logging.getLogger(__name__)

NAME = "bilibili"


# Background audio downloads by destination, so a retried transcript stage joins the one still running.
_active: dict[Path, AudioFeed] = {}
_active_lock = threading.Lock()


def _settle(dest: Path, feed: AudioFeed, *, path: Path | None = None, error: BaseException | None = None) -> None:
    with _active_lock:
        _active.pop(dest, None)
    if error is not None:
        feed.fail(error)
    else:
        feed.finish(path)


@functools.lru_cache(maxsize=4)
def _client(cookies_browser: str) -> BiliClient:
    return BiliClient(cookies_browser)


def client_for(settings: Settings) -> BiliClient:
    return _client(settings.bilibili_cookies_browser)


class BilibiliSource:
    name = NAME
    frames_from_stream = True
    stream_headers = HEADERS  # the CDN answers 403 to ffmpeg's own User-Agent

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
        try:
            (out_dir / "audio.m4a").unlink(missing_ok=True)
        except OSError:  # Windows: still open (e.g. a transcription reading it); harmless to keep
            log.warning("could not remove the transcription audio of %s", video_id)
        return path

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        feed = self.open_audio(video_id, out_dir, 0.0)
        feed.wait_for(math.inf)
        return feed.path()

    def open_audio(self, video_id: str, out_dir: Path, duration: float) -> AudioFeed:
        """Download only the DASH audio track (about a quarter of the MP4) in the background and hand it to
        transcription while it arrives; the MP4 for keyframes comes later. An existing MP4 or audio file is
        reused; any DASH problem falls back to the MP4 (slower, but proven)."""
        for name in ("video.mp4", "audio.m4a"):
            path = out_dir / name
            if path.is_file() and path.stat().st_size > 0:
                return AudioFeed.complete(path)
        dest = out_dir / "audio.m4a"
        with _active_lock:
            if (active := _active.get(dest)) is not None:
                return active  # a retried stage joins the download that is still running
            feed = _active[dest] = AudioFeed(dest, duration)
        try:
            info = self.fetch_info(video_id)

            def audio_urls() -> list[str]:
                urls = parse.dash_audio_urls(self.client.playurl_dash(info["bvid"], info["cid"]))
                if not urls:
                    raise IngestError("PLAYBACK_UNAVAILABLE", "B站没有返回音频地址")
                return urls

            first = audio_urls()
        except BaseException as exc:
            threading.Thread(target=self._mp4_instead, args=(video_id, out_dir, feed, exc), daemon=True).start()
            return feed

        def run() -> None:
            urls = iter([first])  # the downloader asks for URLs up front and again after repeated failures
            try:
                download_ranges(lambda: next(urls, None) or audio_urls(), dest, headers=HEADERS,
                                sleep=self._sleep, on_progress=feed.progress)
            except Exception as exc:
                self._mp4_instead(video_id, out_dir, feed, exc)
                return
            _settle(dest, feed)

        threading.Thread(target=run, name=f"audio-{video_id}", daemon=True).start()
        return feed

    def _mp4_instead(self, video_id: str, out_dir: Path, feed: AudioFeed, exc: BaseException) -> None:
        log.warning("DASH audio failed for %s (%s), downloading the MP4 instead", video_id,
                    getattr(exc, "code", type(exc).__name__))
        feed.reset()
        try:
            path = self.download_video(video_id, out_dir)
        except Exception as mp4_exc:
            _settle(out_dir / "audio.m4a", feed, error=mp4_exc)
            return
        _settle(out_dir / "audio.m4a", feed, path=path)

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        info = self.fetch_info(video_id)
        data = self.client.playurl_mp4(info["bvid"], info["cid"])
        return parse.mp4_playback(data, info["duration"], now=self._clock())

    def parts(self, video_id: str) -> list[Part]:
        bvid, _part = parse.split_video_id(video_id)
        return parse.parts_from_view(self.client.view(bvid), bvid)
