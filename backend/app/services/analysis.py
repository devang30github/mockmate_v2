import hashlib
import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewAnalysis, User
from app.domain.analysis import Analysis
from app.prompts.analysis import PROMPT_VERSION, build_messages
from app.providers.base import LLMProvider
from app.services import documents as documents_svc
from app.services import interviews as interviews_svc
from app.services.structured import complete_structured

log = logging.getLogger("mockmate.analysis")


async def _row(session: AsyncSession, interview_id: uuid.UUID) -> InterviewAnalysis | None:
    return (
        await session.execute(
            select(InterviewAnalysis).where(InterviewAnalysis.interview_id == interview_id)
        )
    ).scalar_one_or_none()


async def get_analysis(
    session: AsyncSession, user: User, interview_id: uuid.UUID
) -> InterviewAnalysis | None:
    if await interviews_svc.get_interview(session, user, interview_id) is None:
        return None
    return await _row(session, interview_id)


async def analyze_interview(
    session: AsyncSession, user: User, interview_id: uuid.UUID, llm: LLMProvider
) -> tuple[InterviewAnalysis, bool]:
    found = await interviews_svc.get_interview(session, user, interview_id)
    if found is None:
        raise interviews_svc.NotFound("Interview not found")
    interview = found[0]

    resume = await documents_svc.get_document(session, user, interview.resume_document_id)
    jd = await documents_svc.get_document(session, user, interview.jd_document_id)
    if resume is None or jd is None:
        raise interviews_svc.NotFound("Source document not found")
    resume_ver, jd_ver = resume[1], jd[1]
    role_title = interview.settings["role_title"]

    input_hash = hashlib.sha256(
        f"{PROMPT_VERSION}|{resume_ver.sha256}|{jd_ver.sha256}|{role_title}".encode()
    ).hexdigest()

    existing = await _row(session, interview.id)
    if existing is not None and existing.input_hash == input_hash:
        return existing, True

    messages = build_messages(resume_ver.raw_text, jd_ver.raw_text, role_title)
    t0 = time.perf_counter()
    analysis, result = await complete_structured(llm, messages, Analysis, max_tokens=3500)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    payload = analysis.model_dump(mode="json")
    if existing is not None:
        existing.input_hash = input_hash
        existing.result = payload
        existing.model = result.model
        existing.prompt_version = PROMPT_VERSION
        existing.latency_ms = latency_ms
        await session.commit()
        await session.refresh(existing)
        row, cached = existing, False
    else:
        row = InterviewAnalysis(
            interview_id=interview.id,
            input_hash=input_hash,
            result=payload,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:  # a concurrent request stored it first
            await session.rollback()
            other = await _row(session, interview.id)
            assert other is not None
            return other, True
        await session.refresh(row)
        cached = False

    log.info(
        "analysis stored model=%s latency_ms=%d prompt_tokens=%s completion_tokens=%s",
        result.model,
        latency_ms,
        result.prompt_tokens,
        result.completion_tokens,
    )
    return row, cached