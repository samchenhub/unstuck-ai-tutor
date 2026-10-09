import logging
import time
from collections.abc import Callable
from pathlib import Path

from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.llm.retry import with_retry
from unstuck.models import FrameDescription, Keyframe

log = logging.getLogger(__name__)

KINDS = frozenset({"formula", "code", "diagram", "slide", "talking_head", "other"})
_FIELDS = ("kind", "summary", "ocr_text", "change_note")

VISION_PROMPT = """You are indexing one frame of an educational video so a tutor can answer questions about it later.
Return ONLY a JSON object with exactly these keys:
- "kind": one of "formula", "code", "diagram", "slide", "talking_head", "other"
- "summary": 1-3 sentences on what the frame shows and teaches
- "ocr_text": all readable on-screen text; write formulas in LaTeX and keep code exactly as shown; "" if none
- "change_note": what changed compared with the previous frame; "" if nothing notable

Previous frame summary: {previous}"""


def build_vision_prompt(previous_summary: str) -> str:
    return VISION_PROMPT.format(previous=previous_summary or "(none, this is the first frame)")


def parse_vision_output(raw: str) -> FrameDescription:
    obj = loads_lenient(raw)
    if not isinstance(obj, dict) or not any(key in obj for key in _FIELDS):
        return FrameDescription(kind="other", summary=raw.strip()[:2000], ocr_text="", change_note="")
    kind = str(obj.get("kind", "other")).strip().lower()
    return FrameDescription(
        kind=kind if kind in KINDS else "other",
        summary=str(obj.get("summary", "")).strip(),
        ocr_text=str(obj.get("ocr_text", "")).strip(),
        change_note=str(obj.get("change_note", "")).strip(),
    )


def describe_pending(
    llm: LLM,
    frames: list[Keyframe],
    save: Callable[[int, FrameDescription | None], None],
    *,
    attempts: int = 2,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Describe every frame that is not yet 'ok', in time order, passing the previous
    frame's summary for context. A frame that keeps failing is saved as None
    (status 'failed') and the loop moves on. Returns the number of failed frames."""
    sleep = sleep or time.sleep
    previous = ""
    failed = 0
    for frame in sorted(frames, key=lambda f: f.t):
        if frame.status == "ok":
            previous = frame.summary
            continue
        prompt = build_vision_prompt(previous)
        try:
            raw = with_retry(
                lambda: llm.vision(prompt, Path(frame.image_path)),
                attempts=attempts, base_delay=base_delay, sleep=sleep,
            )
        except Exception:
            log.warning("vision failed for keyframe %s at %.1fs", frame.id, frame.t, exc_info=True)
            save(frame.id, None)
            failed += 1
            continue
        description = parse_vision_output(raw)
        save(frame.id, description)
        previous = description.summary
    return failed
