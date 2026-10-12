import logging
import sqlite3
import time
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

# Short on purpose: the answer waits for this, and generation time grows with its length (329 → 77 output
# tokens took 2.7 → 1.1 s on deepseek-flash).
LIVE_FRAME_PROMPT = """This is the frame a learner is looking at in an educational video at the moment they paused to ask a question.
Be concise, at most about 150 words: copy formulas exactly in LaTeX and code verbatim, and summarize the rest of the screen (text, diagrams) briefly.
Focus on what relates to the learner's question: {question}"""

STREAM_URL_MARGIN_S = 600.0  # reuse a stream URL until 10 minutes before it expires
_stream_urls: dict[tuple[str, str], tuple[str, float]] = {}


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
        try:
            media.extract_frame(frame_input, float(second), image, headers=headers)
        except Exception:
            _stream_urls.pop((str(settings.db_path), video_id), None)  # perhaps expired: look it up next time
            raise
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
    key = (str(settings.db_path), video_id)
    cached = _stream_urls.get(key)
    if cached is not None and cached[1] - time.time() > STREAM_URL_MARGIN_S:
        return cached[0], src.stream_headers  # asking Bilibili for a URL takes ~2 s; it stays valid ~2 h
    try:
        playback = src.playback(video_id)
    except Exception:
        log.warning("no stream URL for live frame of %s", video_id, exc_info=True)
        return None, None
    if playback.url and playback.expires_at:
        _stream_urls[key] = (playback.url, playback.expires_at)
    return playback.url or None, src.stream_headers
