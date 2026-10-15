import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.main import app
from app.providers.base import LLMResult
from app.providers.mock.providers import MockLLM
from tests.factories import SAMPLE_JD, make_pdf

PASSWORD = "a-decent-password-123"
DOMAIN = "@plan-test.mockmate.dev"


class RecordingLLM:
    def __init__(self, bad_plan: bool = False):
        self.inner = MockLLM()
        self.bad_plan = bad_plan
        self.calls: list = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        is_plan = any(m.role == "system" and "TASK:plan_interview" in m.content for m in messages)
        if self.bad_plan and is_plan:
            return LLMResult(text="not json at all", model="mock-llm")
        return await self.inner.complete(messages, **kwargs)

    def stream(self, messages, **kwargs):
        return self.inner.stream(messages, **kwargs)


def run_sql(sql: str, **params) -> None:
    async def _go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        async with eng.begin() as conn:
            await conn.execute(text(sql), params)
        await eng.dispose()

    asyncio.run(_go())


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    run_sql("DELETE FROM users WHERE email LIKE :p", p=f"%{DOMAIN}")


def register(client):
    email = f"{uuid.uuid4().hex[:10]}{DOMAIN}"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201


def make_interview(client, duration=30, difficulty="medium"):
    resume = client.post(
        "/api/v1/documents/resume", files={"file": ("r.pdf", make_pdf(), "application/pdf")}
    ).json()["id"]
    jd = client.post(
        "/api/v1/documents/job-description", json={"title": "ML Engineer", "text": SAMPLE_JD}
    ).json()["id"]
    r = client.post(
        "/api/v1/interviews",
        json={
            "resume_document_id": resume,
            "jd_document_id": jd,
            "settings": {
                "role_title": "ML Engineer",
                "duration_minutes": duration,
                "difficulty": difficulty,
                "interview_type": "technical",
            },
        },
    )
    return r.json()["id"]


def use_llm(client, llm):
    client.app.state.providers.llm = llm


def test_plan_is_created_marks_ready_and_is_cached_on_repeat(client):
    register(client)
    iid = make_interview(client)
    llm = RecordingLLM()
    use_llm(client, llm)
    r = client.post(f"/api/v1/interviews/{iid}/plan")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert body["interview_state"] == "READY"
    assert body["version"] == 1
    assert body["plan"]["budget"]["total_questions"] == 9
    assert len(body["plan"]["sections"]) == 4
    assert len(llm.calls) == 2  # one analysis call, one planning call
    assert client.get(f"/api/v1/interviews/{iid}").json()["state"] == "READY"

    again = client.post(f"/api/v1/interviews/{iid}/plan")
    assert again.status_code == 200
    assert again.json()["cached"] is True
    assert len(llm.calls) == 2  # nothing new was sent to the model


def test_get_plan_is_404_until_one_exists(client):
    register(client)
    iid = make_interview(client)
    use_llm(client, RecordingLLM())
    assert client.get(f"/api/v1/interviews/{iid}/plan").status_code == 404
    client.post(f"/api/v1/interviews/{iid}/plan")
    got = client.get(f"/api/v1/interviews/{iid}/plan")
    assert got.status_code == 200
    assert got.json()["plan"]["opening_question"]


def test_plan_requires_auth(client):
    assert client.post(f"/api/v1/interviews/{uuid.uuid4()}/plan").status_code == 401


def test_other_users_interview_is_404(client):
    register(client)  # user A
    iid = make_interview(client)
    client.cookies.clear()
    register(client)  # user B
    assert client.post(f"/api/v1/interviews/{iid}/plan").status_code == 404
    assert client.get(f"/api/v1/interviews/{iid}/plan").status_code == 404


def test_malformed_plan_output_is_502_and_interview_stays_draft(client):
    register(client)
    iid = make_interview(client)
    llm = RecordingLLM(bad_plan=True)
    use_llm(client, llm)
    r = client.post(f"/api/v1/interviews/{iid}/plan")
    assert r.status_code == 502
    assert len(llm.calls) == 3  # analysis, plan, one repair attempt
    assert client.get(f"/api/v1/interviews/{iid}").json()["state"] == "DRAFT"
    assert client.get(f"/api/v1/interviews/{iid}/plan").status_code == 404


def test_started_interview_cannot_be_planned(client):
    register(client)
    iid = make_interview(client)
    use_llm(client, RecordingLLM())
    run_sql(
        "UPDATE interview_sessions SET state = 'ASKING' WHERE id = :i", i=uuid.UUID(iid)
    )
    assert client.post(f"/api/v1/interviews/{iid}/plan").status_code == 409


def test_plan_respects_duration_and_difficulty(client):
    register(client)
    iid = make_interview(client, duration=15, difficulty="easy")
    use_llm(client, RecordingLLM())
    body = client.post(f"/api/v1/interviews/{iid}/plan").json()
    assert body["plan"]["budget"]["total_questions"] == 6
    assert len(body["plan"]["sections"]) == 3  # canned plan has 5; the budget caps it