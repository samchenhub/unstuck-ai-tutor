import math
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from unstuck.ingest.cloud_asr import CloudTranscriber
from unstuck.media import ffmpeg_exe
from unstuck.models import Cue
from unstuck.sources.base import AudioFeed

_SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文。"
SAMPLE_RATE = 16000
CHUNK_S = 600.0  # decode 10 minutes at a time: bounded memory, and progress can be published as it goes
FIRST_CHUNK_S = 120.0  # ...but start with 2 minutes, so the first minute is published (and asking opens) sooner
MIN_LANGUAGE_PROBABILITY = 0.5  # silence/noise is detected as English at ~0.39; don't lock that in


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
    cmd = [ffmpeg_exe(), "-nostdin", "-loglevel", "error"]
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
    return _language_options(language), True


def _language_options(language: str) -> dict:
    options: dict = {"vad_filter": True, "language": language}
    if language == "zh":
        # Whisper tends to emit Traditional characters for Mandarin; a Simplified prompt steers it.
        options["initial_prompt"] = _SIMPLIFIED_PROMPT
    return options


def iter_transcript(
    audio: Path | AudioFeed,
    model_name: str,
    *,
    start: float = 0.0,
    chunk_s: float = CHUNK_S,
    first_chunk_s: float = FIRST_CHUNK_S,
    cloud: CloudTranscriber | None = None,
) -> Iterator[TranscriptProgress]:
    """Speech-to-text for videos without captions, yielding cues as they are produced.

    Each chunk goes to `cloud` when given (no local CPU); a chunk the cloud hands back, or every chunk without
    one, runs through local Whisper. `audio` may still be downloading (an AudioFeed): each chunk is decoded once
    it is on disk. One event per transcribed segment and one at the end of each chunk; starts at `start` to resume.
    """
    feed = audio if isinstance(audio, AudioFeed) else AudioFeed.complete(audio)
    model = None  # loaded only if a chunk has to be transcribed locally
    options: dict | None = None
    offset = start
    size = first_chunk_s
    while True:
        feed.wait_for(offset + size)
        complete = feed.is_complete  # read before decoding: a decode that races the download's end is retried
        try:
            samples = load_audio(feed.path(), start=offset, duration=size)
        except RuntimeError:
            if complete:
                raise
            feed.wait_for(math.inf)  # e.g. the download fell back to another file; decode again once it is done
            continue
        seconds = len(samples) / SAMPLE_RATE
        if seconds < size - 1 and not complete:
            feed.wait_for_more()  # the estimate ran ahead of the bytes on disk; not the real end of the audio
            continue
        if seconds < 0.5:  # past the end of the audio
            return
        remote = cloud.transcribe(samples) if cloud is not None else None
        if remote is not None:
            for seg_start, seg_end, text in remote:
                yield TranscriptProgress([Cue(offset + seg_start, offset + seg_end, text)], offset + seg_end)
        else:
            if model is None:
                model = _model(model_name)
            chunk_options = options
            if chunk_options is None and cloud is not None and cloud.language:
                chunk_options = options = _language_options(cloud.language)  # the cloud already detected it
            if chunk_options is None:
                chunk_options, confident = _options(model, samples)
                if confident:
                    options = chunk_options
            segments, _info = model.transcribe(samples, **chunk_options)
            for segment in segments:  # a lazy generator: segments arrive while Whisper works through the chunk
                text = segment.text.strip()
                if text:
                    yield TranscriptProgress(
                        [Cue(offset + segment.start, offset + segment.end, text)], offset + segment.end
                    )
        del samples
        yield TranscriptProgress([], offset + seconds)
        if seconds < size - 1:  # a short chunk of a complete file is the last one
            return
        offset += size
        size = chunk_s
