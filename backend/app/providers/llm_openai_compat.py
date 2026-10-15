import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.credentials.pool import Credential, CredentialPool
from app.providers.base import LLMMessage, LLMResult
from app.providers.errors import (
    AuthFailed,
    BadRequest,
    NoCredentialAvailable,
    ProviderError,
    RateLimited,
    TransientError,
    PaymentRequired,
)

log = logging.getLogger("mockmate.llm")

MAX_COOLDOWN_S = 120.0
PAYMENT_COOLDOWN_S = 1800.0

def parse_cooldown(headers: httpx.Headers) -> float | None:
    """Seconds to wait, from Retry-After or the epoch-seconds x-ratelimit-reset header."""
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), MAX_COOLDOWN_S)
        except ValueError:
            pass  # HTTP-date form: fall through
    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            return min(max(float(reset) - time.time(), 1.0), MAX_COOLDOWN_S)
        except ValueError:
            pass
    return None


def classify(status: int, headers: httpx.Headers) -> ProviderError:
    if status == 402:
        return PaymentRequired("provider requires payment, quota, or check-in")
    if status == 429:
        return RateLimited(parse_cooldown(headers))
    if status in (401, 403):
        return AuthFailed(f"credential rejected (HTTP {status})")
    if status in (408, 425) or status >= 500:
        return TransientError(f"provider error (HTTP {status})")
    return BadRequest(f"request rejected (HTTP {status})")


class OpenAICompatLLM:
    """OpenAI-compatible chat adapter that leases a key per attempt from a CredentialPool.

    Retry rules: 429 -> cool that key down and try another; 401/403 -> disable that key
    and try another; 5xx/timeout/malformed -> back off and retry; other 4xx -> fail now.
    Total attempts are bounded by max_attempts.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        pool: CredentialPool,
        timeout_s: float = 60.0,
        max_attempts: int = 3,
        json_style: str = "prompt",
        limiter=None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):

        self.model = model
        self._pool = pool
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._max_attempts = max(1, max_attempts)
        self._native_json = json_style == "native"
        self._sleep = sleep
        self._limiter = limiter
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0), transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- internals ----------------------------------------------------------
    def _payload(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool,
        max_tokens: int | None,
        temperature: float | None,
        stream: bool,
    ) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode and self._native_json:
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream"] = True
        return body

    @staticmethod
    def _headers(cred: Credential) -> dict:
        return {"Authorization": f"Bearer {cred.secret}"}

    @staticmethod
    def _log_http_error(cred: Credential, resp: httpx.Response) -> None:
        log.warning(
            "llm http error key=%s status=%d body=%s", cred.id, resp.status_code, resp.text[:200]
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.5 * 2 ** (attempt - 1), 4.0) * random.uniform(0.5, 1.5)

    async def _with_retries(self, op: Callable[[Credential], Awaitable]):
        excluded: set[str] = set()
        last_exc: ProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                cred = await self._pool.lease(exclude=frozenset(excluded))
            except NoCredentialAvailable:
                if last_exc is not None:
                    raise last_exc
                raise
            try:
                return await op(cred)
            except RateLimited as e:
                log.warning(
                    "llm rate limited key=%s retry_after=%s attempt=%d",
                    cred.id, e.retry_after, attempt,
                )
                self._pool.mark_rate_limited(cred.id, e.retry_after)
                excluded.add(cred.id)
                last_exc = e
            except AuthFailed as e:
                log.error("llm credential disabled after auth failure key=%s", cred.id)
                self._pool.disable(cred.id)
                excluded.add(cred.id)
                last_exc = e

            except PaymentRequired as e:
                log.error("llm payment/quota required key=%s", cred.id)
                self._pool.mark_rate_limited(cred.id, PAYMENT_COOLDOWN_S)
                excluded.add(cred.id)
                last_exc = e

            except TransientError as e:
                log.warning(
                    "llm transient error key=%s attempt=%d error=%s", cred.id, attempt, e
                )
                last_exc = e
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff(attempt))
        assert last_exc is not None
        raise last_exc

    def _parse(self, resp: httpx.Response, cred: Credential, latency_ms: float) -> LLMResult:
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise TransientError("malformed provider response") from None
        if not text or not text.strip():
            raise TransientError("empty completion")
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        log.info(
            "llm ok key=%s model=%s latency_ms=%.0f prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
            cred.id,
            self.model,
            latency_ms,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            details.get("reasoning_tokens"),
        )
        return LLMResult(
            text=text,
            model=data.get("model") or self.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
            latency_ms=latency_ms,
        )

    # --- public API ---------------------------------------------------------
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        body = self._payload(
            messages,
            json_mode=json_mode,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )

        async def op(cred: Credential) -> LLMResult:
            if self._limiter is not None:
                await self._limiter.acquire()
            t0 = time.perf_counter()
            try:
                resp = await self._client.post(self._url, json=body, headers=self._headers(cred))
            except httpx.TimeoutException:
                raise TransientError("provider timeout") from None
            except httpx.TransportError:
                raise TransientError("provider connection error") from None
            latency_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code != 200:
                self._log_http_error(cred, resp)
                raise classify(resp.status_code, resp.headers)
            return self._parse(resp, cred, latency_ms)

        return await self._with_retries(op)

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        body = self._payload(
            messages, json_mode=False, max_tokens=max_tokens, temperature=temperature, stream=True
        )

        async def open_stream(cred: Credential) -> httpx.Response:
            request = self._client.build_request(
                "POST", self._url, json=body, headers=self._headers(cred)
            )
            try:
                resp = await self._client.send(request, stream=True)
            except httpx.TimeoutException:
                raise TransientError("provider timeout") from None
            except httpx.TransportError:
                raise TransientError("provider connection error") from None
            if resp.status_code != 200:
                await resp.aread()
                await resp.aclose()
                self._log_http_error(cred, resp)
                raise classify(resp.status_code, resp.headers)
            return resp

        # Retries only happen before the first byte; once text is flowing, a failure
        # surfaces to the caller so we never emit duplicated text.
        resp = await self._with_retries(open_stream)
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    continue
                if delta:
                    yield delta
        except httpx.TransportError:
            raise TransientError("stream interrupted") from None
        finally:
            await resp.aclose()