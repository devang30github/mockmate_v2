import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.domain.session_state import Event, InvalidTransition
from app.services.session_state import StaleState, apply_event

pytestmark = pytest.mark.anyio


@pytest.fixture
async def maker():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    yield async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE email LIKE '%@state-test.mockmate.dev'"))
    await eng.dispose()


@pytest.fixture
async def interview_id(maker):
    """A READY interview, created with plain SQL (no API, no LLM)."""
    email = f"{uuid.uuid4().hex[:10]}@state-test.mockmate.dev"
    async with maker() as s:
        uid = (
            await s.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
                {"e": email},
            )
        ).scalar_one()
        doc_ids = []
        for doc_type in ("resume", "job_description"):
            doc_ids.append(
                (
                    await s.execute(
                        text(
                            "INSERT INTO documents (user_id, doc_type, title) "
                            "VALUES (:u, :t, 't') RETURNING id"
                        ),
                        {"u": uid, "t": doc_type},
                    )
                ).scalar_one()
            )
        iid = (
            await s.execute(
                text(
                    "INSERT INTO interview_sessions "
                    "(user_id, resume_document_id, jd_document_id, settings, state) "
                    "VALUES (:u, :r, :j, '{}'::jsonb, 'READY') RETURNING id"
                ),
                {"u": uid, "r": doc_ids[0], "j": doc_ids[1]},
            )
        ).scalar_one()
        await s.commit()
    return iid


async def test_valid_event_advances_state_and_version(maker, interview_id):
    async with maker() as s:
        assert await apply_event(s, interview_id, Event.START) == ("ASKING", 1)
        assert await apply_event(s, interview_id, Event.TTS_DONE) == ("LISTENING", 2)


async def test_invalid_event_is_rejected_and_nothing_changes(maker, interview_id):
    async with maker() as s:
        with pytest.raises(InvalidTransition):
            await apply_event(s, interview_id, Event.STT_OK)
        assert await apply_event(s, interview_id, Event.START) == ("ASKING", 1)


async def test_unknown_interview_raises_lookup_error(maker):
    async with maker() as s:
        with pytest.raises(LookupError):
            await apply_event(s, uuid.uuid4(), Event.START)


async def test_two_racing_starts_have_exactly_one_winner(maker, interview_id):
    async def attempt():
        async with maker() as s:
            return await apply_event(s, interview_id, Event.START)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    wins = [r for r in results if isinstance(r, tuple)]
    losses = [r for r in results if isinstance(r, (InvalidTransition, StaleState))]
    assert len(wins) == 1 and len(losses) == 1
    async with maker() as s:
        row = (
            await s.execute(
                text("SELECT state, state_version FROM interview_sessions WHERE id = :i"),
                {"i": interview_id},
            )
        ).one()
    assert tuple(row) == ("ASKING", 1)