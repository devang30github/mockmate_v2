import io
import json
import math
import wave

import pytest

from app.config import Settings
from app.providers.base import LLMMessage
from app.providers.mock.providers import (
    MOCK_LLM_TEXT,
    MockEmbeddings,
    MockLLM,
    MockSTT,
    MockTTS,
)
from app.providers.registry import build_providers

pytestmark = pytest.mark.anyio


async def test_mock_tts_returns_valid_wav():
    result = await MockTTS().synthesize("Tell me about yourself.")
    assert result.mime == "audio/wav"
    with wave.open(io.BytesIO(result.audio)) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 22050
        assert w.getnframes() > 0


async def test_mock_stt_reports_duration():
    pcm = b"\x00\x00" * 16000 * 3  # 3 seconds at 16 kHz
    result = await MockSTT().transcribe(pcm, 16000)
    assert result.duration_s == pytest.approx(3.0)
    assert "3.0 seconds" in result.text


async def test_mock_embeddings_deterministic_unit_vectors():
    emb = MockEmbeddings()
    a1, a2, b = await emb.embed(["alpha", "alpha", "beta"])
    assert len(a1) == emb.dim == 384
    assert a1 == a2 and a1 != b
    assert math.sqrt(sum(v * v for v in a1)) == pytest.approx(1.0)


async def test_mock_llm_json_mode_is_parseable():
    result = await MockLLM().complete([LLMMessage("user", "hello")], json_mode=True)
    assert json.loads(result.text)["mock"] is True


async def test_mock_llm_stream_reassembles_to_full_text():
    chunks = [c async for c in MockLLM().stream([LLMMessage("user", "hi")])]
    assert "".join(chunks) == MOCK_LLM_TEXT


def test_live_mode_refuses_to_fall_back_to_mocks():
    settings = Settings(app_env="dev", provider_mode="live")
    with pytest.raises(NotImplementedError):
        build_providers(settings)