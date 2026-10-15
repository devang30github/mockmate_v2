import hashlib
import json
import logging
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewPlan, InterviewSession, User
from app.domain.interview_settings import InterviewSettings
from app.domain.planning import PlanDraft, PlanInvalid, finalize_plan, make_budget
from app.prompts.planning import PROMPT_VERSION, build_messages
from app.providers.base import LLMProvider
from app.services import analysis as analysis_svc
from app.services import interviews as interviews_svc
from app.services.structured import StructuredOutputError, complete_structured

log = logging.getLogger("mockmate.planning")

EDITABLE_STATES = ("DRAFT", "READY")


class InterviewLocked(Exception):
    pass


async def _latest(session: AsyncSession, interview_id: uuid.UUID) -> InterviewPlan | None:
    stmt = (
        select(InterviewPlan)
        .where(InterviewPlan.interview_id == interview_id)
        .order_by(InterviewPlan.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_plan(
    session: AsyncSession, user: User, interview_id: uuid.UUID
) -> tuple[InterviewPlan, str] | None:
    found = await interviews_svc.get_interview(session, user, interview_id)
    if found is None:
        return None
    row = await _latest(session, interview_id)
    return (row, found[0].state) if row else None


async def plan_interview(
    session: AsyncSession, user: User, interview_id: uuid.UUID, llm: LLMProvider
) -> tuple[InterviewPlan, bool, str]:
    found = await interviews_svc.get_interview(session, user, interview_id)
    if found is None:
        raise interviews_svc.NotFound("Interview not found")
    interview: InterviewSession = found[0]

    if interview.state not in EDITABLE_STATES:
        existing = await _latest(session, interview.id)
        if existing is None:
            raise InterviewLocked()
        return existing, True, interview.state

    analysis_row, _ = await analysis_svc.analyze_interview(session, user, interview.id, llm)
    cfg = InterviewSettings.model_validate(interview.settings)
    input_hash = hashlib.sha256(
        f"{PROMPT_VERSION}|{analysis_row.input_hash}|"
        f"{json.dumps(interview.settings, sort_keys=True)}".encode()
    ).hexdigest()

    latest = await _latest(session, interview.id)
    if latest is not None and latest.input_hash == input_hash:
        return latest, True, interview.state

    budget = make_budget(cfg.duration_minutes, cfg.difficulty)
    messages = build_messages(
        analysis_row.result,
        role_title=cfg.role_title,
        interview_type=cfg.interview_type,
        difficulty=cfg.difficulty,
        budget=budget,
    )
    t0 = time.perf_counter()
    draft, result = await complete_structured(llm, messages, PlanDraft, max_tokens=4000)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    try:
        plan = finalize_plan(
            draft,
            duration_minutes=cfg.duration_minutes,
            difficulty=cfg.difficulty,
            role_title=cfg.role_title,
        )
    except PlanInvalid as e:
        raise StructuredOutputError(str(e)) from e

    row = InterviewPlan(
        interview_id=interview.id,
        version=(latest.version + 1) if latest else 1,
        input_hash=input_hash,
        result=plan.model_dump(mode="json"),
        model=result.model,
        prompt_version=PROMPT_VERSION,
        latency_ms=latency_ms,
    )
    session.add(row)
    # DRAFT -> READY in the same transaction as the plan insert
    await session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == interview.id, InterviewSession.state == "DRAFT")
        .values(state="READY", state_version=InterviewSession.state_version + 1)
    )
    try:
        await session.commit()
    except IntegrityError:  # a concurrent request stored this version first
        await session.rollback()
        other = await _latest(session, interview.id)
        current = await interviews_svc.get_interview(session, user, interview.id)
        assert other is not None and current is not None
        return other, True, current[0].state

    await session.refresh(row)
    await session.refresh(interview)
    log.info(
        "plan stored version=%d sections=%d questions=%d latency_ms=%d",
        row.version,
        len(plan.sections),
        plan.budget.total_questions,
        latency_ms,
    )
    return row, False, interview.state