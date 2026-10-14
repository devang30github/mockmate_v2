import asyncio
import hashlib
import logging
import re
import uuid

from fastapi import UploadFile
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Document, DocumentVersion, User
from app.services.extraction import (
    FileTooLarge,
    UnreadableDocument,
    extract_text,
    normalize_text,
)

from sqlalchemy.exc import IntegrityError

log = logging.getLogger("mockmate.documents")

JD_MIN_CHARS = 100
JD_MAX_CHARS = 20000


def safe_filename(name: str | None) -> str:
    name = (name or "resume").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "resume")[:255]


async def read_limited(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FileTooLarge(f"File is too large. The limit is {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _store(
    session: AsyncSession,
    user: User,
    *,
    doc_type: str,
    title: str,
    text: str,
    filename: str | None,
    mime: str | None,
    size_bytes: int,
    digest: str,
    page_count: int | None,
) -> tuple[Document, DocumentVersion]:
    doc = Document(user_id=user.id, doc_type=doc_type, title=title)
    session.add(doc)
    await session.flush()
    ver = DocumentVersion(
        document_id=doc.id,
        version=1,
        source_filename=filename,
        mime=mime,
        size_bytes=size_bytes,
        sha256=digest,
        raw_text=text,
        char_count=len(text),
        page_count=page_count,
    )
    session.add(ver)
    await session.commit()
    await session.refresh(doc)
    await session.refresh(ver)
    log.info("document stored type=%s chars=%d pages=%s", doc_type, len(text), page_count)
    return doc, ver


async def create_resume(
    session: AsyncSession, user: User, filename: str, data: bytes, settings: Settings
) -> tuple[Document, DocumentVersion]:
    name = safe_filename(filename)
    extracted = await asyncio.to_thread(
        extract_text,
        name,
        data,
        max_pages=settings.max_pdf_pages,
        min_chars=settings.min_resume_chars,
    )
    return await _store(
        session,
        user,
        doc_type="resume",
        title=name,
        text=extracted.text,
        filename=name,
        mime=extracted.mime,
        size_bytes=len(data),
        digest=hashlib.sha256(data).hexdigest(),
        page_count=extracted.page_count,
    )


async def create_job_description(
    session: AsyncSession, user: User, title: str | None, text: str
) -> tuple[Document, DocumentVersion]:
    clean = normalize_text(text)
    if len(clean) < JD_MIN_CHARS:
        raise UnreadableDocument(
            f"The job description is too short ({len(clean)} characters). "
            f"Paste at least {JD_MIN_CHARS}."
        )
    raw = clean.encode()
    return await _store(
        session,
        user,
        doc_type="job_description",
        title=(title or "Job description").strip()[:200] or "Job description",
        text=clean,
        filename=None,
        mime="text/plain",
        size_bytes=len(raw),
        digest=hashlib.sha256(raw).hexdigest(),
        page_count=None,
    )


def _latest_stmt(user_id: uuid.UUID):
    latest = (
        select(
            DocumentVersion.document_id.label("document_id"),
            func.max(DocumentVersion.version).label("v"),
        )
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    return (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(
            latest,
            and_(
                latest.c.document_id == DocumentVersion.document_id,
                latest.c.v == DocumentVersion.version,
            ),
        )
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
    )


async def list_documents(
    session: AsyncSession, user: User
) -> list[tuple[Document, DocumentVersion]]:
    rows = (await session.execute(_latest_stmt(user.id))).all()
    return [(d, v) for d, v in rows]


async def get_document(
    session: AsyncSession, user: User, doc_id: uuid.UUID
) -> tuple[Document, DocumentVersion] | None:
    row = (await session.execute(_latest_stmt(user.id).where(Document.id == doc_id))).first()
    return (row[0], row[1]) if row else None

class DocumentInUse(Exception):
    pass

async def delete_document(session: AsyncSession, user: User, doc_id: uuid.UUID) -> bool:
    doc = (
        await session.execute(
            select(Document).where(Document.id == doc_id, Document.user_id == user.id)
        )
    ).scalar_one_or_none()
    if doc is None:
        return False

    await session.delete(doc)  # versions go with it (ON DELETE CASCADE)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise DocumentInUse from None
    return True