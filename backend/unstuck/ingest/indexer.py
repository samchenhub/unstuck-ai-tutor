import sqlite3

from unstuck.db import repo
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
    repo.replace_vectors(conn, video_id, [(s.id, v) for s, v in zip(segments, vectors)])
    return len(segments)


def search(conn: sqlite3.Connection, llm: LLM, video_id: str, query: str, k: int) -> list[int]:
    (vector,) = llm.embed([query])
    return [segment_id for segment_id, _distance in repo.search_vectors(conn, video_id, vector, k)]
