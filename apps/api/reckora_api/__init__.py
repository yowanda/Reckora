"""HTTP API for Reckora.

A thin FastAPI shell on top of the existing engine
(``reckora.orchestrator.Orchestrator`` + ``reckora.persistence`` +
``reckora.reports``). The web frontend (Vite + React + TypeScript, see
ROADMAP) consumes this API as its sole backend; the CLI continues to use the
same engine directly without going through HTTP.

Auth model is JWT bearer issued by ``POST /api/v1/auth/token``. Every
investigation endpoint requires a valid token.
"""

from __future__ import annotations

from .config import APISettings
from .main import create_app

__all__ = ["APISettings", "create_app"]
