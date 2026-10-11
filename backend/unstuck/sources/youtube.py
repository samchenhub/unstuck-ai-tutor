from pathlib import Path

from unstuck.ingest import youtube as yt
from unstuck.models import Cue
from unstuck.sources.base import AudioFeed, Part, Playback, VideoMeta, VideoRef

NAME = "youtube"


def parse(url: str) -> VideoRef:
    video_id = yt.parse_video_id(url)
    return VideoRef(source=NAME, video_id=video_id, url=yt.watch_url(video_id))


class YouTubeSource:
    """Adapter over unstuck.ingest.youtube; looked up at call time so tests can monkeypatch it."""

    name = NAME
    frames_from_stream = False  # googlevideo URLs need yt-dlp; frames come from the downloaded file
    stream_headers: dict[str, str] = {}

    def fetch_info(self, video_id: str) -> dict:
        return yt.fetch_info(video_id)

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta:
        meta = yt.check_video_info(info, max_duration_s)
        return VideoMeta(title=meta.title, channel=meta.channel, duration=meta.duration)

    def fetch_captions(self, info: dict) -> list[Cue] | None:
        return yt.fetch_captions(info)

    def download_video(self, video_id: str, out_dir: Path) -> Path:
        return yt.download_video(video_id, out_dir)

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        return yt.download_audio(video_id, out_dir)

    def open_audio(self, video_id: str, out_dir: Path, duration: float) -> AudioFeed:
        # yt-dlp downloads the (small) audio-only stream quickly; transcription starts once it is done.
        return AudioFeed.complete(self.download_audio(video_id, out_dir))

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        return Playback(kind="youtube")

    def parts(self, video_id: str) -> list[Part]:
        return [Part(part=1, title="", duration=0.0, video_id=video_id)]
