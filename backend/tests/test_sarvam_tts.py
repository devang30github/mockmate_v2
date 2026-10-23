import base64
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.credentials.pool import CredentialPool
from app.providers.errors import BadRequest, PaymentRequired, RateLimited, TransientError
from app.providers.mock.providers import MockTTS
from app.providers.registry import build_providers
from app.providers.sarvam_tts import MAX_CHARS, SarvamTTS, prepare_text

pytestmark = pytest.mark.anyio

WAV = b"RIFF" + b"\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


async def no_sleep(_seconds):
    return None


class Script:
    def __init__(self, *items):
        self.items = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(audio: bytes = WAV):
    return httpx.Response(
        200, json={"request_id": "r1", "audios": [base64.b64encode(audio).decode()]}
    )


def err(status: int, code: str | None = None, headers: dict | None = None):
    body = {"error": {"request_id": None, "message": "m", "code": code}} if code else {"nope": 1}
    return httpx.Response(status, json=body, headers=headers or {})


def make_tts(script, keys="key-aaaa-1111,key-bbbb-2222", **kwargs):
    pool = CredentialPool.from_csv("sarvam", keys)
    tts = SarvamTTS(pool=pool, transport=httpx.MockTransport(script), sleep=no_sleep, **kwargs)
    return tts, pool


# ---- text preparation ---------------------------------------------------------------
def test_big_numbers_get_commas_and_other_numbers_are_left_alone():
    text = "We handle 10000 requests in 2026 for 1,250,000 users on v3.14159"
    assert prepare_text(text) == (
        "We handle 10,000 requests in 2026 for 1,250,000 users on v3.14159"
    )


def test_whitespace_is_normalized():
    assert prepare_text("  a \n  b\t c ") == "a b c"


# ---- the request and the response -----------------------------------------------------
async def test_success_returns_audio_and_sends_the_documented_request():
    script = Script(ok())
    tts, _ = make_tts(script, model="bulbul:v4-flash", speaker="simran_en_conversation")
    result = await tts.synthesize("Tell me about FastAPI.")
    assert result.audio == WAV and result.mime == "audio/wav"
    assert result.chars == len("Tell me about FastAPI.")
    request = script.requests[0]
    assert str(request.url) == "https://api.sarvam.ai/text-to-speech"
    assert request.headers["api-subscription-key"] == "key-aaaa-1111"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "bulbul:v4-flash"
    assert body["speaker"] == "simran_en_conversation"
    assert body["language_code"] == "en-IN"
    assert body["text"] == "Tell me about FastAPI."


async def test_voice_override_changes_the_speaker():
    script = Script(ok())
    tts, _ = make_tts(script)
    await tts.synthesize("Hello there.", voice="dev_en_conversational")
    assert json.loads(script.requests[0].content)["speaker"] == "dev_en_conversational"


async def test_mp3_codec_sets_the_mime_type():
    tts, _ = make_tts(Script(ok(b"ID3-not-a-wav")), codec="mp3")
    assert (await tts.synthesize("Hello there.")).mime == "audio/mpeg"


async def test_empty_and_overlong_text_are_rejected_without_a_request():
    script = Script()
    tts, _ = make_tts(script)
    with pytest.raises(BadRequest):
        await tts.synthesize("   ")
    with pytest.raises(BadRequest):
        await tts.synthesize("a" * (MAX_CHARS + 1))
    assert script.requests == []


# ---- the cache -----------------------------------------------------------------------------
async def test_repeated_text_is_served_from_the_cache():
    script = Script(ok())
    tts, _ = make_tts(script)
    first = await tts.synthesize("Same question?")
    second = await tts.synthesize("Same question?")
    assert first.audio == second.audio and second.latency_ms == 0.0
    assert len(script.requests) == 1


async def test_a_different_voice_is_not_a_cache_hit():
    script = Script(ok(), ok())
    tts, _ = make_tts(script)
    await tts.synthesize("Same question?")
    await tts.synthesize("Same question?", voice="dev_en_conversational")
    assert len(script.requests) == 2


async def test_cache_can_be_disabled():
    script = Script(ok(), ok())
    tts, _ = make_tts(script, cache_size=0)
    await tts.synthesize("Same question?")
    await tts.synthesize("Same question?")
    assert len(script.requests) == 2


# ---- failures and credential handling ------------------------------------------------------
async def test_rate_limit_rotates_to_the_next_key_and_cools_the_first():
    script = Script(err(429, "rate_limit_exceeded_error", {"retry-after": "7"}), ok())
    tts, pool = make_tts(script)
    await tts.synthesize("Hello there.")
    assert script.requests[1].headers["api-subscription-key"] == "key-bbbb-2222"
    assert pool.snapshot()[0]["cooling_down"] is True


