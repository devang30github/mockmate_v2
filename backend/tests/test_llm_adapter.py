import json

import httpx
import pytest

from app.config import Settings
from app.credentials.pool import CredentialPool
from app.providers.base import LLMMessage
from app.providers.errors import (
    AuthFailed,
    BadRequest,
    NoCredentialAvailable,
    RateLimited,
    TransientError,
    PaymentRequired,
)
from app.providers.llm_openai_compat import OpenAICompatLLM
from app.providers.registry import build_providers

pytestmark = pytest.mark.anyio

MSGS = [LLMMessage("system", "s"), LLMMessage("user", "hi")]
OK_BODY = {
    "model": "m",
    "choices": [{"message": {"content": "hello"}}],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "completion_tokens_details": {"reasoning_tokens": 5},
    },
}


async def no_sleep(_seconds):
    return None


class Script:
    """MockTransport handler that replays queued responses/exceptions and records requests."""

    def __init__(self, *items):
        self.items = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok():
    return httpx.Response(200, json=OK_BODY)


def status(code, headers=None, body=None):
    return httpx.Response(code, json=body or {"error": "x"}, headers=headers or {})


def sse(*chunks):
    lines = ['data: {"choices":[{"delta":{"role":"assistant"}}]}']
    for c in chunks:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}))
    lines.append("data: [DONE]")
    return httpx.Response(
        200,
        content=("\n\n".join(lines) + "\n\n").encode(),
        headers={"content-type": "text/event-stream"},
    )


def make_llm(script, keys="key-aaaa-1111,key-bbbb-2222", **kwargs):
    pool = CredentialPool.from_csv("apinex", keys)
    llm = OpenAICompatLLM(
        base_url="https://x.test/v1",
        model="test-model",
        pool=pool,
        transport=httpx.MockTransport(script),
        sleep=no_sleep,
        **kwargs,
    )
    return llm, pool


async def test_success_parses_text_and_usage():
    script = Script(ok())
    llm, _ = make_llm(script)
    result = await llm.complete(MSGS)
    assert result.text == "hello"
    assert (result.prompt_tokens, result.completion_tokens, result.reasoning_tokens) == (10, 7, 5)
    request = script.requests[0]
    assert request.headers["authorization"] == "Bearer key-aaaa-1111"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert "response_format" not in body


async def test_native_json_style_sends_response_format():
    script = Script(ok())
    llm, _ = make_llm(script, json_style="native")
    await llm.complete(MSGS, json_mode=True)
    assert json.loads(script.requests[0].content)["response_format"] == {"type": "json_object"}


async def test_rate_limit_rotates_to_next_key_and_cools_the_first():
    script = Script(status(429, {"retry-after": "30"}), ok())
    llm, pool = make_llm(script)
    result = await llm.complete(MSGS)
    assert result.text == "hello"
    assert script.requests[0].headers["authorization"] == "Bearer key-aaaa-1111"
    assert script.requests[1].headers["authorization"] == "Bearer key-bbbb-2222"
    assert pool.snapshot()[0]["cooling_down"] is True


async def test_auth_failure_disables_key_and_rotates():
    script = Script(status(401), ok())
    llm, pool = make_llm(script)
    await llm.complete(MSGS)
    assert pool.snapshot()[0]["enabled"] is False
    assert script.requests[1].headers["authorization"] == "Bearer key-bbbb-2222"


async def test_bad_request_is_not_retried():
    script = Script(status(400))
    llm, _ = make_llm(script)
    with pytest.raises(BadRequest):
        await llm.complete(MSGS)
    assert len(script.requests) == 1


async def test_server_error_is_retried_then_succeeds():
    script = Script(status(500), ok())
    llm, _ = make_llm(script)
    assert (await llm.complete(MSGS)).text == "hello"
    assert len(script.requests) == 2


