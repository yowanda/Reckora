"""FastAPI application factory.

The factory pattern keeps the app construction explicit so tests can build a
fresh app per case (with isolated SQLite paths and JWT secrets) and the CLI
can pass a fully-resolved :class:`APISettings`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from reckora.collectors.avatar import AvatarCollector
from reckora.collectors.email import EmailCollector
from reckora.collectors.github_api import GitHubCollector
from reckora.collectors.gravatar import GravatarCollector
from reckora.collectors.hackernews import HackerNewsCollector
from reckora.collectors.keybase import KeybaseCollector
from reckora.collectors.phone import PhoneCollector
from reckora.collectors.reddit import RedditCollector
from reckora.collectors.wallet_btc import BitcoinChainCollector
from reckora.collectors.wallet_eth import EthereumChainCollector
from reckora.collectors.wallet_sol import SolanaChainCollector
from reckora.collectors.web_profile import WebProfileCollector
from reckora.collectors.whois_rdap import WhoisRdapCollector
from reckora.config import settings as engine_settings
from reckora.orchestrator import Orchestrator
from reckora.persistence.sqlite import SQLiteSubjectRepository
from reckora_api.access.repository import AccessRepository
from reckora_api.access.routes import router as sharing_router
from reckora_api.auth.repository import UserRepository
from reckora_api.auth.routes import router as auth_router
from reckora_api.auth.routes import users_router as auth_users_router
from reckora_api.collab.routes import assignees_router, comments_router
from reckora_api.config import APISettings
from reckora_api.investigations.routes import router as investigations_router
from reckora_api.mentions.routes import mentions_router


def _default_orchestrator_factory() -> Orchestrator:
    return Orchestrator(
        [
            GitHubCollector(token=engine_settings.github_token),
            HackerNewsCollector(),
            KeybaseCollector(),
            GravatarCollector(),
            RedditCollector(),
            WhoisRdapCollector(),
            WebProfileCollector(),
            PhoneCollector(),
            EmailCollector(),
            BitcoinChainCollector(),
            EthereumChainCollector(api_key=engine_settings.etherscan_api_key),
            SolanaChainCollector(),
            AvatarCollector(),
        ]
    )


def create_app(
    settings: APISettings | None = None,
    *,
    orchestrator_factory: Callable[[], Orchestrator] | None = None,
) -> FastAPI:
    """Build a Reckora FastAPI app.

    Tests pass ``settings`` with an ephemeral ``db_path`` and a random
    ``jwt_secret`` so they don't have to depend on environment state.
    """
    s = settings or APISettings()
    if not s.jwt_secret:
        raise RuntimeError(
            "RECKORA_API_JWT_SECRET must be set; refusing to start with an empty secret"
        )

    app = FastAPI(
        title="Reckora API",
        version="0.1.0",
        description="HTTP API for the Reckora OSINT investigation engine.",
        docs_url="/docs" if s.docs_enabled else None,
        redoc_url="/redoc" if s.docs_enabled else None,
        openapi_url="/openapi.json" if s.docs_enabled else None,
    )

    app.state.settings = s
    app.state.user_repo = UserRepository(s.db_path)
    app.state.subject_repo = SQLiteSubjectRepository(s.db_path)
    # AccessRepository must be constructed after the engine + auth repos
    # because its FOREIGN KEY constraints reference ``subjects(id)`` and
    # ``users(id)`` — those tables have to exist when we run the
    # ``CREATE TABLE`` for ``subject_owners`` / ``subject_shares``.
    app.state.access_repo = AccessRepository(s.db_path)
    app.state.orchestrator_factory = orchestrator_factory or _default_orchestrator_factory

    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(auth_users_router, prefix="/api/v1")
    app.include_router(investigations_router, prefix="/api/v1")
    app.include_router(sharing_router, prefix="/api/v1")
    app.include_router(comments_router, prefix="/api/v1")
    app.include_router(assignees_router, prefix="/api/v1")
    app.include_router(mentions_router, prefix="/api/v1")

    # Mount captured screenshots so the frontend can render them inline. The
    # directory is created lazily — the app must not crash if screenshots are
    # disabled and the directory has never been written to.
    screenshots_dir = Path(s.screenshots_dir)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        s.screenshots_url_prefix,
        StaticFiles(directory=str(screenshots_dir)),
        name="screenshots",
    )

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
