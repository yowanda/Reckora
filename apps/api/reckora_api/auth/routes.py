"""Authentication endpoints: register, token, me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from reckora_api.auth.models import TokenResponse, UserCreate, UserPublic, UserRecord
from reckora_api.auth.passwords import hash_password, verify_password
from reckora_api.auth.repository import UserRepository
from reckora_api.auth.tokens import create_token
from reckora_api.config import APISettings
from reckora_api.deps import current_user, get_settings, get_user_repo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserPublic,
    responses={409: {"description": "username already taken"}},
)
def register(
    payload: UserCreate,
    repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> UserPublic:
    """Create a new user. Username must be unique."""
    if repo.get_by_username(payload.username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already taken",
        )
    record = repo.create_user(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    return UserPublic(id=record.id, username=record.username, created_at=record.created_at)


@router.post("/token", response_model=TokenResponse)
def issue_token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    repo: Annotated[UserRepository, Depends(get_user_repo)],
    settings: Annotated[APISettings, Depends(get_settings)],
) -> TokenResponse:
    """Exchange username + password for a short-lived JWT (OAuth2-style form)."""
    user = repo.get_by_username(form.username)
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_token(
        subject=str(user.id),
        secret=settings.jwt_secret,
        ttl_seconds=settings.jwt_ttl_seconds,
        algorithm=settings.jwt_algorithm,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_ttl_seconds,
    )


@router.get("/me", response_model=UserPublic)
def me(user: Annotated[UserRecord, Depends(current_user)]) -> UserPublic:
    """Return the user that owns the current bearer token."""
    return UserPublic(id=user.id, username=user.username, created_at=user.created_at)
