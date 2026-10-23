import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("mockmate.captions")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
_DIGITS = re.compile(r"(\d+)")
_LONE_I = re.compile(r"\bi\b")

Emit = Callable[[str], Awaitable[None]]


class CaptionModelError(RuntimeError):
    """The streaming caption model is missing or could not be loaded."""


def tidy_caption(text: str) -> str:
    """The model writes ALL CAPS with no punctuation; make it readable."""
    text = " ".join(text.lower().split())
    if not text:
        return ""
    text = _LONE_I.sub("I", text)
    return text[0].upper() + text[1:]


def _natural_key(path: Path) -> list:
    # split() with a capture group alternates text, number, text, number, ...
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(path.name)]


def resolve_transducer_files(model_dir: Path) -> dict[str, str]:
    """Find tokens/encoder/decoder/joiner in a model folder.

    Prefers int8 encoder+joiner (with a plain decoder), and among several variants the one
    with the largest numbers in its name (more left context). Falls back to fp32."""
    hint = "Run: bash scripts/fetch_stream_model.sh"
    if not model_dir.is_dir():
        raise CaptionModelError(f"Streaming model folder not found: {model_dir}. {hint}")
    tokens = model_dir / "tokens.txt"
    if not tokens.is_file():
        raise CaptionModelError(f"tokens.txt is missing in {model_dir}. {hint}")

    int8 = sorted(model_dir.glob("encoder*.int8.onnx"), key=_natural_key, reverse=True)
    plain = sorted(
        (p for p in model_dir.glob("encoder*.onnx") if not p.name.endswith(".int8.onnx")),
        key=_natural_key,
        reverse=True,
    )
    for encoder in int8 + plain:
        rest = encoder.name[len("encoder") :]
        is_int8 = rest.endswith(".int8.onnx")
        stem = rest[: -len(".int8.onnx")] if is_int8 else rest[: -len(".onnx")]
        decoder = model_dir / f"decoder{stem}.onnx"
        if not decoder.is_file():
            decoder = model_dir / f"decoder{stem}.int8.onnx"
        joiner = model_dir / (f"joiner{stem}.int8.onnx" if is_int8 else f"joiner{stem}.onnx")
        if decoder.is_file() and joiner.is_file():
            return {
                "tokens": str(tokens),
                "encoder": str(encoder),
                "decoder": str(decoder),
                "joiner": str(joiner),
            }
    raise CaptionModelError(f"No matching encoder/decoder/joiner files in {model_dir}. {hint}")


def _default_factory(model_dir: Path, num_threads: int):
    files = resolve_transducer_files(model_dir)
    try:
        import sherpa_onnx  # lazy: heavy import, and tests use a fake
    except ImportError:
        raise CaptionModelError(
            "The sherpa-onnx package is not installed. Run: uv pip install -r requirements.txt"
        ) from None
    log.info(
        "caption model files: %s",
        ", ".join(Path(files[k]).name for k in ("encoder", "decoder", "joiner")),
    )
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=files["tokens"],
        encoder=files["encoder"],
        decoder=files["decoder"],
        joiner=files["joiner"],
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


class SherpaCaptionEngine:
    """One shared streaming recognizer. All decoding runs on a single worker thread, so the
    event loop is never blocked and concurrent streams are processed one after another."""

    def __init__(
        self,
        *,
        model_dir: Path,
        num_threads: int = 2,
        recognizer_factory: Callable[[Path, int], Any] | None = None,
    ):
        self.model_dir = Path(model_dir)
        self._threads = num_threads
        self._factory = recognizer_factory or _default_factory
        self._recognizer: Any = None
        self._load_lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="captions")

    @property
    def model_name(self) -> str:
        return f"sherpa-onnx:{self.model_dir.name}"

    def load(self) -> None:
        """Blocking. Safe to call more than once."""
        if self._recognizer is not None:
            return
        started = time.perf_counter()
        try:
            self._recognizer = self._factory(self.model_dir, self._threads)
        except CaptionModelError:
            raise
        except Exception:
            log.exception("caption model load failed")
            raise CaptionModelError(
                "Could not load the streaming caption model (see the log above)."
            ) from None
        log.info("caption model loaded in %.1fs", time.perf_counter() - started)

    async def warmup(self) -> None:
        async with self._load_lock:
            if self._recognizer is None:
                await asyncio.to_thread(self.load)

    def open_stream(self, emit: Emit) -> "CaptionStream":
        if self._recognizer is None:
            raise CaptionModelError("The caption model is not loaded.")
        return CaptionStream(self, emit)

    # ---- called from the worker thread ----------------------------------------------
    def _new_stream(self):
        return self._recognizer.create_stream()

    def process(self, stream, pcm: bytes) -> str:
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32) / 32768.0)
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return tidy_caption(self._recognizer.get_result(stream))

    async def aclose(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class CaptionStream:
    """Live captions for one spoken answer. feed() never blocks; if the model falls behind,
    waiting audio is merged into one batch so captions catch up instead of lagging."""

    def __init__(self, engine: SherpaCaptionEngine, emit: Emit):
        self._engine = engine
        self._emit = emit
        self._stream = engine._new_stream()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._last = ""
        self._closed = False
        self._audio_bytes = 0
        self._busy_s = 0.0
        self._max_batch_s = 0.0
        self._task = asyncio.create_task(self._run())

    def feed(self, pcm: bytes) -> None:
        if not self._closed:
            self._audio_bytes += len(pcm)
            self._queue.put_nowait(pcm)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await self._queue.get()
                while not self._queue.empty():
                    data += self._queue.get_nowait()
                started = time.perf_counter()
                text = await loop.run_in_executor(
                    self._engine.executor, self._engine.process, self._stream, data
                )
                took = time.perf_counter() - started
                self._busy_s += took
                self._max_batch_s = max(self._max_batch_s, took)
                if text != self._last:
                    self._last = text
                    await self._safe_emit(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live captions stopped after an error")
            self._closed = True

    async def _safe_emit(self, text: str) -> None:
        try:
            await self._emit(text)
        except Exception:  # captions are best-effort; the client may be gone
            log.debug("caption not sent")

    async def aclose(self) -> None:
        if self._closed and self._task.done():
            return
        self._closed = True
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        if self._audio_bytes:
            log.info(
                "captions closed audio_s=%.1f busy_ms=%.0f max_batch_ms=%.0f",
                self._audio_bytes / BYTES_PER_SECOND,
                self._busy_s * 1000,
                self._max_batch_s * 1000,
            )
            self._audio_bytes = 0