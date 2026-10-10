import logging
import sqlite3

from unstuck import media
from unstuck.config import Settings
from unstuck.db import repo
from unstuck.ingest import youtube
from unstuck.llm.base import LLM

log = logging.getLogger(__name__)

LIVE_FRAME_PROMPT = """This is the frame a learner is looking at in an educational video at the moment they paused to ask a question.
Describe everything on screen that could matter for teaching: transcribe all text, write formulas in LaTeX, copy code exactly, and explain any diagram.
Pay special attention to anything related to the learner's question: {question}"""


def live_frame_description(
    conn: sqlite3.Connection, llm: LLM, settings: Settings, video_id: str, t: float, question: str
) -> str | None:
    second = int(t)
    cached = repo.get_frame_cache(conn, video_id, second)
    if cached is not None:
        return cached

    video_dir = settings.video_dir(video_id)
    video_path = youtube.find_video_file(video_dir)
    if video_path is None:
        return None
    image = video_dir / "live" / f"{second}.jpg"
    try:
        media.extract_frame(video_path, float(second), image)
        description = llm.vision(LIVE_FRAME_PROMPT.format(question=question), image, attempts=2).strip()
    except Exception:
        log.warning("live frame failed for %s at %ss", video_id, second, exc_info=True)
        return None
    if not description:
        return None
    repo.put_frame_cache(conn, video_id, second, description)
    return description
