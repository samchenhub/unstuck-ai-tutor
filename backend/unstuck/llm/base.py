from collections.abc import Iterator
from pathlib import Path
from typing import Protocol


class ModelBusyError(Exception):
    """The model provider kept rate-limiting us after every retry."""


class LLM(Protocol):
    """Every model call in Unstuck goes through this interface."""

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str: ...

    def chat_stream(self, messages: list[dict], *, attempts: int | None = None) -> Iterator[str]: ...

    def vision(self, prompt: str, image_path: Path, *, attempts: int | None = None) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
