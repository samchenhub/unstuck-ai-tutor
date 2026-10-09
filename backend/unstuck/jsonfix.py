import json
import re

from json_repair import repair_json

# A fence that starts the text or a line; used only when strict decoding fails.
_FENCE_RE = re.compile(r"(?:^|\n)```(?:json)?\s*\n?(.*?)(?:\n```|$)", re.S)
# LaTeX commands whose first letter collides with a JSON escape (b f n r t u).
_LATEX_BACKSLASH_RE = re.compile(
    r"(?<!\\)\\(?=(?:beta|bar|bf|binom|bigl|bigr|boldsymbol|bot|frac|forall|flat|nabla|neq|ne|nu|neg|not"
    r"|rho|right|rightarrow|rangle|rceil|rfloor|rm|theta|tau|times|tan|tanh|text|textbf|tilde|to|top"
    r"|triangle|underline|uparrow|upsilon)(?![A-Za-z]))"
)


def _candidate_starts(text: str) -> list[int]:
    return sorted(i for i in (text.find("{"), text.find("[")) if i != -1)


def _decode(text: str) -> dict | list | None:
    """Strict-decode a JSON object/list from the earliest candidate start that yields one.

    A list of plain scalars (e.g. a "[1]" citation in chatter) only wins when no
    object or structured list decodes later in the text."""
    fallback = None
    for start in _candidate_starts(text):
        try:
            obj, _end = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) or (isinstance(obj, list) and any(isinstance(x, (dict, list)) for x in obj)):
            return obj
        if isinstance(obj, list) and fallback is None:
            fallback = obj
    return fallback


def _repair(text: str) -> dict | list | None:
    starts = _candidate_starts(text)
    if not starts:
        return None
    repaired = repair_json(text[starts[0]:], return_objects=True)
    if isinstance(repaired, (dict, list)) and repaired:
        return repaired
    return None


def loads_lenient(raw: str) -> dict | list | None:
    """Parse JSON from model output: tolerate chatter, LaTeX backslashes, code fences and truncation."""
    text = _LATEX_BACKSLASH_RE.sub(r"\\\\", raw.strip())
    obj = _decode(text)
    if obj is not None:
        return obj
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
        obj = _decode(text)
        if obj is not None:
            return obj
    return _repair(text)
