"""Investigation endpoints for the Reckora API.

These endpoints are the HTTP equivalent of the CLI's ``investigate`` /
``list`` / ``show`` / ``delete`` subcommands. They reuse the same engine
(``Orchestrator``, ``SubjectRepository``, ``reckora.reports``) so behaviour
is bit-for-bit identical between CLI and API.
"""

from __future__ import annotations

from .routes import router

__all__ = ["router"]
