import asyncio
import hashlib
import io
import json
import math
import random
import re
import struct
import wave
from collections.abc import AsyncIterator

from app.providers.base import LLMMessage, LLMResult, STTResult, TTSResult

MOCK_LLM_TEXT = (
    "Thanks for that answer. Can you walk me through a specific project "
    "where you applied it? What trade-offs did you face?"
)


class MockLLM:
    model = "mock-llm"

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        if json_mode:
            last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
            text = json.dumps({"mock": True, "echo": last_user[:40]})
        else:
            text = MOCK_LLM_TEXT
        return LLMResult(text=text, model=self.model, prompt_tokens=0, completion_tokens=0)

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        for chunk in re.findall(r"\S+\s*", MOCK_LLM_TEXT):
            await asyncio.sleep(0)
            yield chunk


class MockSTT:
    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16000, hints: list[str] | None = None
    ) -> STTResult:
        duration = len(pcm16) / (2 * sample_rate)
        return STTResult(
            text=f"mock transcript of {duration:.1f} seconds of audio",
            duration_s=duration,
            confidence=1.0,
        )


class MockTTS:
    """Returns a short, valid, playable WAV (a quiet 440 Hz tone)."""

    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult:
        rate = 22050
        seconds = min(max(len(text) * 0.04, 0.3), 5.0)
        n = int(rate * seconds)
        frames = b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(n)
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        return TTSResult(audio=buf.getvalue(), mime="audio/wav", chars=len(text))


class MockEmbeddings:
    dim = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            seed = int(hashlib.sha256(t.encode()).hexdigest(), 16)
            rng = random.Random(seed)
            vec = [rng.uniform(-1, 1) for _ in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec])
        return out