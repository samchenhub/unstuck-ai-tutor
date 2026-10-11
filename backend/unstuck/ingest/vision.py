from pathlib import Path

from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.models import FrameDescription, Keyframe

KINDS = frozenset({"formula", "code", "diagram", "slide", "talking_head", "other"})
_FIELDS = ("kind", "summary", "ocr_text", "change_note")

VISION_PROMPT = """You are indexing one frame of an educational video so a tutor can answer questions about it later.
Return ONLY a JSON object with exactly these keys:
- "kind": one of "formula", "code", "diagram", "slide", "talking_head", "other"
- "summary": 1-3 sentences on what the frame shows and teaches
- "ocr_text": all readable on-screen text; write formulas in LaTeX and keep code exactly as shown; "" if none
- "change_note": what changed compared with the previous frame; "" if nothing notable

Your reply must be valid JSON: escape every backslash inside strings (for example "\\\\frac" for \\frac).

Previous frame summary: {previous}"""


def build_vision_prompt(previous_summary: str) -> str:
    return VISION_PROMPT.format(previous=previous_summary or "(none, this is the first frame)")


def _text_field(obj: dict, key: str) -> str:
    value = obj.get(key)
    return "" if value is None else str(value)


def parse_vision_output(raw: str) -> FrameDescription:
    obj = loads_lenient(raw)
    if not isinstance(obj, dict) or not any(key in obj for key in _FIELDS):
        return FrameDescription(kind="other", summary=raw.strip()[:2000], ocr_text="", change_note="")
    kind = _text_field(obj, "kind").strip().lower()
    return FrameDescription(
        kind=kind if kind in KINDS else "other",
        summary=_text_field(obj, "summary").strip(),
        ocr_text=_text_field(obj, "ocr_text").strip("\n").rstrip(),
        change_note=_text_field(obj, "change_note").strip(),
    )


def describe_frame(llm: LLM, frame: Keyframe, previous_summary: str, *, attempts: int = 1) -> FrameDescription:
    """Describe one keyframe, given the previous frame's summary for context.

    Raises on any failure (ModelBusyError when rate-limited) and leaves waiting to the caller: the background
    job backs off for minutes, a question just moves on. `attempts` is passed to the model client, whose
    default retries would otherwise hold a rate-limited call for about a minute.
    """
    raw = llm.vision(build_vision_prompt(previous_summary), Path(frame.image_path), attempts=attempts)
    if not raw or not raw.strip():
        raise ValueError("empty vision reply")
    return parse_vision_output(raw)
