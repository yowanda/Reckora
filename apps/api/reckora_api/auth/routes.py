"""Authentication and user-management endpoints.

Public surface
--------------
- ``POST /auth/register`` — self-service registration; new users are always
  created as :class:`Role.VIEWER` so a stranger cannot grant themselves
  admin powers.
- ``POST /auth/token`` — OAuth2 password grant.
- ``GET /auth/me`` — current user, including their effective role so the
  frontend can branch on it without an extra request.

Admin surface (``GET /users`` and ``PATCH /users/{id}/role``)
------------------------------------------------------------
Admins can list every user and flip another account between
:class:`Role.VIEWER` and :class:`Role.ADMIN`. The role field on the JWT
is intentionally absent — it is loaded from the database on every
request via :func:`current_user`, so a demotion takes effect immediately
without waiting for the access token to expire.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from reckora_api.auth.models import (
    Role,
    RoleUpdate,
    TokenResponse,
    UserCreate,
    UserPublic,
    UserRecord,
)
from reckora_api.auth.passwords import hash_password, verify_password
from reckora_api.auth.repository import UserRepository
from reckora_api.auth.tokens import create_token
from reckora_api.config import APISettings
from reckora_api.deps import current_user, get_settings, get_user_repo, require_admin

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _to_public(record: UserRecord) -> UserPublic:
    return UserPublic(
        id=record.id,
        username=record.username,
        created_at=record.created_at,
        role=record.role,
    )


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
    """Create a new user. Username must be unique. New accounts are viewers."""
    if repo.get_by_username(payload.username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already taken",
        )
    record = repo.create_user(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=Role.VIEWER,
    )
    return _to_public(record)


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
    return _to_public(user)


@users_router.get(
    "",
    response_model=list[UserPublic],
    responses={403: {"description": "admin role required"}},
)
def list_users(
    _: Annotated[UserRecord, Depends(require_admin)],
    repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> list[UserPublic]:
    """Admin-only directory of every user (id, username, role, created_at)."""
    return [_to_public(r) for r in repo.list_users()]


@users_router.patch(
    "/{user_id}/role",
    response_model=UserPublic,
    responses={
        403: {"description": "admin role required"},
        404: {"description": "user not found"},
        409: {"description": "an admin cannot demote themselves"},
    },
)
def update_role(
    user_id: int,
    payload: RoleUpdate,
    actor: Annotated[UserRecord, Depends(require_admin)],
    repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> UserPublic:
    """Promote or demote a user. Admins cannot demote themselves.

    Self-demotion is rejected with a 409 to prevent an admin from accidentally
    locking themselves out of the only admin seat in a single-operator
    deployment. To step down, another admin (or the CLI) must do it.
    """
    if actor.id == user_id and payload.role is not Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an admin cannot demote themselves; ask another admin or use the CLI",
        )
    updated = repo.set_role(user_id, payload.role)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no user with id {user_id}",
        )
    return _to_public(updated)
