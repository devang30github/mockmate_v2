import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.main import app
from tests.factories import SAMPLE_JD, make_pdf

PASSWORD = "a-decent-password-123"
DOMAIN = "@iv-test.mockmate.dev"
SETTINGS = {
    "role_title": "ML Engineer",
    "duration_minutes": 30,
    "difficulty": "medium",
    "interview_type": "technical",
}


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
    # cascades to documents and interview_sessions
    run_sql("DELETE FROM users WHERE email LIKE :p", p=f"%{DOMAIN}")


def register(client):
    email = f"{uuid.uuid4().hex[:10]}{DOMAIN}"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201


def make_docs(client):
    resume = client.post(
        "/api/v1/documents/resume", files={"file": ("r.pdf", make_pdf(), "application/pdf")}
    ).json()["id"]
    jd = client.post(
        "/api/v1/documents/job-description", json={"title": "ML Engineer", "text": SAMPLE_JD}
    ).json()["id"]
    return resume, jd


def payload(resume, jd, **overrides):
    return {
        "resume_document_id": resume,
        "jd_document_id": jd,
        "settings": {**SETTINGS, **overrides},
    }


def test_create_and_get_interview(client):
    register(client)
    resume, jd = make_docs(client)
    r = client.post("/api/v1/interviews", json=payload(resume, jd))
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "DRAFT"
    assert body["settings"]["duration_minutes"] == 30
    assert body["resume_title"] == "r.pdf"
    assert body["jd_title"] == "ML Engineer"
    got = client.get(f"/api/v1/interviews/{body['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_create_requires_auth(client):
    rid = str(uuid.uuid4())
    assert client.post("/api/v1/interviews", json=payload(rid, rid)).status_code == 401


def test_invalid_duration_rejected(client):
    register(client)
    resume, jd = make_docs(client)
    r = client.post("/api/v1/interviews", json=payload(resume, jd, duration_minutes=20))
    assert r.status_code == 422


def test_blank_role_title_rejected(client):
    register(client)
    resume, jd = make_docs(client)
    r = client.post("/api/v1/interviews", json=payload(resume, jd, role_title="   "))
    assert r.status_code == 422


def test_other_users_documents_not_usable(client):
    register(client)  # user A
    resume, jd = make_docs(client)
    client.cookies.clear()
    register(client)  # user B
    r = client.post("/api/v1/interviews", json=payload(resume, jd))
    assert r.status_code == 404


def test_swapped_document_types_rejected(client):
    register(client)
    resume, jd = make_docs(client)
    r = client.post("/api/v1/interviews", json=payload(jd, resume))
    assert r.status_code == 404


def test_list_is_private_between_users(client):
    register(client)  # user A
    resume, jd = make_docs(client)
    interview_id = client.post("/api/v1/interviews", json=payload(resume, jd)).json()["id"]
    assert len(client.get("/api/v1/interviews").json()) == 1

    client.cookies.clear()
    register(client)  # user B
    assert client.get("/api/v1/interviews").json() == []
    assert client.get(f"/api/v1/interviews/{interview_id}").status_code == 404
    assert client.delete(f"/api/v1/interviews/{interview_id}").status_code == 404


def test_delete_interview(client):
    register(client)
    resume, jd = make_docs(client)
    interview_id = client.post("/api/v1/interviews", json=payload(resume, jd)).json()["id"]
    assert client.delete(f"/api/v1/interviews/{interview_id}").status_code == 204
    assert client.get("/api/v1/interviews").json() == []


def test_document_in_use_cannot_be_deleted_until_interview_is(client):
    register(client)
    resume, jd = make_docs(client)
    interview_id = client.post("/api/v1/interviews", json=payload(resume, jd)).json()["id"]
    blocked = client.delete(f"/api/v1/documents/{resume}")
    assert blocked.status_code == 409
    assert "interview" in blocked.json()["detail"]
    assert client.delete(f"/api/v1/interviews/{interview_id}").status_code == 204
    assert client.delete(f"/api/v1/documents/{resume}").status_code == 204