"""Investigation endpoints (collect / persist / render dossier)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from reckora.agent import AgentLoop, Researcher, ToolBudget
from reckora.auth.storage import load_credentials
from reckora.collectors.base import Collector
from reckora.collectors.breach import BreachCollector
from reckora.collectors.doc_leak import DocLeakCollector
from reckora.config import settings as engine_settings
from reckora.evidence.anchor import Anchor, anchor_traces
from reckora.evidence.archive import Archiver, WaybackArchiver
from reckora.evidence.screenshot import Screenshotter
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType
from reckora.orchestrator import Orchestrator
from reckora.persistence.repository import SavedDossier, SubjectRepository
from reckora.reasoning.client import ReasoningClient
from reckora.reasoning.hypothesize import hypothesize
from reckora.reasoning.summarize import summarize
from reckora.reasoning.web_search import (
    WebSearchFn,
    WebSearchUnavailableError,
    make_web_search_fn,
)
from reckora.reports.html import to_dossier_html
from reckora.reports.json_export import to_dossier_dict
from reckora.reports.markdown import to_dossier_md
from reckora.reports.pdf import to_dossier_pdf
from reckora_api.access.repository import AccessRepository
from reckora_api.auth.models import Role, UserRecord
from reckora_api.auth.repository import UserRepository
from reckora_api.config import APISettings
from reckora_api.deps import (
    current_user,
    get_access_repo,
    get_orchestrator,
    get_subject_repo,
    get_user_repo,
    get_user_settings_repo,
)
from reckora_api.investigations.schemas import (
    IdentifierIn,
    InvestigationRequest,
    SavedDossierPayload,
    SubjectSummary,
)
from reckora_api.settings.repository import UserSettingsRepository


def _resolve_owner_username(
    subject_id: str,
    access_repo: AccessRepository,
    user_repo: UserRepository,
) -> str | None:
    """Look up the owner's username for a subject, or ``None`` if un-owned.

    Resolving by username (rather than returning the numeric id) keeps
    the response payload portable: clients shouldn't need to memoise the
    user table to render a "owned by" badge.
    """
    owner_id = access_repo.get_owner(subject_id)
    if owner_id is None:
        return None
    record = user_repo.get_by_id(owner_id)
    return None if record is None else record.username


def _build_screenshotter(settings: APISettings) -> Screenshotter:
    """Construct a default :class:`Screenshotter` for the API.

    The Playwright import is local so the slim default install (without the
    ``[screenshots]`` extra) doesn't pay any Playwright import cost.
    """
    from reckora.evidence.screenshot import PlaywrightScreenshotter

    return PlaywrightScreenshotter(
        output_dir=settings.screenshots_dir,
        path_prefix=settings.screenshots_url_prefix,
    )


def _build_breach_collector() -> Collector:
    """Construct the HIBP breach collector for a single request.

    Pulled out as a module-level helper (mirroring ``_build_screenshotter``)
    so tests can monkeypatch it and inject a deterministic fake without
    going to the network or needing a real HIBP key.
    """
    return BreachCollector(api_key=engine_settings.hibp_api_key)


def _build_doc_leak_collector(
    *,
    web_search_fn: WebSearchFn | None = None,
) -> Collector:
    """Construct the public-doc-leak collector for a single request.

    Probed alongside HIBP under the same ``breach: true`` toggle: HIBP
    covers structured breach corpora keyed by email, while the doc-leak
    collector probes public document-share / paste sites for the seed
    identifier (username + email) to surface user-uploaded leaks.

    ``web_search_fn`` lets the eight SPA / anti-bot platforms (scribd,
    slideshare, issuu, 4shared, calameo, docplayer, dokumen.tips,
    anyflip) route their searches through OpenAI's Responses
    ``web_search`` tool instead of emitting ``unverified`` traces.
    ``None`` keeps the direct-probe-only behaviour the CLI uses when
    no auth backend is configured.

    Pulled out as a module-level helper so tests can monkeypatch it
    (mirroring ``_build_breach_collector``).
    """
    return DocLeakCollector(web_search_fn=web_search_fn)


@asynccontextmanager
async def _web_search_backend(*, enabled: bool) -> AsyncIterator[WebSearchFn | None]:
    """Resolve a :data:`WebSearchFn` for the doc-leak collector.

    Mirrors :func:`reckora.cli._web_search_backend`: yields ``None``
    when the caller hasn't opted in (``enabled=False``) or no credential
    is configured on the server, so the doc-leak collector falls back
    to its direct-probe-only mode. Otherwise owns an
    :class:`httpx.AsyncClient` for the lifetime of the investigation
    so the eight SPA-platform probes can share one connection pool to
    ``api.openai.com`` / ``chatgpt.com``.

    Resolution order matches the CLI: ``OPENAI_API_KEY`` first, then
    on-disk ChatGPT OAuth credentials saved by ``reckora auth login``.
    """
    if not enabled:
        yield None
        return
    api_key = engine_settings.openai_api_key or None
    creds = load_credentials()
    if not api_key and creds is None:
        yield None
        return
    async with httpx.AsyncClient() as client:
        try:
            fn = make_web_search_fn(
                client=client,
                api_key=api_key,
                oauth_credentials=creds,
            )
        except WebSearchUnavailableError:
            yield None
            return
        yield fn


router = APIRouter(tags=["investigations"])


def _identifier_from(payload: IdentifierIn) -> Identifier:
    try:
        kind = IdentifierType(payload.kind)
    except ValueError as exc:
        valid = ", ".join(e.value for e in IdentifierType)
        raise HTTPException(
            status_code=422,
            detail=f"unknown identifier kind {payload.kind!r}; expected one of: {valid}",
        ) from exc
    return Identifier(type=kind, value=payload.value)


@router.post(
    "/investigations",
    status_code=status.HTTP_201_CREATED,
    response_model=SavedDossierPayload,
)
async def create_investigation(
    payload: InvestigationRequest,
    request: Request,
    user: Annotated[UserRecord, Depends(current_user)],
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    settings_repo: Annotated[UserSettingsRepository, Depends(get_user_settings_repo)],
) -> SavedDossierPayload:
    """Run a Reckora investigation and persist the result.

    Mirrors ``reckora investigate --save`` from the CLI: ``--archive``,
    ``--screenshot`` and ``--ai`` map onto request fields, the saved dossier
    is returned in full so the client doesn't need a follow-up GET. The
    invoking user is recorded as the dossier's owner (Phase 5 RBAC).
    """
    seed = _identifier_from(payload.seed)
    extras = [_identifier_from(e) for e in payload.extras]

    archiver: Archiver | None = WaybackArchiver() if payload.archive else None
    api_settings: APISettings = request.app.state.settings
    screenshotter: Screenshotter | None = (
        _build_screenshotter(api_settings) if payload.screenshot else None
    )
    # The ``web_search_fn`` lifetime must wrap ``orchestrator.investigate``
    # because :class:`DocLeakCollector` invokes it during collection. We
    # only resolve a backend when ``breach`` is on; otherwise the context
    # manager short-circuits to ``None`` and the orchestrator path is
    # unchanged for stock investigations.
    async with _web_search_backend(enabled=payload.breach) as web_search_fn:
        extra_collectors: list[Collector] = (
            [
                _build_breach_collector(),
                _build_doc_leak_collector(web_search_fn=web_search_fn),
            ]
            if payload.breach
            else []
        )
        try:
            subject, traces, edges = await orchestrator.investigate(
                seed,
                extra_identifiers=extras,
                extra_collectors=extra_collectors,
                archiver=archiver,
                screenshotter=screenshotter,
            )
        finally:
            for resource in (archiver, screenshotter):
                close = getattr(resource, "aclose", None)
                if close is not None:
                    await close()

    summary_md: str | None = None
    hypotheses_md: str | None = None
    if payload.ai:
        # Three auth paths are wired through ``ReasoningClient`` —
        # ``openai`` (chat-completions API key), ``chatgpt_oauth``
        # (Codex Responses-API), and ``agentrouter`` (OpenAI-compat
        # gateway with BYOK). The default ``auto`` keeps the
        # historical behaviour: API key first, then OAuth.
        #
        # For the BYOK path we look up the current user's saved
        # AgentRouter key first; if absent the system-wide
        # ``AGENTROUTER_API_KEY`` env var is used as a fallback.
        agentrouter_api_key: str | None = None
        if payload.llm_provider == "agentrouter":
            agentrouter_api_key = settings_repo.get_agentrouter_key(user.id)
        if not agentrouter_api_key:
            agentrouter_api_key = engine_settings.agentrouter_api_key
        client = ReasoningClient(
            api_key=engine_settings.openai_api_key,
            model=engine_settings.openai_model,
            agentrouter_api_key=agentrouter_api_key,
            agentrouter_base_url=engine_settings.agentrouter_base_url,
            agentrouter_model=engine_settings.agentrouter_model,
            provider=payload.llm_provider,
        )
        if payload.ai_iterations >= 1:
            researcher: Researcher | None = None
            if payload.ai_tools:
                researcher = Researcher.with_default_tools(
                    client=client,
                    seed=seed,
                    budget=ToolBudget(calls_remaining=payload.ai_tool_calls),
                )
            loop = AgentLoop(
                orchestrator,
                client,
                max_iterations=payload.ai_iterations,
                researcher=researcher,
            )
            result = await loop.run(seed)
            subject = result.subject
            traces = list(result.traces)
            edges = list(result.edges)
        ident_strs = [str(i) for i in subject.identifiers]
        summary_md = await summarize(
            client,
            seed=str(seed),
            identifiers=ident_strs,
            traces=traces,
            edges=edges,
        )
        hypotheses_md = await hypothesize(
            client,
            seed=str(seed),
            identifiers=ident_strs,
            traces=traces,
            edges=edges,
        )

    anchor: Anchor | None = None
    if payload.anchor:
        if not traces:
            raise HTTPException(
                status_code=422,
                detail="anchor: true requires at least one trace, but none were collected.",
            )
        anchor = await anchor_traces(traces)

    summary_row = repo.save(
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
        anchor=anchor,
    )
    access_repo.set_owner(summary_row.id, user.id)
    payload_dict = to_dossier_dict(
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
        anchor=anchor,
    )
    return SavedDossierPayload(
        id=summary_row.id,
        created_at=summary_row.created_at,
        subject=payload_dict["subject"],
        traces=payload_dict["traces"],
        timeline=payload_dict["timeline"],
        anomalies=payload_dict["anomalies"],
        edges=payload_dict["edges"],
        ai=payload_dict["ai"],
        anchor=payload_dict["anchor"],
        owner_username=user.username,
    )


@router.get("/subjects", response_model=list[SubjectSummary])
def list_subjects(
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[SubjectSummary]:
    """List saved dossiers visible to the current user, newest first.

    - **Admins** see every saved dossier (including legacy un-owned rows
      created by the CLI before RBAC).
    - **Viewers** see only dossiers they own or that have been explicitly
      shared with them via the sharing endpoints.
    """
    if user.role is Role.ADMIN:
        rows = repo.list_recent(limit=limit)
    else:
        rows = access_repo.list_visible_summaries(user.id, limit=limit)
    # Cache user lookups so a 50-row listing with shared subjects from a
    # handful of teammates doesn't hammer the user table.
    owner_cache: dict[str, str | None] = {}

    def _owner(subject_id: str) -> str | None:
        if subject_id not in owner_cache:
            owner_cache[subject_id] = _resolve_owner_username(subject_id, access_repo, user_repo)
        return owner_cache[subject_id]

    return [
        SubjectSummary(
            id=r.id,
            seed=IdentifierIn(kind=r.seed_identifier.type.value, value=r.seed_identifier.value),
            created_at=r.created_at,
            identifier_count=r.identifier_count,
            trace_count=r.trace_count,
            edge_count=r.edge_count,
            has_summary=r.has_summary,
            has_hypotheses=r.has_hypotheses,
            has_anchor=r.has_anchor,
            owner_username=_owner(r.id),
        )
        for r in rows
    ]


def _load_authorised_dossier(
    subject_id: str,
    user: UserRecord,
    repo: SubjectRepository,
    access_repo: AccessRepository,
) -> SavedDossier:
    """Fetch a dossier and verify the actor has read access.

    Raises 404 (rather than 403) when a viewer asks for a subject they
    cannot see, so a non-owner cannot probe the API for which subject
    ids exist on the system. Admins skip the access check entirely so
    they can manage legacy un-owned rows created by the CLI.
    """
    dossier = repo.get(subject_id)
    if dossier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    if user.role is Role.ADMIN:
        return dossier
    owner_id = access_repo.get_owner(subject_id)
    if owner_id != user.id and not access_repo.can_read(subject_id, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    return dossier


@router.get("/subjects/{subject_id}", response_model=SavedDossierPayload)
def get_subject(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    user_repo: Annotated[UserRepository, Depends(get_user_repo)],
) -> SavedDossierPayload:
    """Return the rehydrated dossier for ``subject_id`` (auth-gated)."""
    dossier = _load_authorised_dossier(subject_id, user, repo, access_repo)
    payload_dict = to_dossier_dict(
        subject=dossier.subject,
        traces=dossier.traces,
        edges=dossier.edges,
        summary=dossier.summary,
        hypotheses=dossier.hypotheses,
        anchor=dossier.anchor,
    )
    return SavedDossierPayload(
        id=dossier.id,
        created_at=dossier.created_at,
        subject=payload_dict["subject"],
        traces=payload_dict["traces"],
        timeline=payload_dict["timeline"],
        anomalies=payload_dict["anomalies"],
        edges=payload_dict["edges"],
        ai=payload_dict["ai"],
        anchor=payload_dict["anchor"],
        owner_username=_resolve_owner_username(subject_id, access_repo, user_repo),
    )


@router.get(
    "/subjects/{subject_id}/dossier",
    responses={
        200: {
            "content": {
                "text/html": {},
                "text/markdown": {},
                "application/json": {},
                "application/pdf": {},
            }
        }
    },
)
def get_subject_dossier(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
    fmt: Annotated[str, Query(alias="format", pattern=r"^(md|json|html|pdf)$")] = "html",
) -> Response:
    """Render a saved dossier in the requested format (auth-gated)."""
    dossier = _load_authorised_dossier(subject_id, user, repo, access_repo)
    if fmt == "html":
        body = to_dossier_html(
            subject=dossier.subject,
            traces=dossier.traces,
            edges=dossier.edges,
            summary=dossier.summary,
            hypotheses=dossier.hypotheses,
            anchor=dossier.anchor,
        )
        return HTMLResponse(content=body)
    if fmt == "json":
        return JSONResponse(
            content=to_dossier_dict(
                subject=dossier.subject,
                traces=dossier.traces,
                edges=dossier.edges,
                summary=dossier.summary,
                hypotheses=dossier.hypotheses,
                anchor=dossier.anchor,
            )
        )
    if fmt == "pdf":
        pdf_bytes = to_dossier_pdf(
            subject=dossier.subject,
            traces=dossier.traces,
            edges=dossier.edges,
            summary=dossier.summary,
            hypotheses=dossier.hypotheses,
            anchor=dossier.anchor,
        )
        filename = f"{dossier.id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    md = to_dossier_md(
        subject=dossier.subject,
        traces=dossier.traces,
        edges=dossier.edges,
        summary=dossier.summary,
        hypotheses=dossier.hypotheses,
        anchor=dossier.anchor,
    )
    return PlainTextResponse(content=md, media_type="text/markdown; charset=utf-8")


@router.delete(
    "/subjects/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: {"description": "shared viewer cannot delete a dossier they don't own"},
        404: {"description": "subject not found or not visible to actor"},
    },
)
def delete_subject(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    access_repo: Annotated[AccessRepository, Depends(get_access_repo)],
) -> Response:
    """Delete a saved dossier.

    Authorisation:

    - Admins can delete any subject (including legacy un-owned rows).
    - Owners can delete their own subjects.
    - Shared viewers cannot delete; they get 403 (the subject *is*
      visible to them, so 404 would be misleading).
    - Non-owner / non-shared viewers get 404 to avoid leaking existence.
    """
    if user.role is not Role.ADMIN:
        owner_id = access_repo.get_owner(subject_id)
        if owner_id != user.id:
            if owner_id is not None and access_repo.can_read(subject_id, user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="only the dossier owner can delete it",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no saved dossier with id {subject_id!r}",
            )
    if not repo.delete(subject_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
