import asyncio

import pytest

from app.providers.base import STTResult
from app.providers.errors import ProviderError, TransientError
from app.ws.streaming import UtteranceStreamer

pytestmark = pytest.mark.anyio

MAX_BYTES = 120 * 32000


class FakeSTT:
    def __init__(self, texts=None, delays=None, fail_on=None):
        self.calls: list[int] = []  # bytes handed to each decode
        self._texts = list(texts or [])
        self._delays = list(delays or [])
        self._fail_on = set(fail_on or [])
        self._n = 0

    async def transcribe(self, pcm16, sample_rate=16000, hints=None):
        i = self._n
        self._n += 1
        self.calls.append(len(pcm16))
        if i < len(self._delays) and self._delays[i]:
            await asyncio.sleep(self._delays[i])
        if i in self._fail_on:
            raise TransientError("fail")
        text = self._texts[i] if i < len(self._texts) else f"t{i}"
        return STTResult(text=text, duration_s=len(pcm16) / 32000)


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, str]] = []

    async def __call__(self, committed, live):
        self.events.append((committed, live))


def pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(16000 * seconds)


def make(stt, rec, **kwargs):
    kwargs.setdefault("preview_interval_s", 0)  # previews off unless a test turns them on
    return UtteranceStreamer(stt, ["FastAPI"], rec, max_bytes=MAX_BYTES, **kwargs)


async def test_a_single_chunk_when_there_is_no_pause():
    stt, rec = FakeSTT(["hello world"]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(3.0))
    assert await s.finish() == "hello world"
    assert stt.calls == [96000]
    await s.aclose()


async def test_a_pause_commits_a_chunk_while_speech_continues():
    stt, rec = FakeSTT(["first part", "second part"]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    await asyncio.sleep(0.05)
    assert rec.events == [("first part", "")]
    s.feed(pcm(1.0))
    assert await s.finish() == "first part second part"
    assert stt.calls == [64000, 32000]
    assert rec.events[-1] == ("first part second part", "")
    await s.aclose()


async def test_a_pause_with_too_little_audio_is_ignored():
    stt, rec = FakeSTT(), Recorder()
    s = make(stt, rec)
    s.feed(pcm(0.5))
    s.pause_hint()
    await asyncio.sleep(0.02)
    assert stt.calls == []
    s.feed(pcm(1.0))
    await s.finish()
    assert stt.calls == [48000]
    await s.aclose()


async def test_long_speech_is_cut_automatically():
    stt, rec = FakeSTT(), Recorder()
    s = make(stt, rec, max_chunk_s=2.0)
    for _ in range(50):  # 5 seconds in 100 ms pieces
        s.feed(pcm(0.1))
    await s.finish()
    assert stt.calls == [64000, 64000, 32000]
    await s.aclose()


async def test_chunks_are_joined_in_order():
    stt, rec = FakeSTT(["A", "B"], delays=[0.1, 0.0]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    s.feed(pcm(2.0))
    s.pause_hint()
    assert await s.finish() == "A B"
    assert [e[0] for e in rec.events] == ["A", "A B"]
    await s.aclose()


async def test_an_early_failure_fails_the_whole_answer():
    stt, rec = FakeSTT(fail_on={0}), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    s.feed(pcm(1.0))
    with pytest.raises(ProviderError):
        await s.finish()
    await s.aclose()


async def test_empty_chunks_are_not_joined():
    stt, rec = FakeSTT(["", "second"]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    s.feed(pcm(1.0))
    assert await s.finish() == "second"
    await s.aclose()


async def test_a_tiny_tail_is_not_decoded():
    stt, rec = FakeSTT(["t0"]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    await asyncio.sleep(0.02)
    s.feed(pcm(0.1))
    assert await s.finish() == "t0"
    assert stt.calls == [64000]
    await s.aclose()


async def test_disabled_streaming_decodes_everything_once_at_the_end():
    stt, rec = FakeSTT(), Recorder()
    s = make(stt, rec, enabled=False)
    s.feed(pcm(2.0))
    s.pause_hint()
    s.feed(pcm(2.0))
    await s.finish()
    assert stt.calls == [128000]
    await s.aclose()


async def test_live_preview_appears_during_speech_and_stops_after_finish():
    stt, rec = FakeSTT(["live words", "final words"]), Recorder()
    s = make(stt, rec, preview_interval_s=0.02, preview_min_new_s=0.5)
    s.start()
    s.feed(pcm(1.0))
    await asyncio.sleep(0.15)
    assert ("", "live words") in rec.events
    assert await s.finish() == "final words"
    count = len(rec.events)
    await asyncio.sleep(0.1)
    assert len(rec.events) == count
    await s.aclose()


async def test_aclose_cancels_pending_work_quietly():
    stt, rec = FakeSTT(["x"], delays=[5.0]), Recorder()
    s = make(stt, rec)
    s.feed(pcm(2.0))
    s.pause_hint()
    await asyncio.sleep(0.02)
    await asyncio.wait_for(s.aclose(), timeout=1.0)