import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.main import app

PASSWORD = "a-decent-password-123"
DOMAIN = "@auth-test.mockmate.dev"


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


def new_email() -> str:
    return f"{uuid.uuid4().hex[:10]}{DOMAIN}"


def register(client, email=None, password=PASSWORD):
    email = email or new_email()
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    return email, r


def test_register_logs_in_and_me_works(client):
    email, r = register(client)
    assert r.status_code == 201
    assert r.json()["email"] == email
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert "password" not in me.text


def test_session_cookie_flags(client):
    _, r = register(client)
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_duplicate_email_conflicts(client):
    email, _ = register(client)
    _, r = register(client, email=email)
    assert r.status_code == 409


def test_login_is_case_insensitive_on_email(client):
    email, _ = register(client)
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login", json={"email": email.upper(), "password": PASSWORD}
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200


def test_wrong_password_and_unknown_email_look_identical(client):
    email, _ = register(client)
    client.cookies.clear()
    wrong = client.post("/api/v1/auth/login", json={"email": email, "password": "nope-nope-nope"})
    unknown = client.post(
        "/api/v1/auth/login", json={"email": new_email(), "password": "nope-nope-nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_short_password_rejected(client):
    _, r = register(client, password="short")
    assert r.status_code == 422


def test_me_requires_cookie(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_session_server_side(client):
    register(client)
    name = get_settings().session_cookie_name
    token = client.cookies.get(name)
    assert token
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.clear()
    # replay the old cookie by hand: the server must refuse it
    r = client.get("/api/v1/auth/me", headers={"Cookie": f"{name}={token}"})
    assert r.status_code == 401


def test_expired_session_rejected(client):
    email, _ = register(client)
    run_sql(
        "UPDATE auth_sessions SET expires_at = now() - interval '1 hour' "
        "WHERE user_id = (SELECT id FROM users WHERE email = :e)",
        e=email,
    )
    assert client.get("/api/v1/auth/me").status_code == 401