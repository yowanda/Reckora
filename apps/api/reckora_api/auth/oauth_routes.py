"""Social-login (OAuth) routes mounted under ``/api/v1/auth/oauth``.

Today only GitHub is implemented. The two endpoints are:

- ``GET /auth/oauth/github/login`` — 302 redirect to GitHub's
  authorize URL with a signed ``state`` cookie-of-sorts in the query
  string. The optional ``next`` query param is round-tripped through
  the state so the SPA lands on the original deep link after the
  flow completes.
- ``GET /auth/oauth/github/callback`` — handles GitHub's redirect
  back: verifies state, trades the code for a GitHub access token,
  fetches the user, finds-or-creates a local user row, mints a
  Reckora JWT, and hands the browser back to the SPA at
  ``<frontend>/auth/callback#token=<jwt>&next=<path>``.

The fragment-based hand-off keeps the JWT out of the API access log
and out of the ``Referer`` header on the next navigation.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.oauth_github import (
    GitHubUser,
    build_authorize_url,
    decode_state_token,
    exchange_code,
    fetch_user,
    issue_state_token,
)
from reckora_api.auth.passwords import make_unusable_password_hash
from reckora_api.auth.repository import UserRepository
from reckora_api.auth.tokens import create_token
from reckora_api.config import APISettings
from reckora_api.deps import get_settings, get_user_repo

router = APIRouter(prefix="/auth/oauth", tags=["auth"])


def _require_github_configured(settings: APISettings) -> None:
    if not settings.github_oauth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="github oauth is not configured on this server",
        )


def _safe_next(next_path: str | None) -> str:
    """Reject open-redirect attempts. Only allow same-origin relative paths."""
    if not next_path:
        return "/subjects"
    if not next_path.startswith("/") or next_path.startswith("//"):
        return "/subjects"
    return next_path


@router.get(
    "/github/login",
    responses={
        307: {"description": "redirect to github authorize"},
        503: {"description": "github oauth not configured"},
    },
)
def github_login(
    settings: Annotated[APISettings, Depends(get_settings)],
    next: str | None = None,
) -> RedirectResponse:
    """Kick off the GitHub OAuth flow."""
    _require_github_configured(settings)
    state = issue_state_token(
        secret=settings.jwt_secret,
        next_path=_safe_next(next),
        algorithm=settings.jwt_algorithm,
    )
    url = build_authorize_url(
        client_id=settings.oauth_github_client_id,
        redirect_uri=settings.oauth_github_redirect_url,
        state=state,
    )
    return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


def _http_client_factory(request: Request) -> httpx.AsyncClient:
    """Return the configured ``httpx.AsyncClient`` factory.

    Tests override ``app.state.oauth_http_client_factory`` with a
    stub that returns a client wired to ``pytest-httpx`` mocks; the
    default is a vanilla ``httpx.AsyncClient`` with a short timeout
    so a slow GitHub doesn't hang the request indefinitely.
    """
    factory = getattr(request.app.state, "oauth_http_client_factory", None)
    if factory is None:
        return httpx.AsyncClient(timeout=10.0)
    client: httpx.AsyncClient = factory()
    return client


def _derive_username(login: str, github_id: int, repo: UserRepository) -> str:
    """Pick a Reckora username for a freshly-onboarded GitHub user.

    Prefer the literal GitHub login when it's free — that matches
    what the operator expects to see in the members list. If the
    login is already taken by a pre-existing password account, fall
    back to a ``<login>-gh<id>`` form that's effectively guaranteed
    to be unique (numeric GitHub ids never collide).

    GitHub logins use a superset of Reckora's username rules (they
    can start with a digit, can contain hyphens); the Reckora
    pattern is ``[A-Za-z0-9_-]+``, so a sanitisation pass that maps
    any disallowed character to ``-`` is enough.
    """
    sanitized = "".join(ch if ch.isalnum() or ch in ("_", "-") else "-" for ch in login)
    # Reckora requires usernames between 3 and 64 chars; pad short
    # logins with the github id suffix and truncate long ones.
    if len(sanitized) < 3:
        sanitized = f"{sanitized}-gh{github_id}"
    sanitized = sanitized[:64]

    if repo.get_by_username(sanitized) is None:
        return sanitized
    fallback = f"{sanitized[: 64 - len(str(github_id)) - 3]}-gh{github_id}"
    return fallback[:64]


def _resolve_or_create_user(
    repo: UserRepository,
    github_user: GitHubUser,
) -> UserRecord:
    existing = repo.get_by_github_id(github_user.id)
    if existing is not None:
        return existing
    username = _derive_username(github_user.login, github_user.id, repo)
    if repo.get_by_username(username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "could not derive a free Reckora username for this GitHub "
                "account; ask an admin to link it manually"
            ),
        )
    return repo.create_user(
        username=username,
        password_hash=make_unusable_password_hash(),
        role=Role.VIEWER,
        github_id=github_user.id,
    )


@router.get(
    "/github/callback",
    responses={
        307: {"description": "redirect to frontend with token in fragment"},
        400: {"description": "invalid state, code, or oauth_error"},
        503: {"description": "github oauth not configured"},
    },
)
async def github_callback(
    request: Request,
    settings: Annotated[APISettings, Depends(get_settings)],
    repo: Annotated[UserRepository, Depends(get_user_repo)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Handle GitHub's redirect back, then send the browser to the SPA."""
    _require_github_configured(settings)
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"github denied the request: {error_description or error}",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing code or state in callback",
        )
    try:
        state_payload = decode_state_token(
            state,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired oauth state",
        ) from exc

    raw_next = state_payload.get("next")
    next_path = _safe_next(raw_next if isinstance(raw_next, str) else None)

    client = _http_client_factory(request)
    try:
        async with client:
            access_token = await exchange_code(
                code,
                client=client,
                client_id=settings.oauth_github_client_id,
                client_secret=settings.oauth_github_client_secret,
                redirect_uri=settings.oauth_github_redirect_url,
            )
            github_user = await fetch_user(access_token, client=client)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"github oauth exchange failed: {exc}",
        ) from exc

    user = _resolve_or_create_user(repo, github_user)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account is disabled",
        )
    jwt_token = create_token(
        subject=str(user.id),
        secret=settings.jwt_secret,
        ttl_seconds=settings.jwt_ttl_seconds,
        algorithm=settings.jwt_algorithm,
    )

    fragment = urlencode({"token": jwt_token, "next": next_path})
    target = f"{settings.frontend_url.rstrip('/')}/auth/callback#{fragment}"
    return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
