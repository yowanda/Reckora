"""Per-user settings endpoints.

* ``GET /api/v1/users/me/settings`` — return presence flags only
  (the plaintext API key is never exposed once written).
* ``PUT /api/v1/users/me/settings`` — set / clear the BYOK
  AgentRouter API key. An empty string clears the saved value.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from reckora_api.auth.models import UserRecord
from reckora_api.deps import current_user, get_user_settings_repo

from .models import UserSettingsPublic, UserSettingsUpdate
from .repository import UserSettingsRecord, UserSettingsRepository

router = APIRouter(prefix="/users/me/settings", tags=["settings"])


def _to_public(record: UserSettingsRecord | None) -> UserSettingsPublic:
    """Translate a (possibly missing) settings row to its public shape.

    Missing rows are equivalent to "all unset" — the user has never
    saved any BYOK key, so every presence flag is ``False``.
    """
    if record is None or record.agentrouter_api_key_ciphertext is None:
        return UserSettingsPublic(has_agentrouter_key=False)
    return UserSettingsPublic(has_agentrouter_key=True)


@router.get("", response_model=UserSettingsPublic)
def get_settings(
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[UserSettingsRepository, Depends(get_user_settings_repo)],
) -> UserSettingsPublic:
    """Return presence flags for the current user's saved settings."""
    return _to_public(repo.get(user.id))


@router.put(
    "",
    response_model=UserSettingsPublic,
    status_code=status.HTTP_200_OK,
)
def update_settings(
    payload: UserSettingsUpdate,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[UserSettingsRepository, Depends(get_user_settings_repo)],
) -> UserSettingsPublic:
    """Save or clear the current user's BYOK secrets.

    Empty string in ``agentrouter_api_key`` is the explicit "clear"
    signal — it removes the encrypted ciphertext from the row but
    keeps the row itself so future audit timestamps are continuous.
    """
    if payload.agentrouter_api_key.strip():
        record = repo.set_agentrouter_key(user.id, payload.agentrouter_api_key)
    else:
        record = repo.clear_agentrouter_key(user.id)
    return _to_public(record)
