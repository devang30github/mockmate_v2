import asyncio
import logging
import math
import re
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from app.domain.stt_hints import build_initial_prompt
from app.providers.base import STTResult
from app.providers.errors import BadRequest, ProviderError, TransientError

log = logging.getLogger("mockmate.stt")

SAMPLE_RATE = 16000
NO_SPEECH_PROB = 0.6  # a segment that is probably silence AND low confidence is dropped
LOW_LOGPROB = -1.0
MIN_ECHO_WORDS = 5
_WORDS = re.compile(r"[a-z0-9'+#]+")


def _words(text: str) -> list[str]:
    return _WORDS.findall(text.lower())


def _is_prompt_echo(text: str, prompt: str | None) -> bool:
    """Whisper sometimes answers silence by repeating its prompt back."""
    if not prompt:
        return False
    spoken = _words(text)
    if len(spoken) < MIN_ECHO_WORDS:
        return False
    return " ".join(spoken) in " ".join(_words(prompt))


def _default_factory(model: str, compute_type: str, cpu_threads: int):
    from faster_whisper import WhisperModel  # lazy: heavy import, tests don't need it

    return WhisperModel(model, device="cpu", compute_type=compute_type, cpu_threads=cpu_threads)


class FasterWhisperSTT:
    """Local CPU speech-to-text. One decode at a time; every failure becomes a ProviderError."""

    def __init__(
        self,
        *,
        model: str = "small",
        compute_type: str = "int8",
        cpu_threads: int = 0,
        beam_size: int = 1,
        language: str = "en",
        vad_filter: bool = True,
        model_factory: Callable[..., Any] | None = None,
    ):
        self.model_id = model
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._beam_size = beam_size
        self._language = language or None
        self._vad_filter = vad_filter
        self._factory = model_factory or _default_factory
        self._model: Any = None
        self._load_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(1)

    @property
    def model_name(self) -> str:
        return f"faster-whisper:{self.model_id}"

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                t0 = time.perf_counter()
                log.info(
                    "stt loading model=%s compute_type=%s (the first run downloads the weights)",
                    self.model_id,
                    self._compute_type,
                )
                try:
                    self._model = await asyncio.to_thread(
                        self._factory, self.model_id, self._compute_type, self._cpu_threads
                    )
                except Exception:
                    log.exception("stt model load failed")
                    raise ProviderError("speech model unavailable") from None
                log.info("stt model loaded in %.1fs", time.perf_counter() - t0)
        return self._model

    async def warmup(self) -> None:
        model = await self._ensure_model()
        try:
            async with self._sem:
                await asyncio.to_thread(
                    self._decode, model, np.zeros(SAMPLE_RATE, dtype=np.float32), None, False
                )
        except Exception:
            log.warning("stt warm-up decode failed; continuing")

    def _decode(self, model, audio, prompt, vad=None):
        use_vad = self._vad_filter if vad is None else vad
        segments, _info = model.transcribe(
            audio,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=use_vad,
            initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        texts: list[str] = []
        logprobs: list[float] = []
        for seg in segments:  # a generator: the decoding actually runs here
            if seg.no_speech_prob > NO_SPEECH_PROB and seg.avg_logprob < LOW_LOGPROB:
                continue
            text = seg.text.strip()
            if text:
                texts.append(text)
                logprobs.append(seg.avg_logprob)
        confidence = None
        if logprobs:  # approximate: exp of the mean log-probability, not a calibrated score
            confidence = max(0.0, min(1.0, math.exp(sum(logprobs) / len(logprobs))))
        return " ".join(texts), confidence

    async def transcribe(
        self, pcm16: bytes, sample_rate: int = SAMPLE_RATE, hints: list[str] | None = None
    ) -> STTResult:
        if sample_rate != SAMPLE_RATE:
            raise BadRequest(f"expected {SAMPLE_RATE} Hz audio")
        language = self._language or "en"
        if len(pcm16) < 2:
            return STTResult(text="", language=language)

        model = await self._ensure_model()
        prompt = build_initial_prompt(hints or [])
        samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2")
        audio = samples.astype(np.float32) / 32768.0

        t0 = time.perf_counter()
        try:
            async with self._sem:
                text, confidence = await asyncio.to_thread(self._decode, model, audio, prompt)
        except ProviderError:
            raise
        except Exception:
            log.exception("stt decode failed")
            raise TransientError("transcription failed") from None
        latency_ms = (time.perf_counter() - t0) * 1000

        if _is_prompt_echo(text, prompt):
            log.info("stt dropped a repeated prompt")
            text, confidence = "", None
        duration_s = len(audio) / SAMPLE_RATE
        log.info(
            "stt ok model=%s audio_s=%.1f decode_ms=%.0f chars=%d",
            self.model_id,
            duration_s,
            latency_ms,
            len(text),
        )
        return STTResult(
            text=text,
            language=language,
            confidence=confidence,
            duration_s=duration_s,
            latency_ms=latency_ms,
        )