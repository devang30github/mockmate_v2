from dataclasses import dataclass

from app.config import Settings
from app.providers.base import EmbeddingProvider, LLMProvider, STTProvider, TTSProvider
from app.providers.mock.providers import MockEmbeddings, MockLLM, MockSTT, MockTTS


@dataclass
class Providers:
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    embeddings: EmbeddingProvider


def build_providers(settings: Settings) -> Providers:
    if settings.provider_mode == "mock":
        return Providers(llm=MockLLM(), stt=MockSTT(), tts=MockTTS(), embeddings=MockEmbeddings())
    # Never fall back to mocks silently: live adapters are added in later stages.
    raise NotImplementedError(
        "PROVIDER_MODE=live has no adapters yet; refusing to fall back to mocks"
    )