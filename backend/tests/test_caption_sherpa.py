import asyncio
from pathlib import Path

import pytest

from app.providers.caption_sherpa import (
    CaptionModelError,
    SherpaCaptionEngine,
    resolve_transducer_files,
    tidy_caption,
)
from tests.fakes import FakeRecognizer

pytestmark = pytest.mark.anyio


def pcm(samples: int) -> bytes:
    return b"\x00\x00" * samples


def touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"")


class Recorder:
    def __init__(self):
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)


def make_engine(recognizer=None):
    rec = recognizer or FakeRecognizer()
    calls = []

    def factory(model_dir, threads):
        calls.append((model_dir, threads))
        return rec

    return SherpaCaptionEngine(model_dir=Path("m"), num_threads=2, recognizer_factory=factory), rec, calls


# ---- text tidying ---------------------------------------------------------------------
def test_tidy_caption_makes_all_caps_readable():
    assert tidy_caption("HELLO I AM I'M") == "Hello I am I'm"
    assert tidy_caption("  ") == ""


# ---- finding model files ------------------------------------------------------------
def test_prefers_int8_encoder_and_joiner_with_a_plain_decoder(tmp_path):
    touch(
        tmp_path, "tokens.txt",
        "encoder-epoch-99-avg-1.int8.onnx", "encoder-epoch-99-avg-1.onnx",
        "decoder-epoch-99-avg-1.onnx", "decoder-epoch-99-avg-1.int8.onnx",
        "joiner-epoch-99-avg-1.int8.onnx", "joiner-epoch-99-avg-1.onnx",
    )
    files = resolve_transducer_files(tmp_path)
    assert Path(files["encoder"]).name == "encoder-epoch-99-avg-1.int8.onnx"
    assert Path(files["decoder"]).name == "decoder-epoch-99-avg-1.onnx"
    assert Path(files["joiner"]).name == "joiner-epoch-99-avg-1.int8.onnx"


def test_chunk_named_files_and_the_larger_left_context_wins(tmp_path):
    names = ["tokens.txt"]
    for left in ("64", "128"):
        stem = f"-epoch-99-avg-1-chunk-16-left-{left}"
        names += [f"encoder{stem}.int8.onnx", f"decoder{stem}.onnx", f"joiner{stem}.int8.onnx"]
    touch(tmp_path, *names)
    files = resolve_transducer_files(tmp_path)
    assert "left-128" in files["encoder"] and "left-128" in files["joiner"]


def test_falls_back_to_fp32_files(tmp_path):
    touch(tmp_path, "tokens.txt", "encoder-a.onnx", "decoder-a.onnx", "joiner-a.onnx")
    files = resolve_transducer_files(tmp_path)
    assert Path(files["encoder"]).name == "encoder-a.onnx"


def test_uses_an_int8_decoder_when_only_that_exists(tmp_path):
    touch(tmp_path, "tokens.txt", "encoder-a.int8.onnx", "decoder-a.int8.onnx", "joiner-a.int8.onnx")
    assert Path(resolve_transducer_files(tmp_path)["decoder"]).name == "decoder-a.int8.onnx"


def test_missing_folder_points_at_the_fetch_script(tmp_path):
    with pytest.raises(CaptionModelError, match="fetch_stream_model"):
        resolve_transducer_files(tmp_path / "nope")


def test_missing_tokens_is_reported(tmp_path):
    with pytest.raises(CaptionModelError, match="tokens.txt"):
        resolve_transducer_files(tmp_path)


def test_encoder_without_matching_joiner_is_rejected(tmp_path):
    touch(tmp_path, "tokens.txt", "encoder-a.int8.onnx", "decoder-a.onnx")
    with pytest.raises(CaptionModelError, match="No matching"):
        resolve_transducer_files(tmp_path)


# ---- the engine and its streams -----------------------------------------------------------
async def test_model_loads_once_even_with_concurrent_warmups():
    engine, _, calls = make_engine()
    await asyncio.gather(engine.warmup(), engine.warmup())
    assert len(calls) == 1
    await engine.aclose()


async def test_load_failure_becomes_a_caption_model_error():
    def broken(*_):
        raise OSError("disk on fire")

    engine = SherpaCaptionEngine(model_dir=Path("m"), recognizer_factory=broken)
    with pytest.raises(CaptionModelError):
        await engine.warmup()
    await engine.aclose()


async def test_open_stream_before_loading_is_an_error():
    engine, _, _ = make_engine()
    with pytest.raises(CaptionModelError):
        engine.open_stream(Recorder())
    await engine.aclose()


async def test_captions_grow_as_audio_arrives_and_unchanged_text_is_not_resent():
    engine, _, _ = make_engine()
    await engine.warmup()
    rec = Recorder()
    stream = engine.open_stream(rec)
    stream.feed(pcm(6400))
    await asyncio.sleep(0.1)
    stream.feed(pcm(100))  # not enough for a new word: text unchanged
    await asyncio.sleep(0.1)
    stream.feed(pcm(6300))
    await asyncio.sleep(0.1)
    assert rec.texts == ["Word0 word1", "Word0 word1 word2 word3"]
    await stream.aclose()
    await engine.aclose()


async def test_a_backlog_is_merged_into_one_batch():
    engine, recognizer, _ = make_engine()
    await engine.warmup()
    rec = Recorder()
    stream = engine.open_stream(rec)
    for _ in range(10):  # no awaits in between, so all ten are queued before the consumer runs
        stream.feed(pcm(1600))
    await asyncio.sleep(0.15)
    assert recognizer.streams[0].calls == 1
    assert rec.texts == ["Word0 word1 word2 word3 word4"]
    await stream.aclose()
    await engine.aclose()


async def test_nothing_is_emitted_after_close():
    engine, _, _ = make_engine()
    await engine.warmup()
    rec = Recorder()
    stream = engine.open_stream(rec)
    stream.feed(pcm(6400))
    await asyncio.sleep(0.1)
    await stream.aclose()
    count = len(rec.texts)
    stream.feed(pcm(6400))
    await asyncio.sleep(0.1)
    assert len(rec.texts) == count
    await engine.aclose()


async def test_a_decoding_error_silently_stops_captions():
    class Broken(FakeRecognizer):
        def decode_stream(self, s):
            raise RuntimeError("boom")

    engine, _, _ = make_engine(Broken())
    await engine.warmup()
    rec = Recorder()
    stream = engine.open_stream(rec)
    stream.feed(pcm(6400))
    await asyncio.sleep(0.1)
    stream.feed(pcm(6400))  # ignored, no exception
    await stream.aclose()
    assert rec.texts == []
    await engine.aclose()