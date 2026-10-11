import logging
import sqlite3
from pathlib import Path

import imagehash
from PIL import Image

from unstuck import media, sources
from unstuck.config import Settings
from unstuck.db import repo
from unstuck.ingest import files
from unstuck.llm.base import LLM
from unstuck.models import FrameDescription, NewKeyframe

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
    frame_input, headers = _frame_input(conn, settings, video_id, video_dir)
    if frame_input is None:
        return None
    image = video_dir / "live" / f"{second}.jpg"
    try:
        media.extract_frame(frame_input, float(second), image, headers=headers)
        description = llm.vision(LIVE_FRAME_PROMPT.format(question=question), image, attempts=2).strip()
    except Exception:
        log.warning("live frame failed for %s at %ss", video_id, second, exc_info=True)
        return None
    if not description:
        return None
    repo.put_frame_cache(conn, video_id, second, description)
    _keep_as_keyframe(conn, video_id, second, image, description)
    return description


def _keep_as_keyframe(conn: sqlite3.Connection, video_id: str, second: int, image: Path, description: str) -> None:
    """Later questions near this moment, and retrieval, reuse what was seen instead of calling vision again."""
    try:
        with Image.open(image) as img:
            phash = str(imagehash.phash(img))
        repo.add_described_keyframe(
            conn, video_id, NewKeyframe(float(second), str(image), phash),
            FrameDescription(kind=repo.LIVE_KIND, summary=description, ocr_text="", change_note=""),
        )
    except Exception:
        log.warning("could not keep the live frame of %s at %ss", video_id, second, exc_info=True)


def _frame_input(
    conn: sqlite3.Connection, settings: Settings, video_id: str, video_dir: Path
) -> tuple[Path | str | None, dict[str, str] | None]:
    """The downloaded file if there is one; otherwise the online stream (with its headers) where allowed."""
    local = files.find_video_file(video_dir)
    if local is not None:
        return local, None
    video = repo.get_video(conn, video_id)
    if video is None:
        return None, None
    src = sources.for_video(video, settings)
    if not src.frames_from_stream:
        return None, None
    try:
        return src.playback(video_id).url or None, src.stream_headers
    except Exception:
        log.warning("no stream URL for live frame of %s", video_id, exc_info=True)
        return None, None
