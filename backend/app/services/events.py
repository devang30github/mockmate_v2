import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TranscriptEvent

REPLAY_LIMIT = 2000


async def append_event(
    session: AsyncSession, interview_id: uuid.UUID, type_: str, payload: dict
) -> int:
    """Stage a replayable event in the caller's transaction and return its event_id."""
    row = TranscriptEvent(interview_id=interview_id, type=type_, payload=payload)
    session.add(row)
    await session.flush()
    return row.event_id


async def events_after(
    session: AsyncSession, interview_id: uuid.UUID, last_event_id: int
) -> list[TranscriptEvent]:
    stmt = (
        select(TranscriptEvent)
        .where(
            TranscriptEvent.interview_id == interview_id,
            TranscriptEvent.event_id > last_event_id,
        )
        .order_by(TranscriptEvent.event_id)
        .limit(REPLAY_LIMIT)
    )
    return list((await session.execute(stmt)).scalars())