async def test_rate_limit_without_a_header_still_cools_the_key():
    tts, pool = make_tts(Script(err(429, "rate_limit_exceeded_error"), ok()))
    await tts.synthesize("Hello there.")
    assert pool.snapshot()[0]["cooling_down"] is True


async def test_all_keys_rate_limited_raises_rate_limited():
    tts, _ = make_tts(Script(err(429, "rate_limit_exceeded_error"), err(429, "rate_limit_exceeded_error")))
    with pytest.raises(RateLimited):
        await tts.synthesize("Hello there.")


async def test_exhausted_credits_are_reported_and_the_key_is_parked():
    script = Script(err(429, "insufficient_quota_error"), err(429, "insufficient_quota_error"))
    tts, pool = make_tts(script)
    with pytest.raises(PaymentRequired):
        await tts.synthesize("Hello there.")
    assert all(k["cooling_down"] for k in pool.snapshot())
    assert len(script.requests) == 2


async def test_an_invalid_key_is_disabled_and_the_next_key_is_used():
    script = Script(err(403, "invalid_api_key_error"), ok())
    tts, pool = make_tts(script)
    await tts.synthesize("Hello there.")
    assert pool.snapshot()[0]["enabled"] is False
    assert script.requests[1].headers["api-subscription-key"] == "key-bbbb-2222"


async def test_other_403s_fail_at_once_and_keep_the_key():
    script = Script(err(403, "some_other_error"))
    tts, pool = make_tts(script)
    with pytest.raises(BadRequest):
        await tts.synthesize("Hello there.")
    assert len(script.requests) == 1 and pool.snapshot()[0]["enabled"] is True


@pytest.mark.parametrize("status", [400, 422])
async def test_request_errors_are_not_retried(status):
    script = Script(err(status, "invalid_request_error"))
    tts, _ = make_tts(script)
    with pytest.raises(BadRequest):
        await tts.synthesize("Hello there.")
    assert len(script.requests) == 1


async def test_server_errors_and_timeouts_are_retried_then_succeed():
    script = Script(err(500), httpx.ReadTimeout("t"), httpx.ConnectError("c"), ok())
    tts, _ = make_tts(script, max_attempts=4)
    assert (await tts.synthesize("Hello there.")).audio == WAV
    assert len(script.requests) == 4


async def test_attempts_are_bounded():
    script = Script(err(500), err(500), err(500))
    tts, _ = make_tts(script)
    with pytest.raises(TransientError):
        await tts.synthesize("Hello there.")
    assert len(script.requests) == 3


@pytest.mark.parametrize(
    "bad",
    [
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, json={"audios": []}),
        httpx.Response(200, json={"audios": ["@@@not-base64@@@"]}),
        httpx.Response(200, json={"audios": [base64.b64encode(b"not a wav").decode()]}),
    ],
)
async def test_malformed_audio_is_retried(bad):
    script = Script(bad, ok())
    tts, _ = make_tts(script)
    assert (await tts.synthesize("Hello there.")).audio == WAV
    assert len(script.requests) == 2


async def test_limiter_is_consulted_on_every_attempt():
    class Counter:
        n = 0

        async def acquire(self):
            self.n += 1

    counter = Counter()
    tts, _ = make_tts(Script(err(500), ok()), limiter=counter)
    await tts.synthesize("Hello there.")
    assert counter.n == 2


async def test_logs_never_contain_the_key_or_the_question_text(caplog):
    caplog.set_level(logging.INFO)
    tts, _ = make_tts(Script(err(403, "invalid_api_key_error"), ok()))
    await tts.synthesize("a very private question about Acme Corp")
    assert "key-aaaa" not in caplog.text and "key-bbbb" not in caplog.text
    assert "private question" not in caplog.text and "Acme" not in caplog.text


# ---- configuration -----------------------------------------------------------------------------
def test_mock_is_the_default_voice():
    s = Settings(app_env="dev", provider_mode="mock", tts_mode="mock")
    assert isinstance(build_providers(s).tts, MockTTS)


def test_sarvam_mode_requires_keys():
    s = Settings(app_env="dev", provider_mode="mock", tts_mode="sarvam", sarvam_api_keys="")
    with pytest.raises(RuntimeError):
        build_providers(s)


def test_sarvam_mode_builds_the_adapter():
    s = Settings(
        app_env="dev", provider_mode="mock", tts_mode="sarvam",
        sarvam_api_keys="k-1234,k-5678", sarvam_tts_speaker="dev_en_conversational",
    )
    tts = build_providers(s).tts
    assert isinstance(tts, SarvamTTS) and tts.speaker == "dev_en_conversational"