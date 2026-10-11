import shutil
import sys
from types import SimpleNamespace

import httpx
import numpy as np
import openai
import pytest

from unstuck.ingest import cloud_asr
from unstuck.ingest import transcribe as tr

REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")


def rate_limited(retry_after: str | None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError("rate limited", response=httpx.Response(429, headers=headers, request=REQ), body=None)


def unauthorized():
    return openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None)


def seg(start, end, text, no_speech=0.05, ratio=1.3):
    return SimpleNamespace(start=start, end=end, text=text, no_speech_prob=no_speech, compression_ratio=ratio)


class FakeClient:
    """Plays back `replies` (results or exceptions) for successive transcription calls."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def result(language, *segments):
    return SimpleNamespace(language=language, segments=list(segments))


@pytest.fixture
def no_encode(monkeypatch):
    monkeypatch.setattr(cloud_asr, "encode_samples", lambda samples: (b"ogg", "chunk.ogg", "audio/ogg"))


def make(client, sleeps=None):
    return cloud_asr.CloudTranscriber(client=client, model="whisper-large-v3-turbo",
                                      sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_segments_become_cues_and_doubtful_ones_are_dropped(no_encode):
    client = FakeClient([result("english", seg(0.0, 4.0, " Hello class. "), seg(4.0, 9.0, " you you you", ratio=3.1),
                                seg(9.0, 12.0, " Thanks.", no_speech=0.95))])
    cloud = make(client)
    assert cloud.transcribe(np.zeros(16000, np.float32)) == [(0.0, 4.0, "Hello class.")]
    call = client.calls[0]
    assert call["model"] == "whisper-large-v3-turbo" and call["response_format"] == "verbose_json"
    assert call["file"] == ("chunk.ogg", b"ogg", "audio/ogg") and "language" not in call
    assert cloud.language == "en"


def test_chinese_is_resent_with_the_simplified_prompt_then_fixed(no_encode):
    client = FakeClient([result("chinese", seg(0, 3, "這是導數")), result("chinese", seg(0, 3, "这是导数")),
                         result("chinese", seg(0, 3, "下一段"))])
    cloud = make(client)
    assert cloud.transcribe(np.zeros(16000, np.float32)) == [(0, 3, "这是导数")]
    assert cloud.transcribe(np.zeros(16000, np.float32)) == [(0, 3, "下一段")]
    assert [c.get("language") for c in client.calls] == [None, "zh", "zh"]
    assert "简体中文" in client.calls[1]["prompt"] and "简体中文" in client.calls[2]["prompt"]


def test_short_rate_limit_waits_and_retries(no_encode):
    sleeps = []
    client = FakeClient([rate_limited("7"), result("english", seg(0, 1, "ok"))])
    assert make(client, sleeps).transcribe(np.zeros(16000, np.float32)) == [(0, 1, "ok")]
    assert sleeps == [7.0]


def test_long_rate_limit_or_errors_hand_the_chunk_back(no_encode):
    client = FakeClient([rate_limited("600"), openai.APIConnectionError(request=REQ), result("english", seg(0, 1, "ok"))])
    cloud = make(client)
    assert cloud.transcribe(np.zeros(16000, np.float32)) is None  # caller transcribes this chunk locally
    assert cloud.transcribe(np.zeros(16000, np.float32)) is None
    assert cloud.transcribe(np.zeros(16000, np.float32)) == [(0, 1, "ok")]  # and tries the cloud again next time


def test_a_bad_key_stops_using_the_cloud(no_encode):
    client = FakeClient([unauthorized()])
    cloud = make(client)
    assert cloud.transcribe(np.zeros(16000, np.float32)) is None
    assert cloud.transcribe(np.zeros(16000, np.float32)) is None
    assert len(client.calls) == 1 and cloud.disabled


def test_encode_samples_makes_a_small_decodable_file(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    samples = (0.3 * np.sin(np.linspace(0, 2 * np.pi * 440 * 10, 160000))).astype(np.float32)  # 10 s tone
    data, name, mime = cloud_asr.encode_samples(samples)
    assert len(data) < 60_000  # ~24 kbps
    out = tmp_path / name
    out.write_bytes(data)
    assert abs(len(tr.load_audio(out)) - 160000) < 4000


def test_iter_transcript_uses_the_cloud_and_never_loads_whisper(monkeypatch, tmp_path, no_encode):
    lengths = {0.0: 120.0, 120.0: 30.0}
    monkeypatch.setattr(tr, "load_audio", lambda path, sample_rate=16000, *, start=0.0, duration=None:
                        np.zeros(int(lengths.get(start, 0.0) * 16000), dtype=np.float32))
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # importing it would fail the test
    tr._model.cache_clear()
    client = FakeClient([result("english", seg(1, 5, "first")), result("english", seg(2, 6, "second"))])
    events = list(tr.iter_transcript(tmp_path / "a.m4a", "small", cloud=make(client)))
    assert [(c.start, c.end, c.text) for e in events for c in e.cues] == [(1, 5, "first"), (122, 126, "second")]
    assert events[-1].covered_until == 150.0


def test_iter_transcript_falls_back_to_local_whisper_for_a_chunk(monkeypatch, tmp_path, no_encode):
    lengths = {0.0: 120.0, 120.0: 30.0}
    monkeypatch.setattr(tr, "load_audio", lambda path, sample_rate=16000, *, start=0.0, duration=None:
                        np.zeros(int(lengths.get(start, 0.0) * 16000), dtype=np.float32))
    local_calls = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            pass

        def detect_language(self, audio, **kwargs):
            raise AssertionError("the cloud already told us the language")

        def transcribe(self, audio, **kwargs):
            local_calls.append(kwargs)
            return [SimpleNamespace(start=0.0, end=2.0, text=" local ")], None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    tr._model.cache_clear()
    client = FakeClient([result("english", seg(1, 5, "cloud")), rate_limited("900")])
    events = list(tr.iter_transcript(tmp_path / "a.m4a", "small", cloud=make(client)))
    assert [c.text for e in events for c in e.cues] == ["cloud", "local"]
    assert local_calls[0]["language"] == "en"
    tr._model.cache_clear()
