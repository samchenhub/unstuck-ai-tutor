"""Speech-to-text in the cloud (Groq's OpenAI-compatible Whisper endpoint), so the laptop's CPU stays idle.

Each chunk is compressed to Opus (~1.8 MB per 10 minutes) and sent with segment timestamps. Any problem hands
the chunk back (`None`) so the caller transcribes it locally; a bad key turns the cloud off for the run.
"""

import logging
import subprocess
import time
from collections.abc import Callable

import numpy as np
import openai

log = logging.getLogger(__name__)

SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文。"
MAX_WAIT_S = 60.0  # wait out a short rate limit; a longer one is cheaper to transcribe locally
MAX_NO_SPEECH = 0.8  # Whisper's own estimate that a segment is silence (where it tends to hallucinate)
MAX_COMPRESSION_RATIO = 2.4  # repetitive output ("you you you…") compresses unusually well
MIN_TEXT_TO_LOCK_LANGUAGE = 20  # characters of kept speech; a music/silent intro must not decide the language

# verbose_json names the language ("english"); requests take ISO-639-1 codes.
_LANGUAGE_CODES = {
    "chinese": "zh", "english": "en", "japanese": "ja", "korean": "ko", "french": "fr", "german": "de",
    "spanish": "es", "russian": "ru", "italian": "it", "portuguese": "pt", "cantonese": "yue",
}


def encode_samples(samples: np.ndarray, sample_rate: int = 16000) -> tuple[bytes, str, str]:
    """Compress mono float32 samples for upload: Opus/Ogg at 24 kbps (MP3, then WAV, if Opus is missing)."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    source = ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "-"]
    for codec, fmt, name, mime in (
        (["-c:a", "libopus", "-b:a", "24k"], "ogg", "chunk.ogg", "audio/ogg"),
        (["-c:a", "libmp3lame", "-b:a", "32k"], "mp3", "chunk.mp3", "audio/mpeg"),
        (["-c:a", "pcm_s16le"], "wav", "chunk.wav", "audio/wav"),
    ):
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", *source, *codec, "-f", fmt, "-"]
        try:
            result = subprocess.run(cmd, input=pcm, capture_output=True, check=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if result.stdout:
            return result.stdout, name, mime
    raise RuntimeError("ffmpeg could not encode audio for upload")


def _retry_after(exc: openai.APIStatusError) -> float | None:
    value = exc.response.headers.get("retry-after") if exc.response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _kept_segments(reply) -> list[tuple[float, float, str]]:
    """(start, end, text) of segments that are probably speech (not silence or repetition)."""
    return [
        (s.start, s.end, s.text.strip())
        for s in reply.segments or []
        if s.text.strip()
        and (getattr(s, "no_speech_prob", 0.0) or 0.0) <= MAX_NO_SPEECH
        and (getattr(s, "compression_ratio", 0.0) or 0.0) <= MAX_COMPRESSION_RATIO
    ]


class CloudTranscriber:
    """Transcribes chunks remotely; keeps the detected language so every chunk uses the same one."""

    def __init__(self, *, client, model: str, sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = sleep
        self.language: str | None = None
        self.disabled = False

    @classmethod
    def from_settings(cls, settings) -> "CloudTranscriber | None":
        if not settings.groq_api_key:
            return None
        client = openai.OpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url,
                               max_retries=0, timeout=180)
        return cls(client=client, model=settings.groq_whisper_model)

    def transcribe(self, samples: np.ndarray) -> list[tuple[float, float, str]] | None:
        """(start, end, text) relative to the chunk, or None to transcribe this chunk locally instead."""
        if self.disabled:
            return None
        try:
            upload = encode_samples(samples)
        except RuntimeError:
            log.warning("could not encode audio for cloud transcription", exc_info=True)
            return None
        reply = self._request(upload)
        if reply is None:
            return None
        language = _LANGUAGE_CODES.get((getattr(reply, "language", "") or "").lower())
        kept = _kept_segments(reply)
        if self.language is None and language and sum(len(text) for _s, _e, text in kept) >= MIN_TEXT_TO_LOCK_LANGUAGE:
            self.language = language
            if language == "zh":  # Whisper writes Traditional characters for Mandarin unless prompted
                reply = self._request(upload)
                if reply is None:
                    return None
                kept = _kept_segments(reply)
        return kept

    def _request(self, upload: tuple[bytes, str, str]):
        data, name, mime = upload
        options: dict = {"model": self._model, "file": (name, data, mime), "response_format": "verbose_json",
                         "timestamp_granularities": ["segment"]}
        if self.language:
            options["language"] = self.language
        if self.language == "zh":
            options["prompt"] = SIMPLIFIED_PROMPT
        for _attempt in range(3):
            try:
                return self._client.audio.transcriptions.create(**options)
            except openai.RateLimitError as exc:
                wait = _retry_after(exc)
                if wait is None or wait > MAX_WAIT_S:
                    log.warning("cloud transcription rate-limited (retry-after %s); this chunk runs locally", wait)
                    return None
                self._sleep(wait)
            except (openai.AuthenticationError, openai.PermissionDeniedError):
                log.error("cloud transcription key rejected; transcribing locally from now on")
                self.disabled = True
                return None
            except openai.OpenAIError:
                log.warning("cloud transcription failed; this chunk runs locally", exc_info=True)
                return None
        return None
