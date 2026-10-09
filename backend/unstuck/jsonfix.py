import json
import re

from json_repair import repair_json

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)(?:```|$)", re.S)


def loads_lenient(raw: str) -> dict | list | None:
    """Parse JSON from model output: strip code fences and chatter, repair truncation."""
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    text = text[min(starts):]
    try:
        obj, _end = json.JSONDecoder().raw_decode(text)
        return obj
    except json.JSONDecodeError:
        pass
    repaired = repair_json(text, return_objects=True)
    if isinstance(repaired, (dict, list)) and repaired:
        return repaired
    return None
