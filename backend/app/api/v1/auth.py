import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool

    model_config = {"from_attributes": True}


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    body: RegisterIn, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    try:
        user = await auth_service.register_user(session, body.email, body.password)
    except auth_service.EmailTaken:
        raise HTTPException(status_code=409, detail="Email already registered")
    token = await auth_service.create_session(session, user)
    _set_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    user = await auth_service.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = await auth_service.create_session(session, user)
    _set_cookie(response, token)
    return user


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> None:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if token:
        await auth_service.revoke_session(session, token)
    response.delete_cookie(s.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user