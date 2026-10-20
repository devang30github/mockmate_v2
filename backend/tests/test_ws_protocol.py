import json
import uuid

import pytest

from app.ws.protocol import (
    Hello,
    ProtocolError,
    UtteranceEnd,
    UtteranceStart,
    parse_client_message,
    server_message,
    UtterancePause,
)


def msg(**fields):
    return json.dumps({"v": 1, **fields})


def test_hello_defaults_last_event_id_to_zero():
    parsed = parse_client_message(msg(type="hello"))
    assert isinstance(parsed, Hello) and parsed.last_event_id == 0


def test_utterance_messages_parse_with_uuid_ids():
    uid = str(uuid.uuid4())
    start = parse_client_message(msg(type="utterance.start", utterance_id=uid))
    end = parse_client_message(msg(type="utterance.end", utterance_id=uid, reason="manual"))
    assert isinstance(start, UtteranceStart) and isinstance(end, UtteranceEnd)
    assert str(end.utterance_id) == uid


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        msg(type="nope"),
        json.dumps({"type": "ping"}),  # missing version
        json.dumps({"v": 2, "type": "ping"}),  # wrong version
        msg(type="ping", surprise=1),  # extra field
        msg(type="utterance.start", utterance_id="not-a-uuid"),
        msg(type="utterance.end", utterance_id=str(uuid.uuid4()), reason="because"),
        msg(type="hello", last_event_id=-1),
    ],
)
def test_bad_messages_are_rejected(raw):
    with pytest.raises(ProtocolError) as exc:
        parse_client_message(raw)
    assert exc.value.code == "bad_message"


def test_oversized_control_message_is_rejected():
    with pytest.raises(ProtocolError) as exc:
        parse_client_message(msg(type="ping", pad="x" * 5000))
    assert exc.value.code == "too_large"


def test_server_message_has_version_type_timestamp():
    m = server_message("pong")
    assert m["v"] == 1 and m["type"] == "pong" and "ts" in m and "event_id" not in m


def test_persisted_messages_require_an_event_id():
    assert server_message("state", event_id=7, state="LISTENING")["event_id"] == 7
    with pytest.raises(ValueError):
        server_message("state", state="LISTENING")


def test_event_id_is_rejected_on_transient_messages():
    with pytest.raises(ValueError):
        server_message("pong", event_id=3)


def test_unknown_server_type_is_rejected():
    with pytest.raises(ValueError):
        server_message("made.up")

def test_start_message_parses():
    from app.ws.protocol import Start

    assert isinstance(parse_client_message(msg(type="start")), Start)

def test_utterance_pause_parses():
    uid = str(uuid.uuid4())
    parsed = parse_client_message(msg(type="utterance.pause", utterance_id=uid))
    assert isinstance(parsed, UtterancePause)


def test_partial_is_a_transient_server_message():
    m = server_message("transcript.partial", utterance_id="u", committed="a", live="b")
    assert "event_id" not in m and m["committed"] == "a"
    with pytest.raises(ValueError):
        server_message("transcript.partial", event_id=1, utterance_id="u", committed="", live="")