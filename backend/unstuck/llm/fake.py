import hashlib
import math
import re
from collections.abc import Iterator
from pathlib import Path

DEFAULT_CHAT = "fake answer"
DEFAULT_VISION = '{"kind": "other", "summary": "fake frame", "ocr_text": "", "change_note": ""}'


def _bag_of_words(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeLLM:
    """Deterministic stand-in for real models in tests.

    Queued responses are consumed in order; an Exception item is raised instead
    of returned. For chat_stream a list item is yielded chunk by chunk, raising
    any Exception element (simulates a stream that dies midway).
    """

    def __init__(self, chat_responses=None, vision_responses=None, embed_dim: int = 64):
        self.chat_responses = list(chat_responses or [])
        self.vision_responses = list(vision_responses or [])
        self.embed_dim = embed_dim
        self.calls: list[tuple[str, object]] = []

    @staticmethod
    def _pop(queue: list, default):
        if not queue:
            return default
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        self.calls.append(("chat", messages))
        return self._pop(self.chat_responses, DEFAULT_CHAT)

    def chat_stream(self, messages: list[dict], *, attempts: int | None = None) -> Iterator[str]:
        self.calls.append(("chat_stream", messages))
        item = self._pop(self.chat_responses, DEFAULT_CHAT)
        chunks = item if isinstance(item, list) else [item[i : i + 8] for i in range(0, len(item), 8)]
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def vision(self, prompt: str, image_path: Path, *, attempts: int | None = None) -> str:
        self.calls.append(("vision", (prompt, str(image_path))))
        return self._pop(self.vision_responses, DEFAULT_VISION)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("embed", list(texts)))
        return [_bag_of_words(t, self.embed_dim) for t in texts]
