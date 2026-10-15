from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.providers.registry import Providers
from app.services import auth as auth_service


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    user = await auth_service.get_user_for_token(session, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_providers(request: Request) -> Providers:
    return request.app.state.providers