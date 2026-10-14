import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import security
from app.db.models import AuthSession, User


class EmailTaken(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def register_user(session: AsyncSession, email: str, password: str) -> User:
    pw_hash = await asyncio.to_thread(security.hash_password, password)
    user = User(email=normalize_email(email), password_hash=pw_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise EmailTaken from None
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    user = result.scalar_one_or_none()
    if user is None:
        await asyncio.to_thread(security.verify_dummy, password)
        return None
    ok = await asyncio.to_thread(security.verify_password, user.password_hash, password)
    if not ok or not user.is_active:
        return None
    if security.needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(security.hash_password, password)
        await session.commit()
    return user


async def create_session(session: AsyncSession, user: User) -> str:
    token = security.new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=get_settings().session_ttl_hours)
    session.add(
        AuthSession(user_id=user.id, token_hash=security.hash_token(token), expires_at=expires)
    )
    await session.commit()
    return token


async def get_user_for_token(session: AsyncSession, token: str) -> User | None:
    stmt = (
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == security.hash_token(token),
            AuthSession.expires_at > func.now(),
            User.is_active.is_(True),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def revoke_session(session: AsyncSession, token: str) -> None:
    await session.execute(
        delete(AuthSession).where(AuthSession.token_hash == security.hash_token(token))
    )
    await session.commit()