"""GitHub OAuth helpers for the social-login flow.

The flow is the standard ``authorization_code`` grant. Reckora plays
the role of a confidential client: we have a server-side
``client_secret`` and exchange the ``code`` directly with GitHub
from the API process, so the access token never touches the browser.

The two HTTP calls are wrapped in this module so the route handler
stays a thin orchestration layer and the network surface can be
mocked in tests via ``pytest-httpx``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
USER_EMAILS_URL = "https://api.github.com/user/emails"

# ``read:user`` is the minimum scope that returns the authenticated
# user's profile (id + login). ``user:email`` is added so we can
# show the verified primary email to the operator without forcing
# them to make their email public on GitHub.
SCOPE = "read:user user:email"

# Lifetime of the signed ``state`` round-tripped through GitHub. The
# user only spends a few seconds on GitHub's consent screen, so a
# tight TTL is fine and keeps the attack window small.
_STATE_TTL_SECONDS = 600

_STATE_TOKEN_TYPE = "github_oauth_state"


@dataclass(frozen=True)
class GitHubUser:
    """The subset of GitHub's ``/user`` response Reckora actually uses."""

    id: int
    login: str
    email: str | None


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    """Return the ``github.com/login/oauth/authorize`` URL to redirect to."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "allow_signup": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def issue_state_token(
    *,
    secret: str,
    next_path: str,
    algorithm: str = "HS256",
) -> str:
    """Mint the signed ``state`` carried through GitHub's redirect.

    The token binds the request to (a) a freshly generated nonce — so
    a leaked authorization ``code`` from one login attempt cannot be
    replayed against a different one — and (b) the post-login
    ``next`` path the SPA should land on. We sign with the API's
    JWT secret so no extra config is needed.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "type": _STATE_TOKEN_TYPE,
        "n": secrets.token_urlsafe(16),
        "next": next_path,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_STATE_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_state_token(
    token: str,
    *,
    secret: str,
    algorithm: str = "HS256",
) -> dict[str, Any]:
    """Verify the ``state`` returned by GitHub. Raises on any failure.

    A malformed / expired / wrong-type state must abort the callback
    before we trade the ``code`` for a real access token — without
    this check, an attacker who tricks a victim into hitting the
    callback URL with a stolen ``code`` could impersonate them.
    """
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    if payload.get("type") != _STATE_TOKEN_TYPE:
        raise jwt.InvalidTokenError("state token is not a GitHub OAuth state")
    return payload


async def exchange_code(
    code: str,
    *,
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> str:
    """Trade the authorization ``code`` for a GitHub access token.

    GitHub's token endpoint defaults to an ``application/x-www-form-
    urlencoded`` response. We ask for JSON via the ``Accept`` header
    so we can ``resp.json()`` without parsing a query string.
    """
    resp = await client.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError(f"GitHub did not return an access_token: {body!r}")
    return token


async def fetch_user(
    access_token: str,
    *,
    client: httpx.AsyncClient,
) -> GitHubUser:
    """Fetch the authenticated user's profile + verified primary email.

    GitHub returns ``email = null`` on ``/user`` when the user keeps
    their email private. We fall back to ``/user/emails`` (gated on
    the ``user:email`` scope) and pick the verified primary so the
    operator can see a useful identity on the members page.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = await client.get(USER_URL, headers=headers)
    resp.raise_for_status()
    profile = resp.json()

    user_id = profile.get("id")
    login = profile.get("login")
    if not isinstance(user_id, int) or not isinstance(login, str) or not login:
        raise ValueError(f"GitHub /user response is missing id/login: {profile!r}")

    email = profile.get("email") if isinstance(profile.get("email"), str) else None
    if email is None:
        try:
            email_resp = await client.get(USER_EMAILS_URL, headers=headers)
            email_resp.raise_for_status()
            for entry in email_resp.json():
                if (
                    isinstance(entry, dict)
                    and entry.get("primary")
                    and entry.get("verified")
                    and isinstance(entry.get("email"), str)
                ):
                    email = entry["email"]
                    break
        except httpx.HTTPError:
            # The emails endpoint is best-effort — a 403 here just
            # means the user denied the ``user:email`` scope, which
            # is allowed.
            email = None

    return GitHubUser(id=user_id, login=login, email=email)
