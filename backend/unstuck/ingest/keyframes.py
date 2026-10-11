import logging
import shutil
from collections.abc import Callable
from pathlib import Path

import imagehash
from PIL import Image

from unstuck import media
from unstuck.models import NewKeyframe

log = logging.getLogger(__name__)


SAMPLE_INTERVAL_S = 45.0
MAX_FRAMES = 200
_REFRESH_AFTER_FAILURES = 3


def sample_times(duration: float, interval: float = SAMPLE_INTERVAL_S, max_frames: int = MAX_FRAMES) -> list[float]:
    """Evenly spaced frame times (centred in each interval); long videos get a longer interval, not more frames.

    Even sampling replaced scene detection, which decoded the whole video on the CPU.
    """
    step = max(interval, duration / max_frames)
    if duration < step:
        return [duration / 2]
    return [(i + 0.5) * step for i in range(int(duration // step))]


def dedupe_by_phash(frames: list[NewKeyframe], threshold: int = 5) -> list[NewKeyframe]:
    """Drop frames whose perceptual hash is within threshold bits of the last kept frame."""
    kept: list[NewKeyframe] = []
    for frame in frames:
        if kept:
            distance = imagehash.hex_to_hash(frame.phash) - imagehash.hex_to_hash(kept[-1].phash)
            if distance <= threshold:
                continue
        kept.append(frame)
    return kept


def extract_keyframes(
    video: Path | str | Callable[[], str],
    duration: float,
    out_dir: Path,
    *,
    headers: dict[str, str] | None = None,
) -> list[NewKeyframe]:
    """Grab evenly spaced frames and drop near-duplicates (static slides, talking heads).

    `video` is a local file, or a function returning a fresh online stream URL (asked again after repeated
    failures, e.g. an expired URL); `headers` go with stream requests.
    """
    shutil.rmtree(out_dir, ignore_errors=True)
    refresh = video if callable(video) else None
    source = refresh() if refresh else video
    candidates: list[NewKeyframe] = []
    failures = 0
    for t in sample_times(duration):
        try:
            path = media.extract_frame(source, t, out_dir / f"{int(t * 1000):010d}.jpg", headers=headers)
            with Image.open(path) as img:
                phash = str(imagehash.phash(img))
        except Exception:
            log.warning("skipping keyframe at %.1fs", t, exc_info=True)
            failures += 1
            if refresh and failures % _REFRESH_AFTER_FAILURES == 0:
                try:
                    source = refresh()
                except Exception:
                    log.warning("could not refresh the stream URL for keyframes", exc_info=True)
            continue
        failures = 0
        candidates.append(NewKeyframe(t=t, image_path=str(path), phash=phash))

    kept = dedupe_by_phash(candidates)
    kept_paths = {f.image_path for f in kept}
    for frame in candidates:
        if frame.image_path not in kept_paths:
            Path(frame.image_path).unlink(missing_ok=True)
    return kept
