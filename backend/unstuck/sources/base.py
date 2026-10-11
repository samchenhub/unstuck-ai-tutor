import math
import threading
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


class AudioFeed:
    """Audio that may still be downloading while it is transcribed.

    The producer (a download thread) reports the contiguous prefix on disk and finishes or fails the feed;
    the transcriber waits until the seconds it needs are readable. Seconds are estimated from the byte prefix
    (audio is close to constant bitrate), with a safety margin for the container header and fragment edges.
    """

    SAFETY_BYTES = 256 * 1024  # ~30 s of 64 kbps audio
    RATE_MARGIN = 0.97

    def __init__(self, path: Path, duration: float):
        self._dest = path
        self._duration = duration
        self._cond = threading.Condition()
        self._prefix = 0
        self._total: int | None = None
        self._done = False
        self._error: BaseException | None = None

    @classmethod
    def complete(cls, path: Path) -> "AudioFeed":
        feed = cls(path, 0.0)
        feed._done = True
        return feed

    # -- producer ---------------------------------------------------------
    def progress(self, prefix: int, total: int) -> None:
        with self._cond:
            self._prefix = max(self._prefix, prefix)
            self._total = total
            self._cond.notify_all()

    def reset(self) -> None:
        """The download is starting over (e.g. falling back to another file): nothing is readable yet."""
        with self._cond:
            self._prefix, self._total = 0, None

    def finish(self, path: Path | None = None) -> None:
        with self._cond:
            if path is not None:
                self._dest = path
            self._done = True
            self._cond.notify_all()

    def fail(self, error: BaseException) -> None:
        with self._cond:
            self._error = error
            self._cond.notify_all()

    # -- consumer ---------------------------------------------------------
    @property
    def is_complete(self) -> bool:
        return self._done

    def path(self) -> Path:
        """The file to read now: the finished file, or the partial download beside it."""
        if self._done or self._dest.exists():
            return self._dest
        return self._dest.with_suffix(self._dest.suffix + ".part")

    def readable_seconds(self) -> float:
        with self._cond:
            return self._readable()

    def _readable(self) -> float:
        if self._done:
            return math.inf
        if not self._total or self._duration <= 0:
            return 0.0
        fraction = max(0, self._prefix - self.SAFETY_BYTES) / self._total
        return fraction * self._duration * self.RATE_MARGIN

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def wait_for(self, seconds: float) -> None:
        """Block until audio up to `seconds` is readable (or the download finished); raises its error."""
        with self._cond:
            while not self._done and self._error is None and self._readable() < seconds:
                self._cond.wait(timeout=30)
            self._raise_if_failed()

    def wait_for_more(self) -> None:
        """Block until more audio arrives (or the download finished); raises its error."""
        with self._cond:
            start = self._prefix
            while not self._done and self._error is None and self._prefix == start:
                self._cond.wait(timeout=30)
            self._raise_if_failed()


class Source(Protocol):
    name: str

    def fetch_info(self, video_id: str) -> dict: ...

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta: ...

    def fetch_captions(self, info: dict) -> list[Cue] | None: ...

    def download_video(self, video_id: str, out_dir: Path) -> Path: ...

    def download_audio(self, video_id: str, out_dir: Path) -> Path: ...

    def open_audio(self, video_id: str, out_dir: Path, duration: float) -> AudioFeed: ...

    def playback(self, video_id: str, quality: int = 720) -> Playback: ...

    def parts(self, video_id: str) -> list[Part]: ...
