import base64
import mimetypes
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

import openai
from openai import OpenAI

from unstuck.llm.base import ModelBusyError, ModelQuotaError
from unstuck.llm.embedder import BgeM3Embedder
from unstuck.llm.retry import with_retry

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)

MODEL_BUSY_MESSAGE = "模型当前访问量过大（免费额度限流），请稍后重试"
NO_BALANCE_MESSAGE = (
    "模型账户余额不足或没有可用资源包：请到 bigmodel.cn 充值，或在 backend/.env 里换回免费模型"
    "（CHAT_MODEL=glm-4.7-flash，VISION_MODEL=glm-4.6v-flash）"
)
_NO_BALANCE_CODES = {"1113"}  # Zhipu answers HTTP 429 for both rate limits and an empty balance


def _is_no_balance(exc: openai.RateLimitError) -> bool:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    return str(error.get("code", "")) in _NO_BALANCE_CODES or "余额不足" in str(error.get("message", ""))

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

    def _with_retry(self, fn: Callable[[], T], attempts: int | None = None) -> T:
        # Free tiers rate-limit in bursts, so the default waits 2+4+8+16+32 s before giving up.
        # Interactive callers pass a smaller `attempts` so a busy model fails fast.
        def call() -> T:
            try:
                return fn()
            except openai.RateLimitError as exc:
                if _is_no_balance(exc):
                    raise ModelQuotaError(NO_BALANCE_MESSAGE) from exc
                raise

        try:
            return with_retry(
                call, attempts=attempts or self._retry_attempts, base_delay=self._retry_base_delay,
                retry_on=RETRYABLE, give_up_on=(ModelQuotaError,),
            )
        except openai.RateLimitError as exc:
            raise ModelBusyError(MODEL_BUSY_MESSAGE) from exc

    def _complete(self, model: str, messages: list[dict], *, attempts: int | None = None, **kwargs) -> str:
        def call() -> str:
            with self._slots:
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, extra_body=self._extra_body, **kwargs
                )
            return resp.choices[0].message.content or ""

        return self._with_retry(call, attempts)

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        return self._complete(self._chat_model, messages, **kwargs)

    def chat_stream(self, messages: list[dict], *, attempts: int | None = None) -> Iterator[str]:
        # An open stream is an in-flight API request, so it holds a concurrency slot for
        # its whole life; the slot is released when the generator finishes, raises or is
        # closed. Backoff sleeps never hold the slot: each creation attempt acquires it
        # and gives it back on failure, so a rate-limited answer cannot starve ingest.
        # Only stream creation is retried (a failure mid-stream propagates, since tokens
        # may already have been yielded). Callers must exhaust or close the generator,
        # otherwise the slot is not released until it is garbage-collected.
        def create():
            self._slots.acquire()
            try:
                return self._client.chat.completions.create(
                    model=self._chat_model, messages=messages, stream=True, extra_body=self._extra_body
                )
            except BaseException:
                self._slots.release()
                raise

        stream = self._with_retry(create, attempts)
        try:
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            self._slots.release()

    def vision(self, prompt: str, image_path: Path, *, attempts: int | None = None) -> str:
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
        return self._complete(self._vision_model, messages, attempts=attempts)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed(texts)
