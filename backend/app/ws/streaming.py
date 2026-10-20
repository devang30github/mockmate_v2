import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.providers.base import STTProvider
from app.providers.errors import ProviderError, TransientError

log = logging.getLogger("mockmate.stream")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2

Emit = Callable[[str, str], Awaitable[None]]  # (committed text, live preview text)


class UtteranceStreamer:
    """Transcribes one spoken answer while it is still being spoken.

    Audio is cut into chunks at pauses (or after max_chunk_s). Each chunk is decoded in
    the background, in order, so only the last chunk is left when the speaker stops.
    A best-effort preview of the chunk in progress is decoded now and then. The final
    text is the chunk texts joined; nothing is re-decoded as a whole.
    """

    def __init__(
        self,
        stt: STTProvider,
        hints: list[str],
        emit: Emit,
        *,
        max_bytes: int,
        enabled: bool = True,
        min_chunk_s: float = 1.0,
        max_chunk_s: float = 12.0,
        min_tail_s: float = 0.3,
        preview_interval_s: float = 1.5,
        preview_min_new_s: float = 0.8,
    ):
        self._stt = stt
        self._hints = hints
        self._emit = emit
        self._max_bytes = max_bytes
        self._enabled = enabled
        self._min_chunk = int(min_chunk_s * BYTES_PER_SECOND)
        self._max_chunk = int(max_chunk_s * BYTES_PER_SECOND)
        self._min_tail = int(min_tail_s * BYTES_PER_SECOND)
        self._preview_interval = preview_interval_s
        self._preview_min_new = int(preview_min_new_s * BYTES_PER_SECOND)

        self.buf = bytearray()
        self._cut = 0  # bytes before this offset belong to chunks already handed off
        self._gen = 0  # increases at every cut, so stale previews can be recognised
        self._texts: list[str] = []
        self._tail: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self._extra: list[asyncio.Task] = []
        self._loop_task: asyncio.Task | None = None
        self._preview: asyncio.Task | None = None
        self._preview_mark = 0
        self._closed = False

    @property
    def total_bytes(self) -> int:
        return len(self.buf)

    def start(self) -> None:
        if self._enabled and self._preview_interval > 0:
            self._loop_task = asyncio.create_task(self._preview_loop())
            self._extra.append(self._loop_task)

    def feed(self, data: bytes) -> None:
        if self._closed:
            return
        room_left = self._max_bytes - len(self.buf)
        if room_left <= 0:
            return
        self.buf += data[:room_left]
        if self._enabled and len(self.buf) - self._cut >= self._max_chunk:
            self._cut_chunk()

    def pause_hint(self) -> None:
        """The browser heard a pause. Cut here if there is enough audio to be worth decoding."""
        if self._closed or not self._enabled:
            return
        if len(self.buf) - self._cut >= self._min_chunk:
            self._cut_chunk()

    async def finish(self) -> str:
        """Decode whatever is left, wait for every chunk, and return the full text.

        Raises ProviderError if any chunk could not be transcribed."""
        self._stop_background()
        if len(self.buf) - self._cut > 0:
            self._cut_chunk()
        if self._tail is not None:
            await self._tail
        return self._committed()

    async def aclose(self) -> None:
        self._closed = True
        self._stop_background()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        pending = [*self._tasks, *self._extra]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ---- internals -------------------------------------------------------------
    def _committed(self) -> str:
        return " ".join(t for t in self._texts if t)

    def _cut_chunk(self) -> None:
        end = len(self.buf) - len(self.buf) % 2  # whole 16-bit samples only
        chunk = bytes(self.buf[self._cut : end])
        self._cut = end
        self._gen += 1
        self._cancel_preview()
        index = len(self._texts)
        self._texts.append("")
        task = asyncio.create_task(self._decode_chunk(index, chunk, self._tail))
        self._tail = task
        self._tasks.append(task)

    async def _decode_chunk(self, index: int, chunk: bytes, prev: asyncio.Task | None) -> None:
        if prev is not None:
            await prev  # keeps results in order and re-raises an earlier failure
        self._texts[index] = await self._transcribe(chunk)
        await self._emit_safe(self._committed(), "")

    async def _transcribe(self, pcm: bytes) -> str:
        if len(pcm) < self._min_tail:
            return ""
        try:
            result = await self._stt.transcribe(pcm, SAMPLE_RATE, hints=self._hints)
        except ProviderError:
            raise
        except Exception:
            log.exception("chunk transcription failed")
            raise TransientError("transcription failed") from None
        return result.text.strip()

    async def _emit_safe(self, committed: str, live: str) -> None:
        try:
            await self._emit(committed, live)
        except Exception:  # captions are best-effort; the client may be gone
            log.debug("caption not sent")

    async def _preview_loop(self) -> None:
        while True:
            await asyncio.sleep(self._preview_interval)
            if self._closed:
                return
            new_bytes = len(self.buf) - self._preview_mark
            tail_bytes = len(self.buf) - self._cut
            if new_bytes < self._preview_min_new or tail_bytes < self._min_tail:
                continue
            if self._tail is not None and not self._tail.done():
                continue  # finished chunks take priority over previews
            if self._preview is not None and not self._preview.done():
                continue
            self._preview_mark = len(self.buf)
            pcm = bytes(self.buf[self._cut :])
            self._preview = asyncio.create_task(self._run_preview(pcm, self._gen))
            self._extra.append(self._preview)

    async def _run_preview(self, pcm: bytes, gen: int) -> None:
        try:
            text = await self._transcribe(pcm)
        except ProviderError:
            return  # a failed preview is simply not shown
        if text and not self._closed and gen == self._gen:
            await self._emit_safe(self._committed(), text)

    def _cancel_preview(self) -> None:
        if self._preview is not None and not self._preview.done():
            self._preview.cancel()

    def _stop_background(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
        self._cancel_preview()