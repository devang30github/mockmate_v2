import logging
import time
import uuid
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import InterviewAnswer, InterviewQuestion, InterviewSession
from app.db.session import open_session
from app.domain.question_queue import QueuedQuestion
from app.domain.session_state import Event, InvalidTransition, accepts_audio
from app.providers.errors import ProviderError
from app.providers.registry import Providers
from app.services.events import append_event, events_after
from app.services.session_state import StaleState, advance
from app.ws.protocol import (
    PROTOCOL_VERSION,
    Finish,
    Hello,
    Pause,
    Ping,
    ProtocolError,
    Start,
    TtsDone,
    Unpause,
    UtteranceEnd,
    UtterancePause,
    UtteranceStart,
    parse_client_message,
    server_message,
)
from app.ws.streaming import UtteranceStreamer

log = logging.getLogger("mockmate.room")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
MAX_UTTERANCE_BYTES = 120 * BYTES_PER_SECOND
MIN_UTTERANCE_BYTES = int(0.3 * BYTES_PER_SECOND)

Emit = tuple[str, int, dict]  # (message type, event_id, fields)

async def _discard(committed: str, live: str) -> None:
    """Whisper's phrase-by-phrase captions are not shown while the streaming model captions."""

class RoomError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable



@dataclass
class _Utterance:
    id: uuid.UUID
    streamer: UtteranceStreamer | None = None  # None marks a re-sent, already-stored utterance
    captions: object | None = None  # live caption stream, when streaming captions are on

