from functools import lru_cache
from pathlib import Path

from unstuck.models import Cue


@lru_cache(maxsize=1)
def _model(model_name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device="auto", compute_type="int8")


def transcribe(audio_path: Path, model_name: str) -> list[Cue]:
    """Speech-to-text fallback for videos without captions (runs locally on CPU)."""
    segments, _info = _model(model_name).transcribe(str(audio_path), vad_filter=True)
    return [Cue(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
