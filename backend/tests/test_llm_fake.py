import math

import pytest

from unstuck.llm import FakeLLM


def test_chat_returns_queued_then_default():
    llm = FakeLLM(chat_responses=["one"])
    assert llm.chat([{"role": "user", "content": "hi"}]) == "one"
    assert llm.chat([]) == "fake answer"
    assert llm.calls[0] == ("chat", [{"role": "user", "content": "hi"}])


def test_chat_raises_queued_exception():
    llm = FakeLLM(chat_responses=[RuntimeError("boom")])
    with pytest.raises(RuntimeError):
        llm.chat([])


def test_stream_chunks_then_midstream_error():
    llm = FakeLLM(chat_responses=[["Hel", "lo", RuntimeError("cut")]])
    got = []
    with pytest.raises(RuntimeError):
        for chunk in llm.chat_stream([]):
            got.append(chunk)
    assert got == ["Hel", "lo"]


def test_stream_plain_string_is_chunked():
    llm = FakeLLM(chat_responses=["hello world, this is long"])
    chunks = list(llm.chat_stream([]))
    assert len(chunks) > 1
    assert "".join(chunks) == "hello world, this is long"


def test_vision_default_is_valid_json_and_records_call():
    llm = FakeLLM()
    assert '"summary"' in llm.vision("describe", "/tmp/x.jpg")
    assert llm.calls == [("vision", ("describe", "/tmp/x.jpg"))]


def test_embed_is_deterministic_and_normalized():
    llm = FakeLLM(embed_dim=16)
    a, b = llm.embed(["dot product", "dot product"])
    assert a == b
    assert len(a) == 16
    assert math.isclose(sum(x * x for x in a), 1.0)


def test_embed_shared_words_are_closer():
    llm = FakeLLM(embed_dim=64)
    q, near, far = llm.embed(["dot product geometry", "the dot product", "eigenvalues of matrices"])
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))
    assert dot(q, near) > dot(q, far)