class Room:
    """One WebSocket connection driving one interview.

    The database is the source of truth; this object only holds the answer currently
    being spoken. Messages are handled one at a time, in order.
    """

    def __init__(
        self,
        ws: WebSocket,
        interview_id: uuid.UUID,
        providers: Providers,
        queue: list[QueuedQuestion],
        stt_hints: list[str] | None = None,
    ):
        self.ws = ws
        self.interview_id = interview_id
        self.providers = providers
        self.queue = queue
        self.stt_hints = stt_hints or []
        self.utt: _Utterance | None = None
        self._warned_no_utterance = False

    # ---- sending ----------------------------------------------------------------
    async def send(self, type_: str, event_id: int | None = None, **fields) -> None:
        await self.ws.send_json(server_message(type_, event_id, **fields))

    async def _emit(self, emits: list[Emit]) -> None:
        for type_, event_id, fields in emits:
            await self.send(type_, event_id, **fields)

    async def _error(self, code: str, message: str, retryable: bool = False) -> None:
        await self.send("error", code=code, message=message, retryable=retryable)

    async def _send_partial(self, utterance_id: str, committed: str, live: str) -> None:
        try:
            await self.send(
                "transcript.partial", utterance_id=utterance_id, committed=committed, live=live
            )
        except Exception:  # the client may have gone away; captions are best-effort
            log.debug("partial caption not sent")

    async def _deliver(self, question_id: uuid.UUID, text: str) -> None:
        try:
            tts = await self.providers.tts.synthesize(text)
        except ProviderError:
            log.warning("tts failed")
            await self._error(
                "tts_failed", "Couldn't generate the question audio. Please retry.", True
            )
            return
        await self.send("tts.start", question_id=str(question_id), mime=tts.mime)
        await self.ws.send_bytes(tts.audio)
        await self.send("tts.end", question_id=str(question_id))

    # ---- database helpers (all take the caller's session) ---------------------
    async def _advance(self, s: AsyncSession, event: Event) -> tuple[str, Emit]:
        state, version = await advance(s, self.interview_id, event)
        payload = {"state": state, "state_version": version}
        event_id = await append_event(s, self.interview_id, "state", payload)
        return state, ("state", event_id, payload)

    async def _state(self, s: AsyncSession) -> tuple[str, int]:
        row = (
            await s.execute(
                select(InterviewSession.state, InterviewSession.state_version).where(
                    InterviewSession.id == self.interview_id
                )
            )
        ).one()
        return row[0], row[1]

    async def _current_question(self, s: AsyncSession) -> InterviewQuestion | None:
        return (
            await s.execute(
                select(InterviewQuestion)
                .where(InterviewQuestion.interview_id == self.interview_id)
                .order_by(InterviewQuestion.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _last_answer_seq(self, s: AsyncSession) -> int:
        value = (
            await s.execute(
                select(func.max(InterviewAnswer.seq)).where(
                    InterviewAnswer.interview_id == self.interview_id
                )
            )
        ).scalar()
        return value or 0

    async def _add_question(
        self, s: AsyncSession, seq: int, spec: QueuedQuestion
    ) -> tuple[InterviewQuestion, Emit]:
        row = InterviewQuestion(
            interview_id=self.interview_id,
            seq=seq,
            kind=spec.kind,
            body=spec.text,
            competency=spec.competency,
        )
        s.add(row)
        await s.flush()
        payload = {"question_id": str(row.id), "seq": seq, "text": spec.text}
        event_id = await append_event(s, self.interview_id, "question.text", payload)
        return row, ("question.text", event_id, payload)

    async def _drop_utterance(self) -> None:
        u, self.utt = self.utt, None
        if u is not None and u.captions is not None:
            await u.captions.aclose()
        if u is not None and u.streamer is not None:
            await u.streamer.aclose()

    # ---- main loop ----------------------------------------------------------------
    async def run(self) -> None:
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is not None:
                    await self.handle_text(message["text"])
                elif message.get("bytes") is not None:
                    await self.on_audio(message["bytes"])
        finally:
            await self._drop_utterance()

    async def handle_text(self, raw: str) -> None:
        try:
            msg = parse_client_message(raw)
        except ProtocolError as e:
            await self._error(e.code, e.message)
            return
        try:
            if isinstance(msg, Hello):
                await self.on_hello(msg)
            elif isinstance(msg, Start):
                await self.on_start()
            elif isinstance(msg, TtsDone):
                await self.on_tts_done(msg)
            elif isinstance(msg, UtteranceStart):
                await self.on_utterance_start(msg)
            elif isinstance(msg, UtterancePause):
                await self.on_utterance_pause(msg)
            elif isinstance(msg, UtteranceEnd):
                await self.on_utterance_end(msg)
            elif isinstance(msg, Pause):
                await self.on_pause()
            elif isinstance(msg, Unpause):
                await self.on_unpause()
            elif isinstance(msg, Finish):
                await self.on_finish()
            elif isinstance(msg, Ping):
                await self.send("pong")
        except InvalidTransition as e:
            await self._error("invalid_state", str(e))
        except StaleState:
            await self._error("stale_state", "The interview changed. Please retry.", True)
        except RoomError as e:
            await self._error(e.code, e.message, e.retryable)
        except WebSocketDisconnect:
            raise
        except Exception:
            log.exception("room handler failed")
            await self._error("internal", "Something went wrong. Please retry.", True)

    # ---- handlers -------------------------------------------------------------------
    async def on_hello(self, msg: Hello) -> None:
        await self._drop_utterance()
        lost_answer = False
        async with open_session() as s:
            state, version = await self._state(s)
            if state == "TRANSCRIBING":
                # The connection dropped while the answer was being transcribed.
                await self._advance(s, Event.STT_FAILED)
                await s.commit()
                lost_answer = True
                state, version = await self._state(s)
            events = await events_after(s, self.interview_id, msg.last_event_id)
            current = await self._current_question(s)
            answered = await self._last_answer_seq(s) if state == "PROCESSING" else 0

        await self.send(
            "server.hello",
            interview_id=str(self.interview_id),
            protocol=PROTOCOL_VERSION,
            state=state,
            state_version=version,
            current_question=(
                {"question_id": str(current.id), "seq": current.seq, "text": current.body}
                if current
                else None
            ),
        )
        for e in events:
            await self.send(e.type, e.event_id, **e.payload)
        if lost_answer:
            await self._error(
                "answer_lost", "Your last answer didn't reach us. Please answer again.", True
            )
        if state == "ASKING" and current is not None:
            await self._deliver(current.id, current.body)
        elif state == "PROCESSING":
            if answered >= 1:
                await self._next_turn(answered)
            else:
                await self._error("internal", "Interview state is inconsistent.", False)

    async def on_start(self) -> None:
        async with open_session() as s:
            _, state_ev = await self._advance(s, Event.START)  # raises unless READY
            if not self.queue:
                raise RoomError("no_plan", "This interview has no plan.")
            row, question_ev = await self._add_question(s, 1, self.queue[0])
            await s.commit()
            question_id, text = row.id, row.body
        await self._emit([state_ev, question_ev])
        await self._deliver(question_id, text)

    async def on_tts_done(self, msg: TtsDone) -> None:
        async with open_session() as s:
            state, _ = await self._state(s)
            if state != "ASKING":
                return  # a late or duplicate acknowledgement is harmless
            current = await self._current_question(s)
            if current is None or current.id != msg.question_id:
                raise RoomError("stale_question", "That question is no longer current.")
            _, ev = await self._advance(s, Event.TTS_DONE)
            await s.commit()
        await self._emit([ev])

    async def on_utterance_start(self, msg: UtteranceStart) -> None:
        async with open_session() as s:
            known = (
                await s.execute(
                    select(InterviewAnswer.id).where(InterviewAnswer.utterance_id == msg.utterance_id)
                )
            ).scalar_one_or_none()
            state, _ = await self._state(s)
        await self._drop_utterance()
        if known is not None:  # a re-send of an answer we already have
            self.utt = _Utterance(msg.utterance_id)
            return
        if not accepts_audio(state):
            raise RoomError("not_listening", "Not listening for an answer right now.")

        settings = get_settings()
        uid = str(msg.utterance_id)
        engine = getattr(self.providers, "captions", None)

        async def emit_whisper(committed: str, live: str) -> None:
            await self._send_partial(uid, committed, live)

        async def emit_caption(text: str) -> None:
            await self._send_partial(uid, "", text)

        captions = engine.open_stream(emit_caption) if engine is not None else None
        streamer = UtteranceStreamer(
            self.providers.stt,
            self.stt_hints,
            _discard if captions is not None else emit_whisper,
            max_bytes=MAX_UTTERANCE_BYTES,
            enabled=settings.stt_live_captions,
            preview_interval_s=0 if captions is not None else settings.stt_preview_interval_s,
        )
        streamer.start()
        self.utt = _Utterance(msg.utterance_id, streamer, captions)
        self._warned_no_utterance = False

    async def on_utterance_pause(self, msg: UtterancePause) -> None:
        u = self.utt
        if u is not None and u.streamer is not None and u.id == msg.utterance_id:
            u.streamer.pause_hint()
        # a marker for anything else is stale and safely ignored

    async def on_audio(self, data: bytes) -> None:
        u = self.utt
        if u is None:
            if not self._warned_no_utterance:
                self._warned_no_utterance = True
                await self._error("no_utterance", "Audio received outside an utterance.")
            return
        if u.streamer is None:  # a duplicate utterance: its audio is ignored
            return
        u.streamer.feed(data)
        if u.captions is not None:
            u.captions.feed(data)

    async def on_utterance_end(self, msg: UtteranceEnd) -> None:
        u = self.utt
        if u is None or u.id != msg.utterance_id:
            raise RoomError("no_utterance", "No matching utterance in progress.")
        self.utt = None
        if u.streamer is None:
            await self.send("answer.ack", utterance_id=str(u.id), duplicate=True)
            return
        if u.captions is not None:
            await u.captions.aclose()  # the final transcript replaces the live caption
        try:
            await self._finish_utterance(u.id, u.streamer)
        finally:
            await u.streamer.aclose()

    async def _finish_utterance(self, utterance_id: uuid.UUID, streamer: UtteranceStreamer) -> None:
        async with open_session() as s:
            _, ev = await self._advance(s, Event.UTTERANCE_END)
            await s.commit()
        await self._emit([ev])

        total = streamer.total_bytes
        if total < MIN_UTTERANCE_BYTES:
            await self._return_to_listening(
                Event.STT_EMPTY, "no_speech", "I didn't hear anything. Please try again."
            )
            return

        t0 = time.perf_counter()
        try:
            transcript = (await streamer.finish()).strip()
        except ProviderError:
            log.warning("stt failed")
            await self._return_to_listening(
                Event.STT_FAILED, "stt_failed", "Transcription failed. Please try again."
            )
            return
        # time from the end of speech to the finished transcript (the end-of-speech wait is extra)
        stt_ms = int((time.perf_counter() - t0) * 1000)
        audio_ms = total * 1000 // BYTES_PER_SECOND
        log.info("answer transcribed audio_ms=%d post_speech_ms=%d", audio_ms, stt_ms)
        if not transcript:
            await self._return_to_listening(
                Event.STT_EMPTY, "no_speech", "I didn't hear anything. Please try again."
            )
            return

        async with open_session() as s:
            question = await self._current_question(s)
            if question is None:
                raise RoomError("internal", "No question is being answered.")
            answer = InterviewAnswer(
                interview_id=self.interview_id,
                question_id=question.id,
                seq=question.seq,
                utterance_id=utterance_id,
                body=transcript,
                audio_ms=audio_ms,
                stt_model=getattr(
                    self.providers.stt, "model_name", type(self.providers.stt).__name__
                ),
                stt_latency_ms=stt_ms,
            )
            s.add(answer)
            try:
                await s.flush()
            except IntegrityError:  # this answer was already recorded elsewhere
                await s.rollback()
                await self.send("answer.ack", utterance_id=str(utterance_id), duplicate=True)
                return
            _, state_ev = await self._advance(s, Event.STT_OK)
            payload = {
                "answer_id": str(answer.id),
                "utterance_id": str(utterance_id),
                "seq": question.seq,
                "text": transcript,
            }
            event_id = await append_event(s, self.interview_id, "transcript.final", payload)
            await s.commit()
            answered_seq = question.seq
        await self._emit([state_ev, ("transcript.final", event_id, payload)])
        await self.send(
            "answer.ack", utterance_id=str(utterance_id), duplicate=False, stt_ms=stt_ms
        )
        await self._next_turn(answered_seq)

    async def _return_to_listening(self, event: Event, code: str, message: str) -> None:
        async with open_session() as s:
            _, ev = await self._advance(s, event)
            await s.commit()
        await self._emit([ev])
        await self._error(code, message, True)

    async def _next_turn(self, answered_seq: int) -> None:
        """Ask question answered_seq + 1, or finish when the queue is exhausted."""
        if answered_seq >= len(self.queue):
            async with open_session() as s:
                _, ev = await self._advance(s, Event.FINISH)
                await s.commit()
            await self._emit([ev])
            return
        spec = self.queue[answered_seq]
        async with open_session() as s:
            try:
                _, state_ev = await self._advance(s, Event.QUESTION_READY)
                row, question_ev = await self._add_question(s, answered_seq + 1, spec)
                await s.commit()
            except IntegrityError:
                await s.rollback()
                raise StaleState() from None
            question_id, text = row.id, row.body
        await self._emit([state_ev, question_ev])
        await self._deliver(question_id, text)

    async def on_pause(self) -> None:
        await self._drop_utterance()
        async with open_session() as s:
            _, ev = await self._advance(s, Event.PAUSE)
            await s.commit()
        await self._emit([ev])

    async def on_unpause(self) -> None:
        async with open_session() as s:
            _, ev = await self._advance(s, Event.RESUME)
            current = await self._current_question(s)
            await s.commit()
        await self._emit([ev])
        if current is not None:
            await self._deliver(current.id, current.body)

    async def on_finish(self) -> None:
        await self._drop_utterance()
        async with open_session() as s:
            _, ev = await self._advance(s, Event.FINISH)
            await s.commit()
        await self._emit([ev])