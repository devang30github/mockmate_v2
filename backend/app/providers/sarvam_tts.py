import asyncio
import base64
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx

from app.credentials.pool import Credential, CredentialPool
from app.credentials.retry import call_with_credentials
from app.providers.base import TTSResult
from app.providers.errors import (
    AuthFailed,
    BadRequest,
    PaymentRequired,
    ProviderError,
    RateLimited,
    TransientError,
)
from app.providers.llm_openai_compat import parse_cooldown

log = logging.getLogger("mockmate.tts")

MAX_CHARS = 2500
DEFAULT_RATE_LIMIT_COOLDOWN_S = 5.0
_MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "aac": "audio/aac", "flac": "audio/flac"}
_AUTH_CODES = {"invalid_api_key_error", "authentication_error"}
# Sarvam asks for commas in numbers over four digits so they are read as whole numbers.
_BIG_NUMBER = re.compile(r"(?<![\d,.])\d{5,}(?![\d,]|\.\d)")


def prepare_text(text: str) -> str:
    text = " ".join(text.split())
    return _BIG_NUMBER.sub(lambda m: f"{int(m.group()):,}", text)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        code = resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return code if isinstance(code, str) else None


def classify(status: int, headers: httpx.Headers, code: str | None) -> ProviderError:
    if status == 402:
        return PaymentRequired("provider credits exhausted")
    if status == 429:
        if code == "insufficient_quota_error":
            return PaymentRequired("provider credits or quota exhausted")
        return RateLimited(parse_cooldown(headers) or DEFAULT_RATE_LIMIT_COOLDOWN_S)
    if status == 403:
        if code in _AUTH_CODES:
            return AuthFailed("credential rejected")
        return BadRequest(f"request forbidden ({code or 'no code'})")
    if status in (408, 425) or status >= 500:
        return TransientError(f"provider error (HTTP {status})")
    return BadRequest(f"request rejected (HTTP {status})")


class SarvamTTS:
    """Sarvam Bulbul text-to-speech over REST, with credential failover and a small cache."""

    def __init__(
        self,
        *,
        pool: CredentialPool,
        base_url: str = "https://api.sarvam.ai",
        model: str = "bulbul:v4-flash",
        speaker: str = "simran_en_conversation",
        language_code: str = "en-IN",
        pace: float = 1.0,
        temperature: float = 0.6,
        sample_rate: int = 24000,
        codec: str = "wav",
        timeout_s: float = 30.0,
        max_attempts: int = 3,
        cache_size: int = 64,
        limiter=None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._pool = pool
        self._url = base_url.rstrip("/") + "/text-to-speech"
        self.model = model
        self.speaker = speaker
        self._language = language_code
        self._pace = pace
        self._temperature = temperature
        self._sample_rate = sample_rate
        self._codec = codec
        self._max_attempts = max_attempts
        self._cache_size = cache_size
        self._limiter = limiter
        self._sleep = sleep
        self._cache: OrderedDict[tuple, TTSResult] = OrderedDict()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0), transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _parse(self, resp: httpx.Response, cred: Credential, latency_ms: float, chars: int):
        try:
            data = resp.json()
            audio = base64.b64decode(data["audios"][0], validate=True)
            request_id = data.get("request_id")
        except (ValueError, KeyError, IndexError, TypeError):
            raise TransientError("malformed provider response") from None
        if not audio or (self._codec == "wav" and audio[:4] != b"RIFF"):
            raise TransientError("unexpected audio from provider")
        log.info(
            "tts ok key=%s model=%s chars=%d latency_ms=%.0f audio_bytes=%d request_id=%s",
            cred.id, self.model, chars, latency_ms, len(audio), request_id,
        )
        return TTSResult(
            audio=audio,
            mime=_MIME.get(self._codec, "application/octet-stream"),
            chars=chars,
            latency_ms=latency_ms,
        )

    async def synthesize(self, text: str, *, voice: str | None = None) -> TTSResult:
        prepared = prepare_text(text)
        if not prepared:
            raise BadRequest("nothing to say")
        if len(prepared) > MAX_CHARS:
            raise BadRequest(f"text is longer than {MAX_CHARS} characters")

        speaker = voice or self.speaker
        key = (self.model, speaker, self._pace, self._temperature, self._codec, prepared)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            log.info("tts cache hit chars=%d", len(prepared))
            return TTSResult(audio=cached.audio, mime=cached.mime, chars=cached.chars, latency_ms=0.0)

        payload = {
            "text": prepared,
            "language_code": self._language,
            "model": self.model,
            "speaker": speaker,
            "pace": self._pace,
            "temperature": self._temperature,
            "speech_sample_rate": self._sample_rate,
            "output_audio_codec": self._codec,
        }

        async def op(cred: Credential) -> TTSResult:
            if self._limiter is not None:
                await self._limiter.acquire()
            started = time.perf_counter()
            try:
                resp = await self._client.post(
                    self._url, json=payload, headers={"api-subscription-key": cred.secret}
                )
            except httpx.TimeoutException:
                raise TransientError("provider timeout") from None
            except httpx.TransportError:
                raise TransientError("provider connection error") from None
            latency_ms = (time.perf_counter() - started) * 1000
            if resp.status_code != 200:
                code = _error_code(resp)
                log.warning(
                    "tts http error key=%s status=%d code=%s", cred.id, resp.status_code, code
                )
                raise classify(resp.status_code, resp.headers, code)
            return self._parse(resp, cred, latency_ms, len(prepared))

        result = await call_with_credentials(
            self._pool, op, label="tts", max_attempts=self._max_attempts, sleep=self._sleep
        )
        if self._cache_size > 0:
            self._cache[key] = result
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result