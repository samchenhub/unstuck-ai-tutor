import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from unstuck.models import Cue

_SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文。"


@lru_cache(maxsize=1)
def _model(model_name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device="auto", compute_type="int8")


def load_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode any audio/video file to mono float32 samples with ffmpeg.

    faster-whisper's own decoder goes through PyAV, whose `open()` signature changed in v19 and breaks it.
    """
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "-"]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed to decode audio: {stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out while decoding audio") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed or not on PATH") from exc
    stdout = result.stdout
    del result
    audio = np.frombuffer(stdout, np.int16).astype(np.float32)
    del stdout  # the raw PCM is as large as the float copy; drop it before normalising in place
    audio /= 32768.0
    return audio


def transcribe(audio_path: Path, model_name: str) -> list[Cue]:
    """Speech-to-text fallback for videos without captions (runs locally on CPU)."""
    model = _model(model_name)
    audio = load_audio(audio_path)
    language, _probability, _all = model.detect_language(audio, vad_filter=True)
    options: dict = {"vad_filter": True}
    if language == "zh":
        # Whisper tends to emit Traditional characters for Mandarin; a Simplified prompt steers it.
        options.update(language="zh", initial_prompt=_SIMPLIFIED_PROMPT)
    segments, _info = model.transcribe(audio, **options)
    return [Cue(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
