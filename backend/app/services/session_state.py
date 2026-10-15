import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewSession
from app.domain.session_state import Event, transition


class StaleState(Exception):
    """Another request changed this interview between our read and our write."""


async def advance(
    session: AsyncSession, interview_id: uuid.UUID, event: Event
) -> tuple[str, int]:
    """Stage a state change inside the caller's transaction. Does NOT commit.

    Raises InvalidTransition when the event isn't allowed in the current state, and
    StaleState when a concurrent request changed the row first. The update only
    succeeds if the state and version are still the ones we read, so when two
    requests race for the same transition exactly one of them wins.
    The caller must already have checked that the user owns this interview.
    """
    row = (
        await session.execute(
            select(InterviewSession.state, InterviewSession.state_version).where(
                InterviewSession.id == interview_id
            )
        )
    ).one_or_none()
    if row is None:
        raise LookupError("interview not found")
    old_state, version = row
    new_state = transition(old_state, event)

    result = await session.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == interview_id,
            InterviewSession.state == old_state,
            InterviewSession.state_version == version,
        )
        .values(state=new_state.value, state_version=version + 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise StaleState()
    return new_state.value, version + 1


async def apply_event(
    session: AsyncSession, interview_id: uuid.UUID, event: Event
) -> tuple[str, int]:
    """advance() plus commit, for callers that change nothing else in the same transaction."""
    new_state, version = await advance(session, interview_id, event)
    await session.commit()
    return new_state, version