import base64
import mimetypes
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

import openai
from openai import OpenAI

from unstuck.llm.base import ModelBusyError
from unstuck.llm.embedder import BgeM3Embedder
from unstuck.llm.retry import with_retry

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)

MODEL_BUSY_MESSAGE = "模型当前访问量过大（免费额度限流），请稍后重试"

T = TypeVar("T")


class OpenAICompatLLM:
    """Chat + vision over any OpenAI-compatible endpoint (Zhipu by default); embeddings stay local."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        vision_model: str,
        embedder: BgeM3Embedder,
        extra_body: dict | None = None,
        max_concurrency: int = 2,
        retry_attempts: int = 6,
        retry_base_delay: float = 2.0,
    ):
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=120)
        self._chat_model = chat_model
        self._vision_model = vision_model
        self._embedder = embedder
        self._extra_body = extra_body
        self._slots = threading.Semaphore(max_concurrency)
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def _with_retry(self, fn: Callable[[], T]) -> T:
        # Free tiers rate-limit in bursts, so the default waits 2+4+8+16+32 s before giving up.
        try:
            return with_retry(
                fn, attempts=self._retry_attempts, base_delay=self._retry_base_delay, retry_on=RETRYABLE
            )
        except openai.RateLimitError as exc:
            raise ModelBusyError(MODEL_BUSY_MESSAGE) from exc

    def _complete(self, model: str, messages: list[dict], **kwargs) -> str:
        def call() -> str:
            with self._slots:
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, extra_body=self._extra_body, **kwargs
                )
            return resp.choices[0].message.content or ""

        return self._with_retry(call)

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        return self._complete(self._chat_model, messages, **kwargs)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        # The concurrency slot is held for the whole stream on purpose: a streaming answer
        # is an in-flight API request, so it counts against the provider's concurrency
        # limit. Only stream creation is retried (a failure mid-stream propagates, since
        # tokens may already have been yielded). Callers must exhaust or close the
        # generator, otherwise the slot is not released until it is garbage-collected.
        with self._slots:
            stream = self._with_retry(
                lambda: self._client.chat.completions.create(
                    model=self._chat_model, messages=messages, stream=True, extra_body=self._extra_body
                )
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    def vision(self, prompt: str, image_path: Path) -> str:
        path = Path(image_path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        return self._complete(self._vision_model, messages)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed(texts)
