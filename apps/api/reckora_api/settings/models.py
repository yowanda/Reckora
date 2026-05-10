"""Pydantic models for the per-user settings surface.

Public-facing models live here so request/response schemas show up in
the OpenAPI document. The plaintext API key is *never* echoed back to
the client; instead the GET endpoint returns presence flags (e.g.
``has_agentrouter_key``) so the UI can render a "configured / not set"
status without the server ever returning ciphertext or plaintext.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserSettingsPublic(BaseModel):
    """Body returned by ``GET /api/v1/users/me/settings``.

    Carries presence flags only — the actual API key, once written,
    is opaque to the user-facing surface (the only thing the UI
    needs to know is whether one is set).
    """

    model_config = ConfigDict(extra="forbid")

    has_agentrouter_key: bool = Field(
        default=False,
        description=(
            "True if the current user has saved a per-account AgentRouter "
            "API key. Used to drive the BYOK indicator in the UI."
        ),
    )


class UserSettingsUpdate(BaseModel):
    """Body for ``PUT /api/v1/users/me/settings``.

    The empty string is the explicit "clear" signal so the OpenAPI
    schema can describe both operations (set + clear) with a single
    non-nullable field. Omitting the field on a PATCH-style call is
    not supported — PUT semantics here are "send the desired full
    state of the user's settings".
    """

    model_config = ConfigDict(extra="forbid")

    agentrouter_api_key: str = Field(
        default="",
        description=(
            "AgentRouter (https://agentrouter.org) API key for the BYOK "
            "path. Send a non-empty string to save / replace the key, "
            "or an empty string to clear it. The plaintext value is "
            "never returned by GET endpoints; only presence is exposed."
        ),
        max_length=512,
    )
