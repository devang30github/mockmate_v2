import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect
from app.providers.base import STTResult

from app.config import get_settings
from app.main import app
from tests.factories import SAMPLE_JD, make_pdf

from pathlib import Path
from app.providers.caption_sherpa import SherpaCaptionEngine
from tests.fakes import FakeRecognizer
from app.providers.errors import TransientError

PASSWORD = "a-decent-password-123"
DOMAIN = "@ws-test.mockmate.dev"
PCM_1S = b"\x00\x00" * 16000


# ---- database helpers --------------------------------------------------------------
def _run(coro_fn):
    async def go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            return await coro_fn(eng)
        finally:
            await eng.dispose()

    return asyncio.run(go())


def run_sql(sql: str, **params) -> None:
    async def fn(eng):
        async with eng.begin() as conn:
            await conn.execute(text(sql), params)

    _run(fn)


def scalar(sql: str, **params):
    async def fn(eng):
        async with eng.connect() as conn:
            return (await conn.execute(text(sql), params)).scalar()

    return _run(fn)


# ---- websocket helpers -------------------------------------------------------------
def send(ws, type_, **fields):
    ws.send_json({"v": 1, "type": type_, **fields})


def recv(ws):
    m = ws.receive()
    if m["type"] == "websocket.close":
        raise WebSocketDisconnect(m.get("code", 1000))
    if m.get("bytes") is not None:
        return m["bytes"]
    return json.loads(m["text"])


def collect_until(ws, predicate):
    out = []
    while True:
        m = recv(ws)
        out.append(m)
        if isinstance(m, dict) and predicate(m):
            return out


def collect(ws, stop_type):
    return collect_until(ws, lambda m: m["type"] == stop_type)


def types(msgs):
    return [m["type"] if isinstance(m, dict) else "<audio>" for m in msgs]


def speak(ws, utterance_id=None, pcm=PCM_1S):
    uid = utterance_id or str(uuid.uuid4())
    send(ws, "utterance.start", utterance_id=uid)
    for i in range(0, len(pcm), 6400):
        ws.send_bytes(pcm[i : i + 6400])
    send(ws, "utterance.end", utterance_id=uid, reason="manual")
    return uid


def connect(client, iid):
    name = get_settings().session_cookie_name
    cookie = f"{name}={client.cookies.get(name)}"
    return client.websocket_connect(f"/ws/interviews/{iid}", headers={"cookie": cookie})


def begin(ws):
    """hello, then start; returns (question.text message, all messages up to tts.end)."""
    send(ws, "hello")
    assert recv(ws)["type"] == "server.hello"
    send(ws, "start")
    msgs = collect(ws, "tts.end")
    question = next(m for m in msgs if isinstance(m, dict) and m["type"] == "question.text")
    return question, msgs


def reach_listening(ws):
    question, _ = begin(ws)
    send(ws, "tts.done", question_id=question["question_id"])
    assert recv(ws)["state"] == "LISTENING"
    return question


# ---- fixtures ------------------------------------------------------------------------
@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    run_sql("DELETE FROM users WHERE email LIKE :p", p=f"%{DOMAIN}")


def register(client):
    email = f"{uuid.uuid4().hex[:10]}{DOMAIN}"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201


@pytest.fixture
def iid(client):
    register(client)
    resume = client.post(
        "/api/v1/documents/resume", files={"file": ("r.pdf", make_pdf(), "application/pdf")}
    ).json()["id"]
    jd = client.post(
        "/api/v1/documents/job-description", json={"title": "ML", "text": SAMPLE_JD}
    ).json()["id"]
    interview_id = client.post(
        "/api/v1/interviews",
        json={
            "resume_document_id": resume,
            "jd_document_id": jd,
            "settings": {
                "role_title": "ML Engineer",
                "duration_minutes": 30,
                "difficulty": "medium",
                "interview_type": "technical",
            },
        },
    ).json()["id"]
    assert client.post(f"/api/v1/interviews/{interview_id}/plan").status_code == 200
    return interview_id


