import sqlite3

from unstuck.db import repo
from unstuck.ingest import timeline
from unstuck.llm.base import LLM
from unstuck.models import Keyframe, Segment

EMBED_BATCH = 32


def segment_text(segment: Segment, frames_by_id: dict[int, Keyframe]) -> str:
    """Text that represents a segment in the vector index: transcript + what was on screen."""
    parts = [segment.transcript]
    for frame_id in segment.keyframe_ids:
        frame = frames_by_id.get(frame_id)
        if frame and frame.status == "ok":
            parts.append(frame.summary)
            parts.append(frame.ocr_text)
    return "\n".join(p for p in parts if p)


def build_index(conn: sqlite3.Connection, llm: LLM, video_id: str) -> int:
    segments = repo.get_segments(conn, video_id)
    frames_by_id = {f.id: f for f in repo.get_keyframes(conn, video_id)}
    texts = [segment_text(s, frames_by_id) or " " for s in segments]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(llm.embed(texts[i : i + EMBED_BATCH]))
    repo.replace_vectors(conn, video_id, [(s.id, v) for s, v in zip(segments, vectors, strict=True)])
    return len(segments)


def publish_index(
    conn: sqlite3.Connection, llm: LLM, video_id: str, segments: list[Segment], cache: dict[str, list[float]]
) -> None:
    """Embed `segments` (reusing `cache` for text embedded before) and swap them in atomically.

    Used while a transcript grows: earlier segments keep their text, so only the new ones are embedded.
    """
    frames_by_id = {f.id: f for f in repo.get_keyframes(conn, video_id)}
    texts = [segment_text(s, frames_by_id) or " " for s in segments]
    missing = list(dict.fromkeys(t for t in texts if t not in cache))
    for i in range(0, len(missing), EMBED_BATCH):
        batch = missing[i : i + EMBED_BATCH]
        cache.update(zip(batch, llm.embed(batch), strict=True))
    repo.replace_index(conn, video_id, segments, [cache[t] for t in texts])


def refresh_index(conn: sqlite3.Connection, llm: LLM, video_id: str, cache: dict[str, list[float]]) -> None:
    """Rebuild a video's segments from its stored transcript and keyframes, and swap the index in atomically."""
    video = repo.get_video(conn, video_id)
    segments = timeline.build_segments(
        repo.get_cues(conn, video_id), repo.get_keyframes(conn, video_id), video.duration
    )
    publish_index(conn, llm, video_id, segments, cache)


def search(conn: sqlite3.Connection, llm: LLM, video_id: str, query: str, k: int) -> list[int]:
    (vector,) = llm.embed([query])
    return [segment_id for segment_id, _distance in repo.search_vectors(conn, video_id, vector, k)]
