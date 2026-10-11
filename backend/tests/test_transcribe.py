import shutil
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from unstuck.ingest import transcribe as tr


@pytest.fixture
def tone_wav(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def fake_audio(monkeypatch):
    audio = np.zeros(16000, dtype=np.float32)
    monkeypatch.setattr(tr, "load_audio", lambda path, sample_rate=16000, **kw: audio)
    return audio


def _install_fake_model(monkeypatch, language, calls, loads=None):
    detect_kwargs = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            if loads is not None:
                loads.append(name)

        def detect_language(self, audio, **kwargs):
            detect_kwargs.append(kwargs)
            return language, 0.9, []

        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return [SimpleNamespace(start=0.0, end=1.0, text=" hi ")], None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    tr._model.cache_clear()
    return detect_kwargs


def test_load_audio_returns_16k_mono_float32(tone_wav):
    audio = tr.load_audio(tone_wav)
    assert audio.dtype == np.float32
    assert abs(len(audio) - 16000) < 400
    assert np.abs(audio).max() <= 1.0
    assert np.abs(audio).max() > 0.1


def test_load_audio_reports_ffmpeg_errors(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    with pytest.raises(RuntimeError, match="ffmpeg"):
        tr.load_audio(tmp_path / "missing.m4a")


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired(["ffmpeg"], 600),
    FileNotFoundError(2, "No such file or directory: 'ffmpeg'"),
])
def test_load_audio_wraps_ffmpeg_launch_failures(monkeypatch, tmp_path, error):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(tr.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        tr.load_audio(tmp_path / "a.m4a")


def test_model_is_loaded_once_per_name(monkeypatch, tmp_path, fake_audio):
    loads, calls = [], []
    _install_fake_model(monkeypatch, "en", calls, loads)
    audio = tmp_path / "a.m4a"
    first = tr.transcribe(audio, "tiny")
    tr.transcribe(audio, "tiny")
    assert loads == ["tiny"]
    assert [(c.start, c.end, c.text) for c in first] == [(0.0, 1.0, "hi")]
    tr._model.cache_clear()


def test_chinese_audio_gets_simplified_prompt(monkeypatch, tmp_path, fake_audio):
    calls = []
    detect_kwargs = _install_fake_model(monkeypatch, "zh", calls)
    tr.transcribe(tmp_path / "a.m4a", "tiny")
    audio, kwargs = calls[0]
    assert isinstance(audio, np.ndarray)
    assert "简体中文" in kwargs["initial_prompt"]
    assert kwargs["language"] == "zh"
    assert detect_kwargs == [{"vad_filter": True}]  # speech-only language detection
    assert kwargs["vad_filter"] is True
    tr._model.cache_clear()


def test_other_languages_get_no_prompt(monkeypatch, tmp_path, fake_audio):
    calls = []
    _install_fake_model(monkeypatch, "en", calls)
    tr.transcribe(tmp_path / "a.m4a", "tiny")
    audio, kwargs = calls[0]
    assert isinstance(audio, np.ndarray)
    assert not kwargs.get("initial_prompt")
    assert kwargs["vad_filter"] is True
    tr._model.cache_clear()


def test_load_audio_reads_a_time_range(tone_wav):
    audio = tr.load_audio(tone_wav, start=0.5, duration=0.25)
    assert abs(len(audio) - 4000) < 400


def test_iter_transcript_walks_chunks_with_absolute_times(monkeypatch, tmp_path):
    """Chunks are decoded one at a time; cues are shifted by the chunk offset; a short chunk ends it."""
    loads = []
    lengths = {0.0: 10.0, 10.0: 10.0, 20.0: 4.0}

    def fake_load(path, sample_rate=16000, *, start=0.0, duration=None):
        loads.append((start, duration))
        return np.zeros(int(lengths.get(start, 0.0) * 16000), dtype=np.float32)

    monkeypatch.setattr(tr, "load_audio", fake_load)
    calls = []
    detect_kwargs = _install_fake_model(monkeypatch, "en", calls)
    events = list(tr.iter_transcript(tmp_path / "a.m4a", "tiny", chunk_s=10.0))
    assert loads == [(0.0, 10.0), (10.0, 10.0), (20.0, 10.0)]
    assert [(c.start, c.end, c.text) for e in events for c in e.cues] == [
        (0.0, 1.0, "hi"), (10.0, 11.0, "hi"), (20.0, 21.0, "hi"),
    ]
    assert [e.covered_until for e in events] == [1.0, 10.0, 11.0, 20.0, 21.0, 24.0]
    assert len(detect_kwargs) == 1  # language detected once, then fixed for later chunks
    assert all(kwargs["language"] == "en" for _audio, kwargs in calls)
    tr._model.cache_clear()


def test_iter_transcript_resumes_and_stops_past_the_end(monkeypatch, tmp_path):
    loads = []

    def fake_load(path, sample_rate=16000, *, start=0.0, duration=None):
        loads.append(start)
        return np.zeros(0, dtype=np.float32)

    monkeypatch.setattr(tr, "load_audio", fake_load)
    _install_fake_model(monkeypatch, "en", [])
    assert list(tr.iter_transcript(tmp_path / "a.m4a", "tiny", start=1234.0)) == []
    assert loads == [1234.0]
    tr._model.cache_clear()
