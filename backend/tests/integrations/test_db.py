import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def engine():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


async def test_select_one(engine):
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1


async def test_extensions_installed(engine):
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT extname FROM pg_extension"))
        names = {r[0] for r in rows}
    assert {"vector", "pg_trgm"} <= names


async def test_users_table_columns(engine):
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users'"
            )
        )
        cols = {r[0] for r in rows}
    assert {"id", "email", "password_hash", "is_active", "is_admin",
            "created_at", "updated_at"} <= cols


async def test_email_must_be_lowercase(engine):
    async with engine.connect() as conn:  # no commit: rolled back on close
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("INSERT INTO users (email, password_hash) VALUES ('A@B.com', 'x')")
            )


async def test_email_is_unique(engine):
    async with engine.connect() as conn:
        await conn.execute(
            text("INSERT INTO users (email, password_hash) VALUES ('dup@test.dev', 'x')")
        )
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("INSERT INTO users (email, password_hash) VALUES ('dup@test.dev', 'y')")
            )