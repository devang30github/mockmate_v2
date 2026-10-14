import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import InterviewSession, User
from app.db.session import get_session
from app.domain.interview_settings import InterviewSettings
from app.services import interviews as svc

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