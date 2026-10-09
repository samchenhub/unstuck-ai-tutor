import re

from unstuck.models import Cue

# Auto captions carry per-word offsets but no word durations; assume the last word lasts this long.
WORD_TAIL_S = 0.3
SENTENCE_END_RE = re.compile(r"[.!?。！？…][\"'”’)\]]*$")


def parse_json3(data: dict) -> list[Cue]:
    """Convert a YouTube json3 caption track into cues with non-overlapping times."""
    raw: list[list] = []
    for event in data.get("events", []):
        segs = event.get("segs") or []
        text = re.sub(r"\s+", " ", "".join(s.get("utf8", "") for s in segs)).strip()
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000
        end = start + event.get("dDurationMs", 0) / 1000
        offsets = [s["tOffsetMs"] for s in segs if "tOffsetMs" in s]
        if offsets:
            end = min(end, start + max(offsets) / 1000 + WORD_TAIL_S)
        raw.append([start, end, text])

    for current, nxt in zip(raw, raw[1:]):
        if current[1] > nxt[0]:
            current[1] = nxt[0]
    return [Cue(start, max(end, start), text) for start, end, text in raw]


def sentence_end_flags(cues: list[Cue], pause: float = 0.8) -> list[bool]:
    """A cue ends a sentence if it ends with punctuation or is followed by a pause.

    Auto captions have no punctuation, so the pause rule carries them.
    """
    flags = []
    for i, cue in enumerate(cues):
        if i == len(cues) - 1:
            flags.append(True)
            continue
        gap = cues[i + 1].start - cue.end
        flags.append(bool(SENTENCE_END_RE.search(cue.text)) or gap >= pause)
    return flags
