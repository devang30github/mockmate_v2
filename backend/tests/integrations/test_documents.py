import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.main import app
from tests.factories import SAMPLE_JD, make_blank_pdf, make_docx, make_pdf

PASSWORD = "a-decent-password-123"
DOMAIN = "@docs-test.mockmate.dev"


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
    run_sql("DELETE FROM users WHERE email LIKE :p", p=f"%{DOMAIN}")  # cascades to documents


def register(client):
    email = f"{uuid.uuid4().hex[:10]}{DOMAIN}"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201
    return email


def upload(client, name="resume.pdf", data=None, mime="application/pdf"):
    data = make_pdf() if data is None else data
    return client.post("/api/v1/documents/resume", files={"file": (name, data, mime)})


def test_upload_pdf_and_read_back(client):
    register(client)
    r = upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["doc_type"] == "resume"
    assert body["version"] == 1
    assert body["page_count"] == 1
    assert body["char_count"] > 200
    assert "Jane Doe" in body["text_preview"]
    detail = client.get(f"/api/v1/documents/{body['id']}")
    assert detail.status_code == 200
    assert "FastAPI" in detail.json()["text"]


def test_upload_docx(client):
    register(client)
    r = upload(
        client,
        "resume.docx",
        make_docx(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert r.status_code == 201
    assert r.json()["page_count"] is None


def test_upload_requires_auth(client):
    assert upload(client).status_code == 401


def test_unsupported_type_is_415(client):
    register(client)
    assert upload(client, "resume.txt", b"hello", "text/plain").status_code == 415


def test_scanned_pdf_is_422_with_clear_message(client):
    register(client)
    r = upload(client, "scan.pdf", make_blank_pdf())
    assert r.status_code == 422
    assert "scanned" in r.json()["detail"]


def test_oversized_upload_is_413(client, monkeypatch):
    register(client)
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(client).status_code == 413


def test_create_job_description(client):
    register(client)
    r = client.post(
        "/api/v1/documents/job-description", json={"title": "ML Engineer", "text": SAMPLE_JD}
    )
    assert r.status_code == 201
    assert r.json()["doc_type"] == "job_description"
    assert r.json()["title"] == "ML Engineer"


def test_job_description_too_short_is_422(client):
    register(client)
    r = client.post("/api/v1/documents/job-description", json={"text": "too short"})
    assert r.status_code == 422


def test_documents_are_private_between_users(client):
    register(client)  # user A
    doc_id = upload(client).json()["id"]
    assert len(client.get("/api/v1/documents").json()) == 1

    client.cookies.clear()
    register(client)  # user B
    assert client.get("/api/v1/documents").json() == []
    assert client.get(f"/api/v1/documents/{doc_id}").status_code == 404
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 404


def test_delete_document(client):
    register(client)
    doc_id = upload(client).json()["id"]
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
    assert client.get(f"/api/v1/documents/{doc_id}").status_code == 404
    assert client.get("/api/v1/documents").json() == []