async def test_timeout_and_connection_errors_are_transient():
    script = Script(httpx.ReadTimeout("t"), httpx.ConnectError("c"), ok())
    llm, _ = make_llm(script)
    assert (await llm.complete(MSGS)).text == "hello"
    assert len(script.requests) == 3


async def test_attempts_are_bounded():
    script = Script(status(500), status(500), status(500))
    llm, _ = make_llm(script)
    with pytest.raises(TransientError):
        await llm.complete(MSGS)
    assert len(script.requests) == 3


async def test_all_keys_rate_limited_raises_rate_limited():
    script = Script(status(429), status(429))
    llm, _ = make_llm(script)
    with pytest.raises(RateLimited):
        await llm.complete(MSGS)
    assert len(script.requests) == 2


async def test_no_usable_credentials_raises_without_any_request():
    script = Script()
    llm, pool = make_llm(script)
    pool.disable("apinex_key_01")
    pool.disable("apinex_key_02")
    with pytest.raises(NoCredentialAvailable):
        await llm.complete(MSGS)
    assert script.requests == []


async def test_empty_completion_is_retried():
    empty = httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    script = Script(empty, ok())
    llm, _ = make_llm(script)
    assert (await llm.complete(MSGS)).text == "hello"
    assert len(script.requests) == 2


async def test_malformed_response_fails_after_bounded_retries():
    bad = lambda: httpx.Response(200, json={"nope": 1})  # noqa: E731
    script = Script(bad(), bad(), bad())
    llm, _ = make_llm(script)
    with pytest.raises(TransientError):
        await llm.complete(MSGS)
    assert len(script.requests) == 3


async def test_error_text_never_contains_a_key():
    echo_a = status(401, body={"error": "invalid key key-aaaa-1111"})
    echo_b = status(401, body={"error": "invalid key key-bbbb-2222"})
    llm, _ = make_llm(Script(echo_a, echo_b))
    with pytest.raises(AuthFailed) as exc:
        await llm.complete(MSGS)
    assert "key-aaaa" not in str(exc.value)
    assert "key-bbbb" not in str(exc.value)


async def test_stream_yields_text_chunks_and_skips_role_only_deltas():
    llm, _ = make_llm(Script(sse("Hel", "lo")))
    assert [c async for c in llm.stream(MSGS)] == ["Hel", "lo"]


async def test_stream_rate_limit_before_first_chunk_rotates_key():
    script = Script(status(429), sse("Hi"))
    llm, _ = make_llm(script)
    assert [c async for c in llm.stream(MSGS)] == ["Hi"]
    assert len(script.requests) == 2


def test_live_llm_requires_model_and_keys():
    s = Settings(
        app_env="dev", provider_mode="mock", llm_mode="live", apinex_model="", apinex_api_keys=""
    )
    with pytest.raises(RuntimeError):
        build_providers(s)


def test_live_llm_builds_from_config():
    s = Settings(
        app_env="dev",
        provider_mode="mock",
        llm_mode="live",
        apinex_model="some-model",
        apinex_api_keys="k-1234,k-5678",
    )
    assert isinstance(build_providers(s).llm, OpenAICompatLLM)

async def test_payment_required_cools_key_and_rotates():
    script = Script(status(402), ok())
    llm, pool = make_llm(script)
    assert (await llm.complete(MSGS)).text == "hello"
    assert pool.snapshot()[0]["cooling_down"] is True
    assert script.requests[1].headers["authorization"] == "Bearer key-bbbb-2222"


async def test_payment_required_on_every_key_raises():
    llm, _ = make_llm(Script(status(402), status(402)))
    with pytest.raises(PaymentRequired):
        await llm.complete(MSGS)


async def test_limiter_is_consulted_on_every_attempt():
    class Counter:
        n = 0

        async def acquire(self):
            self.n += 1

    counter = Counter()
    llm, _ = make_llm(Script(status(500), ok()), limiter=counter)
    await llm.complete(MSGS)
    assert counter.n == 2