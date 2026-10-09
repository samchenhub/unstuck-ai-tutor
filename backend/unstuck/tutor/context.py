import math
import re
from dataclasses import dataclass

from unstuck.ingest.outline import current_chapter
from unstuck.models import Chat, Keyframe, Segment
from unstuck.timefmt import fmt_ts

WINDOW_BEFORE_S = 90.0
WINDOW_AFTER_S = 15.0
LIVE_FRAME_GAP_S = 15.0
RETRIEVE_K = 3
HISTORY_MESSAGES = 12  # 6 rounds of question + answer
TOKEN_BUDGET = 6000

_DATA_TAG_RE = re.compile(r"<\s*/?\s*video_data\s*>", re.I)


@dataclass(frozen=True)
class FrameContext:
    t: float
    text: str
    live: bool


@dataclass(frozen=True)
class ContextInput:
    t: float
    outline: list[dict] | None
    window: list[Segment]
    window_keyframes: list[Keyframe]
    frame: FrameContext | None
    retrieved: list[Segment]
    history: list[Chat]
    visual_available: bool


@dataclass(frozen=True)
class BuiltContext:
    video_data: str
    history: list[Chat]
    retrieved: list[Segment]


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯"


def estimate_tokens(text: str) -> int:
    """Rough token count: one per CJK character, one per four other characters."""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    return cjk + math.ceil((len(text) - cjk) / 4)


def window_segments(segments: list[Segment], t: float) -> list[Segment]:
    return [s for s in segments if s.end > t - WINDOW_BEFORE_S and s.start < t + WINDOW_AFTER_S]


def keyframes_between(keyframes: list[Keyframe], start: float, end: float) -> list[Keyframe]:
    return [k for k in keyframes if k.status == "ok" and start <= k.t < end]


def pick_keyframe(keyframes: list[Keyframe], t: float) -> Keyframe | None:
    candidates = [k for k in keyframes if k.status == "ok" and k.t <= t]
    return max(candidates, key=lambda k: k.t, default=None)


def needs_live_frame(keyframe: Keyframe | None, t: float) -> bool:
    return keyframe is None or t - keyframe.t > LIVE_FRAME_GAP_S


def select_retrieved(
    ranked_ids: list[int], segments: list[Segment], window: list[Segment], limit: int = RETRIEVE_K
) -> list[Segment]:
    by_id = {s.id: s for s in segments}
    excluded = {s.id for s in window}
    picked: list[Segment] = []
    for segment_id in ranked_ids:
        if segment_id in excluded or segment_id not in by_id:
            continue
        excluded.add(segment_id)
        picked.append(by_id[segment_id])
        if len(picked) == limit:
            break
    return picked


def describe_keyframe(keyframe: Keyframe) -> str:
    text = f"[{fmt_ts(keyframe.t)}] ({keyframe.kind}) {keyframe.summary}"
    if keyframe.ocr_text:
        text += f"\n  On-screen text: {keyframe.ocr_text}"
    return text


def _outline_block(outline: list[dict] | None, t: float) -> str:
    if not outline:
        return ""
    current = current_chapter(outline, t)
    lines = ["## Video outline"]
    for i, chapter in enumerate(outline):
        summary = f": {chapter['summary']}" if chapter.get("summary") else ""
        marker = "  <-- the learner is here" if i == current else ""
        lines.append(f"- [{fmt_ts(chapter['start'])}] {chapter['title']}{summary}{marker}")
    return "\n".join(lines)


def _window_block(window: list[Segment], frames: list[Keyframe], t: float) -> str:
    lines = [f"## Transcript around the pause point (paused at [{fmt_ts(t)}])"]
    if not window:
        lines.append("(no transcript here)")
    lines.extend(f"[{fmt_ts(s.start)}-{fmt_ts(s.end)}] {s.transcript}" for s in window)
    if frames:
        lines.append("")
        lines.append("On screen during this stretch:")
        lines.extend(f"- {describe_keyframe(k)}" for k in frames)
    return "\n".join(lines)


def _frame_block(frame: FrameContext | None, visual_available: bool) -> str:
    if not visual_available:
        return "## Current screen\nNot available (this video was imported in transcript-only mode)."
    if frame is None:
        return "## Current screen\nNo description available."
    source = "captured live at the pause point" if frame.live else f"nearest keyframe, [{fmt_ts(frame.t)}]"
    return f"## Current screen ({source})\n{frame.text}"


def _retrieved_block(retrieved: list[Segment], t: float) -> str:
    if not retrieved:
        return ""
    lines = ["## Other relevant parts of the video"]
    for s in retrieved:
        later = " (covered later in the video)" if s.start > t else ""
        lines.append(f"[{fmt_ts(s.start)}-{fmt_ts(s.end)}]{later} {s.transcript}")
    return "\n".join(lines)


def build_context(inp: ContextInput, budget: int = TOKEN_BUDGET) -> BuiltContext:
    frames = inp.window_keyframes if inp.visual_available else []
    fixed = "\n\n".join(
        b for b in (
            _outline_block(inp.outline, inp.t),
            _window_block(inp.window, frames, inp.t),
            _frame_block(inp.frame, inp.visual_available),
        ) if b
    )
    retrieved = list(inp.retrieved)
    history = list(inp.history)

    def total() -> int:
        return (
            estimate_tokens(fixed)
            + estimate_tokens(_retrieved_block(retrieved, inp.t))
            + sum(estimate_tokens(c.content) for c in history)
        )

    while total() > budget and retrieved:
        retrieved.pop()
    while total() > budget and history:
        history.pop(0)

    body = "\n\n".join(b for b in (fixed, _retrieved_block(retrieved, inp.t)) if b)
    body = _DATA_TAG_RE.sub("[tag removed]", body)
    return BuiltContext(video_data=f"<video_data>\n{body}\n</video_data>", history=history, retrieved=retrieved)
