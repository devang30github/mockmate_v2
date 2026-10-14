import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Document, InterviewSession, User
from app.domain.interview_settings import InterviewSettings

log = logging.getLogger("mockmate.interviews")


class NotFound(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def _owned_doc(
    session: AsyncSession, user: User, doc_id: uuid.UUID, doc_type: str, label: str
) -> Document:
    doc = (
        await session.execute(
            select(Document).where(
                Document.id == doc_id,
                Document.user_id == user.id,
                Document.doc_type == doc_type,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFound(f"{label} not found")
    return doc


async def create_interview(
    session: AsyncSession,
    user: User,
    resume_id: uuid.UUID,
    jd_id: uuid.UUID,
    cfg: InterviewSettings,
) -> tuple[InterviewSession, str, str]:
    resume = await _owned_doc(session, user, resume_id, "resume", "Resume")
    jd = await _owned_doc(session, user, jd_id, "job_description", "Job description")
    row = InterviewSession(
        user_id=user.id,
        resume_document_id=resume.id,
        jd_document_id=jd.id,
        settings=cfg.model_dump(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    log.info(
        "interview created type=%s duration=%s difficulty=%s",
        cfg.interview_type,
        cfg.duration_minutes,
        cfg.difficulty,
    )
    return row, resume.title, jd.title


def _select_with_titles(user_id: uuid.UUID):
    resume_doc, jd_doc = aliased(Document), aliased(Document)
    return (
        select(InterviewSession, resume_doc.title, jd_doc.title)
        .join(resume_doc, resume_doc.id == InterviewSession.resume_document_id)
        .join(jd_doc, jd_doc.id == InterviewSession.jd_document_id)
        .where(InterviewSession.user_id == user_id)
    )


async def list_interviews(
    session: AsyncSession, user: User
) -> list[tuple[InterviewSession, str, str]]:
    stmt = _select_with_titles(user.id).order_by(InterviewSession.created_at.desc())
    return [(i, r, j) for i, r, j in (await session.execute(stmt)).all()]


async def get_interview(
    session: AsyncSession, user: User, interview_id: uuid.UUID
) -> tuple[InterviewSession, str, str] | None:
    stmt = _select_with_titles(user.id).where(InterviewSession.id == interview_id)
    row = (await session.execute(stmt)).first()
    return (row[0], row[1], row[2]) if row else None


async def delete_interview(session: AsyncSession, user: User, interview_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            select(InterviewSession).where(
                InterviewSession.id == interview_id, InterviewSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True