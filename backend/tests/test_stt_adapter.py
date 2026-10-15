import asyncio
import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from app.domain.stt_hints import build_initial_prompt
from app.providers.errors import BadRequest, ProviderError
from app.providers.stt_faster_whisper import FasterWhisperSTT

pytestmark = pytest.mark.anyio


@dataclass
class Seg:
    text: str
    avg_logprob: float = -0.2
    no_speech_prob: float = 0.01


class FakeModel:
    def __init__(self, segments=None, fail=False, delay=0.0):
        self.segments = [Seg(" hello there ")] if segments is None else segments
        self.fail = fail
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def transcribe(self, audio, **kwargs):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((audio, kwargs))
            if self.fail:
                raise RuntimeError("boom")
            if self.delay:
                time.sleep(self.delay)
            return iter(self.segments), object()
        finally:
            with self._lock:
                self.active -= 1


def make_stt(model=None, **kwargs):
    model = model or FakeModel()
    factory_calls = []

    def factory(name, compute_type, cpu_threads):
        factory_calls.append((name, compute_type, cpu_threads))
        return model

    return FasterWhisperSTT(model_factory=factory, **kwargs), model, factory_calls


def pcm(seconds=1.0):
    return b"\x00\x00" * int(16000 * seconds)


async def test_segments_are_joined_and_timed():
    stt, _, _ = make_stt(FakeModel([Seg(" Hello "), Seg(" world.")]))
    result = await stt.transcribe(pcm(1.0))
    assert result.text == "Hello world."
    assert result.duration_s == pytest.approx(1.0)
    assert 0 < result.confidence <= 1
    assert result.latency_ms >= 0


async def test_pcm_becomes_normalized_float32():
    stt, model, _ = make_stt()
    await stt.transcribe(np.array([0, 16384, -32768], dtype="<i2").tobytes())
    audio = model.calls[0][0]
    assert audio.dtype == np.float32
    assert audio.tolist() == [0.0, 0.5, -1.0]


async def test_options_are_passed_to_the_model():
    stt, model, _ = make_stt(beam_size=3, language="en", vad_filter=False)
    await stt.transcribe(pcm())
    kwargs = model.calls[0][1]
    assert kwargs["beam_size"] == 3
    assert kwargs["language"] == "en"
    assert kwargs["vad_filter"] is False
    assert kwargs["initial_prompt"] is None


async def test_hints_become_an_initial_prompt():
    stt, model, _ = make_stt()
    await stt.transcribe(pcm(), hints=["FastAPI", "pgvector"])
    prompt = model.calls[0][1]["initial_prompt"]
    assert "FastAPI" in prompt and "pgvector" in prompt


async def test_probable_silence_segments_are_dropped():
    segments = [Seg("Thanks for watching", avg_logprob=-1.5, no_speech_prob=0.9), Seg("real answer")]
    stt, _, _ = make_stt(FakeModel(segments))
    assert (await stt.transcribe(pcm())).text == "real answer"


async def test_confident_quiet_speech_is_kept():
    stt, _, _ = make_stt(FakeModel([Seg("quiet answer", avg_logprob=-0.3, no_speech_prob=0.8)]))
    assert (await stt.transcribe(pcm())).text == "quiet answer"


async def test_no_segments_means_empty_text():
    stt, _, _ = make_stt(FakeModel([]))
    result = await stt.transcribe(pcm())
    assert result.text == "" and result.confidence is None


async def test_a_repeated_prompt_is_discarded():
    hints = ["PostgreSQL", "Docker", "Python", "FastAPI", "Kubernetes", "Terraform"]
    stt, _, _ = make_stt(FakeModel([Seg(build_initial_prompt(hints))]))
    assert (await stt.transcribe(pcm(), hints=hints)).text == ""


async def test_a_short_real_answer_matching_a_term_is_kept():
    stt, _, _ = make_stt(FakeModel([Seg("FastAPI")]))
    assert (await stt.transcribe(pcm(), hints=["FastAPI", "pgvector"])).text == "FastAPI"


async def test_wrong_sample_rate_is_rejected():
    stt, _, _ = make_stt()
    with pytest.raises(BadRequest):
        await stt.transcribe(pcm(), sample_rate=44100)


async def test_empty_audio_returns_empty_without_loading_the_model():
    stt, _, factory_calls = make_stt()
    assert (await stt.transcribe(b"")).text == ""
    assert factory_calls == []


async def test_decode_errors_become_provider_errors_without_leaking_details():
    stt, _, _ = make_stt(FakeModel(fail=True))
    with pytest.raises(ProviderError) as exc:
        await stt.transcribe(pcm())
    assert "boom" not in str(exc.value)


async def test_load_failure_is_a_provider_error():
    def broken(*_):
        raise OSError("download failed")

    stt = FasterWhisperSTT(model_factory=broken)
    with pytest.raises(ProviderError):
        await stt.transcribe(pcm())


async def test_model_loads_once_even_with_concurrent_calls():
    stt, _, factory_calls = make_stt()
    await asyncio.gather(stt.warmup(), stt.warmup(), stt.transcribe(pcm()))
    assert len(factory_calls) == 1


async def test_decodes_never_overlap():
    stt, model, _ = make_stt(FakeModel(delay=0.05))
    await asyncio.gather(*[stt.transcribe(pcm()) for _ in range(3)])
    assert model.max_active == 1


async def test_model_name_label():
    assert make_stt()[0].model_name == "faster-whisper:small"
    assert make_stt(model=None)[0].model_id == "small"


async def test_warmup_survives_a_failing_warm_decode():
    stt, _, factory_calls = make_stt(FakeModel(fail=True))
    await stt.warmup()  # must not raise: the model loaded, only the warm-up decode failed
    assert len(factory_calls) == 1