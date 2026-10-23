from dataclasses import dataclass

from app.config import Settings
from app.credentials.pool import CredentialPool
from app.providers.base import EmbeddingProvider, LLMProvider, STTProvider, TTSProvider
from app.providers.caption_sherpa import SherpaCaptionEngine
from app.providers.llm_openai_compat import OpenAICompatLLM
from app.providers.mock.providers import MockEmbeddings, MockLLM, MockSTT, MockTTS
from app.providers.ratelimit import RateLimiter
from app.providers.stt_faster_whisper import FasterWhisperSTT


@dataclass
class Providers:
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    embeddings: EmbeddingProvider
    captions: SherpaCaptionEngine | None = None  # live captions while speaking (optional)


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


def _build_stt(settings: Settings) -> STTProvider:
    if settings.stt_mode == "mock":
        return MockSTT()
    return FasterWhisperSTT(
        model=settings.stt_model,
        compute_type=settings.stt_compute_type,
        cpu_threads=settings.stt_cpu_threads,
        beam_size=settings.stt_beam_size,
        language=settings.stt_language,
        vad_filter=settings.stt_vad_filter,
    )


def _build_captions(settings: Settings) -> SherpaCaptionEngine | None:
    if settings.stream_asr_mode == "off":
        return None
    return SherpaCaptionEngine(
        model_dir=settings.stream_asr_path, num_threads=settings.stream_asr_threads
    )


def build_providers(settings: Settings) -> Providers:
    if settings.provider_mode != "mock":
        # Never fall back to mocks silently: live TTS/embedding adapters come later.
        raise NotImplementedError(
            "PROVIDER_MODE=live has no TTS/embedding adapters yet; refusing to fall back to mocks"
        )
    return Providers(
        llm=_build_llm(settings),
        stt=_build_stt(settings),
        tts=MockTTS(),
        embeddings=MockEmbeddings(),
        captions=_build_captions(settings),
    )