from pathlib import Path

from unstuck.models import Cue


def transcribe(audio_path: Path, model_name: str) -> list[Cue]:
    """Speech-to-text fallback for videos without captions (runs locally on CPU)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    return [Cue(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
