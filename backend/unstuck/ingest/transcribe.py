import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from unstuck.models import Cue

_SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文。"
SAMPLE_RATE = 16000
CHUNK_S = 600.0
MIN_LANGUAGE_PROBABILITY = 0.5  # silence/noise is detected as English at ~0.39; don't lock that in  # decode 10 minutes at a time: bounded memory, and progress can be published as it goes


@dataclass(frozen=True)
class TranscriptProgress:
    cues: list[Cue]  # new cues since the previous event, in absolute video time
    covered_until: float  # seconds of audio transcribed so far


@lru_cache(maxsize=1)
def _model(model_name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device="auto", compute_type="int8")


def load_audio(
    path: Path, sample_rate: int = SAMPLE_RATE, *, start: float = 0.0, duration: float | None = None
) -> np.ndarray:
    """Decode any audio/video file (optionally only `duration` seconds from `start`) to mono float32 with ffmpeg.

    faster-whisper's own decoder goes through PyAV, whose `open()` signature changed in v19 and breaks it.
    """
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "-"]
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


def _options(model, audio: np.ndarray) -> tuple[dict, bool]:
    """Detect the language (speech only); returns the options and whether to keep them for later chunks.

    A confident language is fixed so later chunks cannot drift; an unsure one (a silent intro) is not.
    """
    language, probability, _all = model.detect_language(audio, vad_filter=True)
    if probability < MIN_LANGUAGE_PROBABILITY:
        return {"vad_filter": True}, False
    options: dict = {"vad_filter": True, "language": language}
    if language == "zh":
        # Whisper tends to emit Traditional characters for Mandarin; a Simplified prompt steers it.
        options["initial_prompt"] = _SIMPLIFIED_PROMPT
    return options, True


def iter_transcript(
    audio_path: Path, model_name: str, *, start: float = 0.0, chunk_s: float = CHUNK_S
) -> Iterator[TranscriptProgress]:
    """Speech-to-text for videos without captions (local CPU), yielding cues as Whisper produces them.

    One event per transcribed segment and one at the end of each chunk; starts at `start` to resume.
    """
    model = _model(model_name)
    options: dict | None = None
    offset = start
    while True:
        audio = load_audio(audio_path, start=offset, duration=chunk_s)
        seconds = len(audio) / SAMPLE_RATE
        if seconds < 0.5:  # past the end of the audio
            return
        chunk_options = options
        if chunk_options is None:
            chunk_options, confident = _options(model, audio)
            if confident:
                options = chunk_options
        segments, _info = model.transcribe(audio, **chunk_options)
        for segment in segments:  # a lazy generator: segments arrive while Whisper works through the chunk
            text = segment.text.strip()
            if text:
                yield TranscriptProgress([Cue(offset + segment.start, offset + segment.end, text)], offset + segment.end)
        del audio
        yield TranscriptProgress([], offset + seconds)
        if seconds < chunk_s - 1:  # a short chunk is the last one
            return
        offset += chunk_s

