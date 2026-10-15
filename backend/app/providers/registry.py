from dataclasses import dataclass

from app.config import Settings
from app.credentials.pool import CredentialPool
from app.providers.base import EmbeddingProvider, LLMProvider, STTProvider, TTSProvider
from app.providers.llm_openai_compat import OpenAICompatLLM
from app.providers.mock.providers import MockEmbeddings, MockLLM, MockSTT, MockTTS
from app.providers.ratelimit import RateLimiter

@dataclass
class Providers:
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    embeddings: EmbeddingProvider


def _build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "mock":
        return MockLLM()
    pool = CredentialPool.from_csv("apinex", settings.apinex_api_keys)
    if not settings.apinex_model.strip() or not pool.snapshot():
        raise RuntimeError("LLM_MODE=live requires APINEX_MODEL and APINEX_API_KEYS to be set")
    return OpenAICompatLLM(
        base_url=settings.apinex_base_url,
        model=settings.apinex_model.strip(),
        pool=pool,
        timeout_s=settings.apinex_timeout_s,
        max_attempts=settings.apinex_max_attempts,
        json_style=settings.apinex_json_mode,
        limiter=RateLimiter(settings.apinex_max_rpm),
    )


def build_providers(settings: Settings) -> Providers:
    if settings.provider_mode != "mock":
        # Never fall back to mocks silently: live STT/TTS/embedding adapters come later.
        raise NotImplementedError(
            "PROVIDER_MODE=live has no STT/TTS/embedding adapters yet; refusing to fall back to mocks"
        )
    return Providers(
        llm=_build_llm(settings),
        stt=MockSTT(),
        tts=MockTTS(),
        embeddings=MockEmbeddings(),
    )