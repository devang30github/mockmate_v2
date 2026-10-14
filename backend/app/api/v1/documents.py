import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import get_settings
from app.db.models import Document, DocumentVersion, User
from app.db.session import get_session
from app.services import documents as svc
from app.services.extraction import DocumentError

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: uuid.UUID
    doc_type: str
    title: str
    version: int
    source_filename: str | None
    char_count: int
    page_count: int | None
    created_at: datetime
    text_preview: str


class DocumentDetail(DocumentOut):
    text: str


class JobDescriptionIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=svc.JD_MIN_CHARS, max_length=svc.JD_MAX_CHARS)


def _out(doc: Document, ver: DocumentVersion) -> dict:
    return {
        "id": doc.id,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "version": ver.version,
        "source_filename": ver.source_filename,
        "char_count": ver.char_count,
        "page_count": ver.page_count,
        "created_at": doc.created_at,
        "text_preview": ver.raw_text[:300],
    }


@router.post("/resume", response_model=DocumentOut, status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    try:
        data = await svc.read_limited(file, settings.max_upload_bytes)
        doc, ver = await svc.create_resume(
            session, user, file.filename or "resume", data, settings
        )
    except DocumentError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return _out(doc, ver)


@router.post("/job-description", response_model=DocumentOut, status_code=201)
async def create_job_description(
    body: JobDescriptionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        doc, ver = await svc.create_job_description(session, user, body.title, body.text)
    except DocumentError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return _out(doc, ver)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return [_out(d, v) for d, v in await svc.list_documents(session, user)]


@router.get("/{doc_id}", response_model=DocumentDetail)
async def get_document(
    doc_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    found = await svc.get_document(session, user, doc_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Document not found")
    doc, ver = found
    return {**_out(doc, ver), "text": ver.raw_text}


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        deleted = await svc.delete_document(session, user, doc_id)
    except svc.DocumentInUse:
        raise HTTPException(
            status_code=409,
            detail="This document is used by an interview. Delete that interview first.",
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")