# ---- tests -----------------------------------------------------------------------------
def test_unauthenticated_socket_is_rejected(client):
    with client.websocket_connect(f"/ws/interviews/{uuid.uuid4()}") as ws:
        assert recv(ws)["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as exc:
            recv(ws)
        assert exc.value.code == 4401


def test_other_users_interview_is_not_found(client, iid):
    client.cookies.clear()
    register(client)
    with connect(client, iid) as ws:
        assert recv(ws)["code"] == "not_found"
        with pytest.raises(WebSocketDisconnect) as exc:
            recv(ws)
        assert exc.value.code == 4404


def test_hello_reports_ready_with_no_question(client, iid):
    with connect(client, iid) as ws:
        send(ws, "hello")
        hello = recv(ws)
    assert hello["type"] == "server.hello"
    assert hello["state"] == "READY" and hello["protocol"] == 1
    assert hello["current_question"] is None


def test_start_asks_the_opening_question_with_audio(client, iid):
    opening = client.get(f"/api/v1/interviews/{iid}/plan").json()["plan"]["opening_question"]
    with connect(client, iid) as ws:
        _, msgs = begin(ws)
    assert types(msgs) == ["state", "question.text", "tts.start", "<audio>", "tts.end"]
    assert msgs[0]["state"] == "ASKING" and msgs[0]["event_id"] > 0
    assert msgs[1]["seq"] == 1 and msgs[1]["text"] == opening
    assert msgs[2]["mime"] == "audio/wav"
    assert msgs[3][:4] == b"RIFF"


def test_tts_done_opens_listening(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
    assert scalar("SELECT state FROM interview_sessions WHERE id=:i", i=uuid.UUID(iid)) == "LISTENING"


def test_full_turn_persists_answer_and_asks_next_question(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        speak(ws)
        msgs = collect(ws, "tts.end")
    assert types(msgs) == [
        "state", "transcript.partial", "state", "transcript.final", "answer.ack",
        "state", "question.text", "tts.start", "<audio>", "tts.end",
    ]
    partial = next(m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.partial")
    assert partial["committed"] == "mock transcript of 1.0 seconds of audio"
    assert partial["live"] == ""
    
    states = [m["state"] for m in msgs if isinstance(m, dict) and m["type"] == "state"]
    assert states == ["TRANSCRIBING", "PROCESSING", "ASKING"]
    final = next(m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.final")
    assert final["text"].startswith("mock transcript of 1.0 seconds") and final["seq"] == 1
    ack = next(m for m in msgs if isinstance(m, dict) and m["type"] == "answer.ack")
    assert ack["duplicate"] is False
    nxt = next(m for m in msgs if isinstance(m, dict) and m["type"] == "question.text")
    assert nxt["seq"] == 2
    assert scalar("SELECT count(*) FROM interview_answers WHERE interview_id=:i", i=uuid.UUID(iid)) == 1


def test_resent_utterance_is_acknowledged_as_duplicate_and_not_stored_twice(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        uid = speak(ws)
        collect(ws, "tts.end")
        speak(ws, utterance_id=uid)
        ack = recv(ws)
    assert ack["type"] == "answer.ack" and ack["duplicate"] is True
    assert scalar("SELECT count(*) FROM interview_answers WHERE interview_id=:i", i=uuid.UUID(iid)) == 1


def test_too_short_audio_returns_to_listening_without_an_answer(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        speak(ws, pcm=b"\x00\x00" * 50)
        msgs = collect(ws, "error")
    assert types(msgs) == ["state", "state", "error"]
    assert [m["state"] for m in msgs[:2]] == ["TRANSCRIBING", "LISTENING"]
    assert msgs[2]["code"] == "no_speech" and msgs[2]["retryable"] is True
    assert scalar("SELECT count(*) FROM interview_answers WHERE interview_id=:i", i=uuid.UUID(iid)) == 0


def test_speaking_while_the_question_plays_is_refused(client, iid):
    with connect(client, iid) as ws:
        begin(ws)
        send(ws, "utterance.start", utterance_id=str(uuid.uuid4()))
        assert recv(ws)["code"] == "not_listening"


def test_pause_then_unpause_replays_the_question(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        send(ws, "pause")
        assert recv(ws)["state"] == "PAUSED"
        send(ws, "unpause")
        msgs = collect(ws, "tts.end")
    assert types(msgs) == ["state", "tts.start", "<audio>", "tts.end"]
    assert msgs[0]["state"] == "ASKING"


def test_finish_moves_to_finishing_and_stops_accepting_audio(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        send(ws, "finish")
        assert recv(ws)["state"] == "FINISHING"
        send(ws, "utterance.start", utterance_id=str(uuid.uuid4()))
        assert recv(ws)["code"] == "not_listening"


def test_reconnect_replays_events_and_the_current_question(client, iid):
    with connect(client, iid) as ws:
        begin(ws)
    with connect(client, iid) as ws:
        send(ws, "hello", last_event_id=0)
        msgs = collect(ws, "tts.end")
    assert types(msgs) == [
        "server.hello", "state", "question.text", "tts.start", "<audio>", "tts.end",
    ]
    assert msgs[0]["state"] == "ASKING" and msgs[0]["current_question"]["seq"] == 1
    last_id = max(m["event_id"] for m in msgs if isinstance(m, dict) and "event_id" in m)
    with connect(client, iid) as ws:
        send(ws, "hello", last_event_id=last_id)
        msgs = collect(ws, "tts.end")
    assert types(msgs) == ["server.hello", "tts.start", "<audio>", "tts.end"]


def test_answer_lost_in_transcription_is_recovered_on_reconnect(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
    run_sql("UPDATE interview_sessions SET state='TRANSCRIBING' WHERE id=:i", i=uuid.UUID(iid))
    with connect(client, iid) as ws:
        send(ws, "hello")
        msgs = collect(ws, "error")
    assert msgs[0]["type"] == "server.hello" and msgs[0]["state"] == "LISTENING"
    assert msgs[-1]["code"] == "answer_lost"


def test_interrupted_question_generation_is_finished_on_reconnect(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        speak(ws)
        collect(ws, "tts.end")
    # simulate a crash after the answer was saved but before question 2 existed
    run_sql("DELETE FROM interview_questions WHERE interview_id=:i AND seq=2", i=uuid.UUID(iid))
    run_sql("UPDATE interview_sessions SET state='PROCESSING' WHERE id=:i", i=uuid.UUID(iid))
    with connect(client, iid) as ws:
        send(ws, "hello")
        msgs = collect(ws, "tts.end")
    assert msgs[0]["state"] == "PROCESSING"
    questions = [m for m in msgs if isinstance(m, dict) and m["type"] == "question.text"]
    assert questions[-1]["seq"] == 2
    assert scalar("SELECT count(*) FROM interview_questions WHERE interview_id=:i", i=uuid.UUID(iid)) == 2


def test_whole_interview_runs_to_finishing(client, iid):
    with connect(client, iid) as ws:
        question, _ = begin(ws)
        qid, asked = question["question_id"], 1
        for _ in range(15):
            send(ws, "tts.done", question_id=qid)
            assert recv(ws)["state"] == "LISTENING"
            speak(ws)
            out = collect_until(
                ws,
                lambda m: m["type"] == "tts.end"
                or (m["type"] == "state" and m["state"] == "FINISHING"),
            )
            if out[-1]["type"] == "state":
                break
            qid = [m for m in out if isinstance(m, dict) and m["type"] == "question.text"][-1][
                "question_id"
            ]
            asked += 1
        else:
            pytest.fail("interview never finished")
    assert asked == 9
    i = uuid.UUID(iid)
    assert scalar("SELECT state FROM interview_sessions WHERE id=:i", i=i) == "FINISHING"
    assert scalar("SELECT count(*) FROM interview_answers WHERE interview_id=:i", i=i) == 9

    

class RecordingSTT:
    def __init__(self):
        self.hints = []

    async def transcribe(self, pcm16, sample_rate=16000, hints=None):
        self.hints.append(hints)
        return STTResult(text="a recorded answer", duration_s=len(pcm16) / 32000)


def test_resume_and_job_terms_reach_speech_recognition(client, iid):
    stt = RecordingSTT()
    client.app.state.providers.stt = stt
    with connect(client, iid) as ws:
        reach_listening(ws)
        speak(ws)
        collect(ws, "tts.end")
    assert stt.hints and "FastAPI" in stt.hints[0]

def test_pause_marker_streams_a_partial_and_the_chunks_are_joined(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        uid = str(uuid.uuid4())
        send(ws, "utterance.start", utterance_id=uid)
        for _ in range(10):  # 10 x 0.2 s = 2 s
            ws.send_bytes(b"\x00\x00" * 3200)
        send(ws, "utterance.pause", utterance_id=uid)
        for _ in range(5):  # 1 s more
            ws.send_bytes(b"\x00\x00" * 3200)
        send(ws, "utterance.end", utterance_id=uid, reason="manual")
        msgs = collect(ws, "answer.ack")
    partials = [m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.partial"]
    assert any(p["committed"].startswith("mock transcript of 2.0") for p in partials)
    final = next(m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.final")
    assert final["text"] == (
        "mock transcript of 2.0 seconds of audio mock transcript of 1.0 seconds of audio"
    )
    ack = msgs[-1]
    assert ack["duplicate"] is False and isinstance(ack["stt_ms"], int)


def test_a_pause_marker_for_no_utterance_is_ignored(client, iid):
    with connect(client, iid) as ws:
        reach_listening(ws)
        send(ws, "utterance.pause", utterance_id=str(uuid.uuid4()))
        send(ws, "ping")
        assert recv(ws)["type"] == "pong"

def test_live_captions_stream_to_the_browser_and_replace_whisper_previews(client, iid):
    engine = SherpaCaptionEngine(
        model_dir=Path("unused"), recognizer_factory=lambda d, t: FakeRecognizer()
    )
    engine.load()
    client.app.state.providers.captions = engine
    with connect(client, iid) as ws:
        reach_listening(ws)
        uid = str(uuid.uuid4())
        send(ws, "utterance.start", utterance_id=uid)
        for _ in range(5):
            ws.send_bytes(b"\x00\x00" * 3200)  # five pieces of 0.2 s
        first = collect(ws, "transcript.partial")[-1]
        assert first["live"].startswith("Word0") and first["committed"] == ""
        send(ws, "utterance.end", utterance_id=uid, reason="manual")
        msgs = collect(ws, "tts.end")
    engine.executor.shutdown(wait=False)
    partials = [m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.partial"]
    assert all(p["committed"] == "" for p in partials)  # Whisper's commits are not shown
    final = next(m for m in msgs if isinstance(m, dict) and m["type"] == "transcript.final")
    assert final["text"].startswith("mock transcript of")  # the saved text still comes from STT

    class FailingTTS:
    async def synthesize(self, text, *, voice=None):
        raise TransientError("provider down")


def test_failed_question_audio_can_be_retried_with_hello(client, iid):
    working = client.app.state.providers.tts
    client.app.state.providers.tts = FailingTTS()
    with connect(client, iid) as ws:
        send(ws, "hello")
        assert recv(ws)["type"] == "server.hello"
        send(ws, "start")
        msgs = collect(ws, "error")
        assert types(msgs) == ["state", "question.text", "error"]
        assert msgs[-1]["code"] == "tts_failed" and msgs[-1]["retryable"] is True
        client.app.state.providers.tts = working
        send(ws, "hello", last_event_id=msgs[1]["event_id"])
        again = collect(ws, "tts.end")
    assert types(again) == ["server.hello", "tts.start", "<audio>", "tts.end"]
    assert again[0]["state"] == "ASKING"