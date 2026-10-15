import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_providers
from app.db.models import InterviewPlan, User
from app.db.session import get_session
from app.domain.planning import Plan
from app.providers.errors import ProviderError
from app.providers.registry import Providers
from app.services import interviews as interviews_svc
from app.services import planning as planning_svc
from app.services.structured import StructuredOutputError

router = APIRouter(prefix="/interviews", tags=["plans"])


class PlanOut(BaseModel):
    interview_id: uuid.UUID
    interview_state: str
    version: int
    model: str
    prompt_version: str
    created_at: datetime
    cached: bool
    plan: Plan


def _out(row: InterviewPlan, state: str, cached: bool) -> dict:
    return {
        "interview_id": row.interview_id,
        "interview_state": state,
        "version": row.version,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "created_at": row.created_at,
        "cached": cached,
        "plan": row.result,
    }


@router.post("/{interview_id}/plan", response_model=PlanOut)
async def create_plan(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    providers: Providers = Depends(get_providers),
) -> dict:
    try:
        row, cached, state = await planning_svc.plan_interview(
            session, user, interview_id, providers.llm
        )
    except interviews_svc.NotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    except planning_svc.InterviewLocked:
        raise HTTPException(
            status_code=409, detail="This interview has already started and can't be re-planned."
        )
    except StructuredOutputError:
        raise HTTPException(
            status_code=502, detail="The AI response could not be validated. Please try again."
        )
    except ProviderError:
        raise HTTPException(
            status_code=503, detail="The AI provider is unavailable. Please try again shortly."
        )
    return _out(row, state, cached)


@router.get("/{interview_id}/plan", response_model=PlanOut)
async def get_plan(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    found = await planning_svc.get_plan(session, user, interview_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    row, state = found
    return _out(row, state, True)