import sys
from types import SimpleNamespace

from unstuck.ingest import transcribe as tr


def test_model_is_loaded_once_per_name(monkeypatch, tmp_path):
    loads = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            loads.append(name)

        def transcribe(self, path, vad_filter):
            return [SimpleNamespace(start=0.0, end=1.0, text=" hi ")], None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    tr._model.cache_clear()
    audio = tmp_path / "a.m4a"
    first = tr.transcribe(audio, "tiny")
    tr.transcribe(audio, "tiny")
    assert loads == ["tiny"]
    assert [(c.start, c.end, c.text) for c in first] == [(0.0, 1.0, "hi")]
    tr._model.cache_clear()
