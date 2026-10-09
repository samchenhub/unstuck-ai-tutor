from collections.abc import Iterator
from pathlib import Path
from typing import Protocol


class LLM(Protocol):
    """Every model call in Unstuck goes through this interface."""

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str: ...

    def chat_stream(self, messages: list[dict]) -> Iterator[str]: ...

    def vision(self, prompt: str, image_path: Path) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
