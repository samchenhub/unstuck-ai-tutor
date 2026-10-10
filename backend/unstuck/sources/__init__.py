from unstuck.config import Settings
from unstuck.models import Video
from unstuck.sources import youtube
from unstuck.sources.base import Source, VideoRef


def resolve(url: str, settings: Settings) -> VideoRef:
    """Turn a pasted link into a VideoRef; raises IngestError (INVALID_URL, …)."""
    return youtube.parse(url)


def for_name(name: str, settings: Settings) -> Source:
    if name == youtube.NAME:
        return youtube.YouTubeSource()
    raise ValueError(f"unknown video source {name!r}")


def for_video(video: Video, settings: Settings) -> Source:
    return for_name(video.source, settings)
