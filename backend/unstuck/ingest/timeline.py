import bisect

from unstuck.ingest.captions import sentence_end_flags
from unstuck.models import Cue, Keyframe, Segment

EMPTY_WINDOW_S = 45.0


def _near_cut(t: float, cut_times: list[float], tolerance: float) -> bool:
    i = bisect.bisect_left(cut_times, t - tolerance)
    return i < len(cut_times) and cut_times[i] <= t + tolerance


def _group_cues(
    cues: list[Cue], cut_times: list[float], *, min_len: float, max_len: float,
    cut_eligible: float, cut_tolerance: float, min_tail: float,
) -> list[list[int]]:
    flags = sentence_end_flags(cues)
    groups: list[list[int]] = []
    current: list[int] = []
    for i, cue in enumerate(cues):
        if current and cue.end - cues[current[0]].start > max_len:
            groups.append(current)
            current = []
        current.append(i)
        length = cue.end - cues[current[0]].start
        if (length >= min_len and flags[i]) or (
            length >= cut_eligible and _near_cut(cue.end, cut_times, cut_tolerance)
        ):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    if len(groups) > 1:
        tail = groups[-1]
        if cues[tail[-1]].end - cues[tail[0]].start < min_tail:
            groups[-2].extend(groups.pop())
    return groups


def _attach_keyframes(spans: list[tuple[float, float, str]], keyframes: list[Keyframe]) -> list[Segment]:
    starts = [start for start, _end, _text in spans]
    ids: list[list[int]] = [[] for _ in spans]
    for frame in sorted(keyframes, key=lambda f: f.t):
        index = max(0, bisect.bisect_right(starts, frame.t) - 1)
        ids[index].append(frame.id)
    return [
        Segment(start=start, end=end, transcript=text, keyframe_ids=frame_ids)
        for (start, end, text), frame_ids in zip(spans, ids)
    ]


def build_segments(
    cues: list[Cue],
    keyframes: list[Keyframe],
    duration: float,
    *,
    min_len: float = 30.0,
    max_len: float = 60.0,
    cut_eligible: float = 45.0,
    cut_tolerance: float = 1.5,
    min_tail: float = 10.0,
) -> list[Segment]:
    """Cut the transcript into 30–60 s segments, preferring sentence ends, then scene changes."""
    if not cues:
        spans = []
        start = 0.0
        while start < duration:
            spans.append((start, min(start + EMPTY_WINDOW_S, duration), ""))
            start += EMPTY_WINDOW_S
        return _attach_keyframes(spans, keyframes)

    cut_times = sorted(f.t for f in keyframes)
    groups = _group_cues(
        cues, cut_times, min_len=min_len, max_len=max_len, cut_eligible=cut_eligible,
        cut_tolerance=cut_tolerance, min_tail=min_tail,
    )
    spans = [
        (cues[g[0]].start, cues[g[-1]].end, " ".join(cues[i].text for i in g))
        for g in groups
    ]
    return _attach_keyframes(spans, keyframes)
