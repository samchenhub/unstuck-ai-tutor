import logging
import math
import shutil
from pathlib import Path

import imagehash
from PIL import Image

from unstuck import media
from unstuck.models import NewKeyframe, Scene

log = logging.getLogger(__name__)


def _merge_short(scenes: list[Scene], min_len: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for scene in scenes:
        if merged and (scene.end - scene.start < min_len or merged[-1][1] - merged[-1][0] < min_len):
            merged[-1][1] = scene.end
        else:
            merged.append([scene.start, scene.end])
    return merged


def plan_sample_times(
    scenes: list[Scene],
    duration: float,
    *,
    min_len: float = 5.0,
    max_len: float = 45.0,
    avg_interval: float = 10.0,
    max_frames: int = 300,
) -> list[float]:
    """Choose the timestamps to grab keyframes at.

    Scenes shorter than min_len are merged into a neighbour, scenes longer than
    max_len are split evenly, one frame is sampled ~1 s into each piece, and the
    result is thinned evenly to at most one frame per avg_interval seconds
    (and never more than max_frames).
    """
    spans = _merge_short(scenes, min_len) if scenes else [[0.0, duration]]
    pieces: list[tuple[float, float]] = []
    for start, end in spans:
        n = max(1, math.ceil((end - start) / max_len))
        step = (end - start) / n
        pieces.extend((start + i * step, start + (i + 1) * step) for i in range(n))
    times = [start + min(1.0, (end - start) / 2) for start, end in pieces]

    cap = min(max_frames, max(1, int(duration // avg_interval)))
    if len(times) > cap:
        times = [times[int(i * len(times) / cap)] for i in range(cap)]
    return times


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


def detect_scenes(video_path: Path) -> list[Scene]:
    from scenedetect import ContentDetector, detect

    return [Scene(start.get_seconds(), end.get_seconds()) for start, end in detect(str(video_path), ContentDetector())]


def extract_keyframes(video_path: Path, duration: float, out_dir: Path) -> list[NewKeyframe]:
    shutil.rmtree(out_dir, ignore_errors=True)
    times = plan_sample_times(detect_scenes(video_path), duration)
    candidates: list[NewKeyframe] = []
    for t in times:
        try:
            path = media.extract_frame(video_path, t, out_dir / f"{int(t * 1000):010d}.jpg")
            with Image.open(path) as img:
                phash = str(imagehash.phash(img))
        except Exception:
            log.warning("skipping keyframe at %.1fs", t, exc_info=True)
            continue
        candidates.append(NewKeyframe(t=t, image_path=str(path), phash=phash))

    kept = dedupe_by_phash(candidates)
    kept_paths = {f.image_path for f in kept}
    for frame in candidates:
        if frame.image_path not in kept_paths:
            Path(frame.image_path).unlink(missing_ok=True)
    return kept
