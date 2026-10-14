from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float = 0.0


@dataclass
class STTResult:
    text: str
    language: str = "en"
    confidence: float | None = None
    duration_s: float = 0.0
    latency_ms: float = 0.0


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    chars: int
    latency_ms: float = 0.0


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    def stream(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16000, hints: list[str] | None = None
    ) -> STTResult: ...


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dim(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...