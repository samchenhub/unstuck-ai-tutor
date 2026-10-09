import dataclasses
from types import SimpleNamespace

import httpx
import openai
import pytest

from unstuck.llm import ModelBusyError, factory
from unstuck.llm.openai_compat import OpenAICompatLLM


class StubEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0, 0.0] for _ in texts]


class StubClient:
    """Stands in for openai.OpenAI: records create() kwargs, replays queued results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def chunk(text, *, no_choices=False):
    choices = [] if no_choices else [SimpleNamespace(delta=SimpleNamespace(content=text))]
    return SimpleNamespace(choices=choices)


def make_llm(results, *, extra_body=None, embedder=None, **kwargs):
    llm = OpenAICompatLLM(
        base_url="http://fake.invalid",
        api_key="k",
        chat_model="chat-m",
        vision_model="vision-m",
        embedder=embedder or StubEmbedder(),
        extra_body=extra_body,
        **kwargs,
    )
    llm._client = StubClient(results)
    return llm


def connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("unstuck.llm.retry.time.sleep", lambda s: None)


def test_chat_json_mode_passes_response_format_model_and_extra_body():
    extra = {"thinking": {"type": "disabled"}}
    llm = make_llm([reply("{}")], extra_body=extra)
    assert llm.chat([{"role": "user", "content": "hi"}], json_mode=True) == "{}"
    (call,) = llm._client.calls
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "chat-m"
    assert call["extra_body"] == extra


def test_plain_chat_has_no_response_format():
    llm = make_llm([reply("hello")])
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hello"
    assert "response_format" not in llm._client.calls[0]


def test_chat_retries_retryable_error_then_succeeds():
    llm = make_llm([connection_error(), reply("ok")])
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert len(llm._client.calls) == 2


def rate_limit_error():
    response = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return openai.RateLimitError("busy", response=response, body=None)


def test_chat_backs_off_2_4_8_16_32_seconds_by_default(monkeypatch):
    delays = []
    monkeypatch.setattr("unstuck.llm.retry.time.sleep", delays.append)
    llm = make_llm([connection_error() for _ in range(5)] + [reply("ok")])
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_retry_settings_are_configurable(monkeypatch):
    delays = []
    monkeypatch.setattr("unstuck.llm.retry.time.sleep", delays.append)
    llm = make_llm([connection_error(), connection_error()], retry_attempts=2, retry_base_delay=0.5)
    with pytest.raises(openai.APIConnectionError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert delays == [0.5]


def test_exhausted_rate_limit_raises_model_busy_error():
    original = rate_limit_error()
    llm = make_llm([original for _ in range(6)])
    with pytest.raises(ModelBusyError, match="稍后重试") as info:
        llm.chat([{"role": "user", "content": "hi"}])
    assert info.value.__cause__ is original
    assert len(llm._client.calls) == 6


def test_exhausted_rate_limit_on_stream_creation_raises_model_busy_error():
    llm = make_llm([rate_limit_error() for _ in range(6)])
    with pytest.raises(ModelBusyError):
        list(llm.chat_stream([{"role": "user", "content": "hi"}]))


def test_exhausted_connection_error_propagates_unchanged():
    llm = make_llm([connection_error() for _ in range(6)])
    with pytest.raises(openai.APIConnectionError):
        llm.chat([{"role": "user", "content": "hi"}])


def test_chat_does_not_retry_non_retryable_error():
    llm = make_llm([ValueError("bad"), reply("never")])
    with pytest.raises(ValueError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(llm._client.calls) == 1


def test_vision_sends_data_url_then_text(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG-bytes")
    llm = make_llm([reply("a diagram")])
    assert llm.vision("describe", image) == "a diagram"
    (call,) = llm._client.calls
    assert call["model"] == "vision-m"
    (message,) = call["messages"]
    image_part, text_part = message["content"]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert text_part == {"type": "text", "text": "describe"}


def test_chat_stream_yields_only_non_empty_content():
    stream = [chunk("a"), chunk(None), chunk(""), chunk(None, no_choices=True), chunk("b")]
    llm = make_llm([iter(stream)])
    assert list(llm.chat_stream([{"role": "user", "content": "hi"}])) == ["a", "b"]
    call = llm._client.calls[0]
    assert call["stream"] is True
    assert call["model"] == "chat-m"


def test_embed_delegates_to_embedder():
    embedder = StubEmbedder()
    llm = make_llm([], embedder=embedder)
    assert llm.embed(["x", "y"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert embedder.calls == [["x", "y"]]


def test_build_llm_extra_body_follows_disable_thinking(settings):
    keyed = dataclasses.replace(settings, llm_api_key="test-key")  # the OpenAI client rejects an empty key
    on = factory.build_llm(dataclasses.replace(keyed, llm_disable_thinking=True))
    off = factory.build_llm(dataclasses.replace(keyed, llm_disable_thinking=False))
    assert on._extra_body == {"thinking": {"type": "disabled"}}
    assert off._extra_body is None
