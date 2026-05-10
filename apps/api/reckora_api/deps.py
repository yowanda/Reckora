"""FastAPI dependency providers for shared resources.

The app stores its settings, repositories, and orchestrator factory on
``app.state`` so tests can swap any of them out without monkey-patching
modules. These helpers turn that state back into typed dependencies.
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from reckora.orchestrator import Orchestrator
from reckora.persistence.repository import SubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.auth.tokens import decode_token
from reckora_api.config import APISettings
from reckora_api.settings.repository import UserSettingsRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=True)


def get_settings(request: Request) -> APISettings:
    settings: APISettings = request.app.state.settings
    return settings


def get_user_repo(request: Request) -> UserRepository:
    repo: UserRepository = request.app.state.user_repo
    return repo


def get_subject_repo(request: Request) -> SubjectRepository:
    repo: SubjectRepository = request.app.state.subject_repo
    return repo


def get_access_repo(request: Request) -> AccessRepository:
    repo: AccessRepository = request.app.state.access_repo
    return repo


def get_orchestrator(request: Request) -> Orchestrator:
    factory: Any = request.app.state.orchestrator_factory
    orch: Orchestrator = factory()
    return orch


def get_user_settings_repo(request: Request) -> UserSettingsRepository:
    repo: UserSettingsRepository = request.app.state.user_settings_repo
    return repo


def current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    settings: Annotated[APISettings, Depends(get_settings)],
    repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> UserRecord:
    """Decode the bearer token and load the matching user row.

    Returns 401 for any failure mode (bad signature, expired, missing sub,
    inactive user) — clients only ever see "invalid token", never the
    underlying reason, so we don't leak whether a username exists.
    """
    try:
        payload = decode_token(
            token,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = repo.get_by_id(int(sub))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user: Annotated[UserRecord, Depends(current_user)]) -> UserRecord:
    """Authorise admin-only routes.

    Returns the user record on success so the route handler can read the
    actor (e.g. self-demotion guards). Returns 403 — not 404 — because the
    target endpoint exists; the caller simply lacks the role.
    """
    if user.role is not Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user
