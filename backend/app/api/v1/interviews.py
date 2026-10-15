import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_providers
from app.db.models import InterviewAnalysis, InterviewSession, User
from app.db.session import get_session
from app.domain.analysis import Analysis
from app.domain.interview_settings import InterviewSettings
from app.providers.errors import ProviderError
from app.providers.registry import Providers
from app.services import analysis as analysis_svc
from app.services import interviews as svc
from app.services.structured import StructuredOutputError

router = APIRouter(prefix="/interviews", tags=["interviews"])


class CreateInterviewIn(BaseModel):
    resume_document_id: uuid.UUID
    jd_document_id: uuid.UUID
    settings: InterviewSettings


class InterviewOut(BaseModel):
    id: uuid.UUID
    state: str
    settings: InterviewSettings
    resume_title: str
    jd_title: str
    created_at: datetime


def _out(row: InterviewSession, resume_title: str, jd_title: str) -> dict:
    return {
        "id": row.id,
        "state": row.state,
        "settings": row.settings,
        "resume_title": resume_title,
        "jd_title": jd_title,
        "created_at": row.created_at,
    }


@router.post("", response_model=InterviewOut, status_code=201)
async def create_interview(
    body: CreateInterviewIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        row, rt, jt = await svc.create_interview(
            session, user, body.resume_document_id, body.jd_document_id, body.settings
        )
    except svc.NotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    return _out(row, rt, jt)


@router.get("", response_model=list[InterviewOut])
async def list_interviews(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return [_out(*t) for t in await svc.list_interviews(session, user)]


@router.get("/{interview_id}", response_model=InterviewOut)
async def get_interview(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    found = await svc.get_interview(session, user, interview_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Interview not found")
    return _out(*found)


@router.delete("/{interview_id}", status_code=204)
async def delete_interview(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await svc.delete_interview(session, user, interview_id):
        raise HTTPException(status_code=404, detail="Interview not found")

class AnalysisOut(BaseModel):
    interview_id: uuid.UUID
    model: str
    prompt_version: str
    created_at: datetime
    cached: bool
    result: Analysis


def _analysis_out(row: InterviewAnalysis, cached: bool) -> dict:
    return {
        "interview_id": row.interview_id,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "created_at": row.created_at,
        "cached": cached,
        "result": row.result,
    }


@router.post("/{interview_id}/analyze", response_model=AnalysisOut)
async def analyze_interview(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    providers: Providers = Depends(get_providers),
) -> dict:
    try:
        row, cached = await analysis_svc.analyze_interview(
            session, user, interview_id, providers.llm
        )
    except svc.NotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    except StructuredOutputError:
        raise HTTPException(
            status_code=502,
            detail="The AI response could not be validated. Please try again.",
        )
    except ProviderError:
        raise HTTPException(
            status_code=503, detail="The AI provider is unavailable. Please try again shortly."
        )
    return _analysis_out(row, cached)


@router.get("/{interview_id}/analysis", response_model=AnalysisOut)
async def get_analysis(
    interview_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await analysis_svc.get_analysis(session, user, interview_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return _analysis_out(row, True)