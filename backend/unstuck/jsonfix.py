import json
import re

from json_repair import repair_json

# A fence that starts the text or a line; used only when strict decoding fails.
_FENCE_RE = re.compile(r"(?:^|\n)```(?:json)?\s*\n?(.*?)(?:\n```|$)", re.S)
# LaTeX commands whose first letter collides with a JSON escape (b f n r t u).
# Known ambiguity: a real "\n" escape directly followed by a whitelisted word
# (e.g. code "\ne = 1") is read as the LaTeX command \ne.
_LATEX_BACKSLASH_RE = re.compile(
    r"(?<!\\)\\(?=(?:beta|bar|bf|binom|bigl|bigr|boldsymbol|bot|frac|forall|flat|nabla|neq|ne|nu|neg|not"
    r"|rho|right|rightarrow|rangle|rceil|rfloor|rm|theta|tau|times|tan|tanh|text|textbf|tilde|to|top"
    r"|triangle|underline|uparrow|upsilon)(?![A-Za-z]))"
)


def _start(text: str) -> int | None:
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    return min(starts) if starts else None


def _raw_decode(text: str, start: int) -> dict | list | None:
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, (dict, list)) else None


def _decode(text: str) -> dict | list | None:
    """Strict-decode at the earliest '{' / '['. Never falls back to inner fragments.

    Exception: a list of plain scalars (e.g. a "[1]" citation in chatter) yields to
    a later object that decodes strictly."""
    start = _start(text)
    if start is None:
        return None
    obj = _raw_decode(text, start)
    if isinstance(obj, list) and not any(isinstance(x, (dict, list)) for x in obj):
        brace = text.find("{", start)
        if brace != -1:
            inner = _raw_decode(text, brace)
            if isinstance(inner, dict):
                return inner
    return obj


def _repair(text: str) -> dict | list | None:
    start = _start(text)
    if start is None:
        return None
    repaired = repair_json(text[start:], return_objects=True)
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
