from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from unstuck.models import Cue


@dataclass(frozen=True)
class VideoRef:
    """What a pasted link resolves to."""

    source: str
    video_id: str
    url: str
    course_id: str | None = None
    part: int | None = None


@dataclass(frozen=True)
class VideoMeta:
    title: str
    channel: str
    duration: float
    part_title: str = ""
    parts_total: int = 1
    thumbnail: str = ""


@dataclass(frozen=True)
class Playback:
    """How the browser plays a video: "youtube" (IFrame player) or "mp4" (a direct, expiring URL)."""

    kind: str
    url: str = ""
    expires_at: float = 0.0
    duration: float = 0.0
    backup_urls: tuple[str, ...] = ()  # mirrors of the same file (Bilibili); the downloader races them


@dataclass(frozen=True)
class Part:
    part: int
    title: str
    duration: float
    video_id: str


class Source(Protocol):
    name: str

    def fetch_info(self, video_id: str) -> dict: ...

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta: ...

    def fetch_captions(self, info: dict) -> list[Cue] | None: ...

    def download_video(self, video_id: str, out_dir: Path) -> Path: ...

    def download_audio(self, video_id: str, out_dir: Path) -> Path: ...

    def playback(self, video_id: str, quality: int = 720) -> Playback: ...

    def parts(self, video_id: str) -> list[Part]: ...
