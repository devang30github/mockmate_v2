import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

PROTOCOL_VERSION = 1
MAX_CONTROL_BYTES = 4096


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]


# ---- client -> server (JSON text frames) -------------------------------------
# Audio travels as binary frames (16 kHz, mono, 16-bit little-endian PCM) between
# utterance.start and utterance.end. It never goes inside JSON.
class Hello(_Msg):
    type: Literal["hello"]
    last_event_id: int = Field(default=0, ge=0)  # replay everything after this id


class UtteranceStart(_Msg):
    type: Literal["utterance.start"]
    utterance_id: uuid.UUID  # idempotency key: re-sending the same id is harmless


class UtteranceEnd(_Msg):
    type: Literal["utterance.end"]
    utterance_id: uuid.UUID
    reason: Literal["vad", "manual", "max_length"]

class UtterancePause(_Msg):
    type: Literal["utterance.pause"]
    utterance_id: uuid.UUID  # the speaker paused: a good place to cut a chunk

class TtsDone(_Msg):
    type: Literal["tts.done"]
    question_id: uuid.UUID

class Start(_Msg):
    type: Literal["start"]

class Pause(_Msg):
    type: Literal["pause"]


class Unpause(_Msg):
    type: Literal["unpause"]


class Finish(_Msg):
    type: Literal["finish"]


class Ping(_Msg):
    type: Literal["ping"]

ClientMessage = Annotated[
    Hello
    | Start
    | UtteranceStart
    | UtterancePause
    | UtteranceEnd
    | TtsDone
    | Pause
    | Unpause
    | Finish
    | Ping,
    Field(discriminator="type"),
]
_client_adapter = TypeAdapter(ClientMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    if len(raw) > MAX_CONTROL_BYTES:
        raise ProtocolError("too_large", "control message too large")
    try:
        return _client_adapter.validate_json(raw)
    except ValidationError:
        raise ProtocolError("bad_message", "invalid or unsupported message") from None


# ---- server -> client ----------------------------------------------------------
SERVER_TYPES = frozenset(
    {
        "server.hello",  # session info right after connecting
        "state",  # {state, state_version}
        "question.text",  # {question_id, seq, text}; the audio follows in binary frames
        "tts.start",  # {question_id, mime}; binary audio frame(s) follow
        "tts.end",  # {question_id}
        "transcript.final",  # {answer_id, utterance_id, seq, text}
        "transcript.partial",  # {utterance_id, committed, live}: live captions, not replayed
        "answer.ack",  # {utterance_id, duplicate}
        "error",  # {code, message, retryable}
        "pong",
    }
)
# Only these are stored with an event_id, so a reconnecting client can replay them.
PERSISTED_TYPES = frozenset({"state", "question.text", "transcript.final"})


def server_message(type: str, event_id: int | None = None, **fields) -> dict:
    if type not in SERVER_TYPES:
        raise ValueError(f"unknown server message type: {type}")
    if (type in PERSISTED_TYPES) != (event_id is not None):
        raise ValueError(f"{type}: event_id is required for persisted types and only for those")
    msg: dict = {
        "v": PROTOCOL_VERSION,
        "type": type,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if event_id is not None:
        msg["event_id"] = event_id
    msg.update(fields)
    return msg