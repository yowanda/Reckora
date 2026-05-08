"""Investigation endpoints (collect / persist / render dossier)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from reckora.collectors.base import Collector
from reckora.collectors.breach import BreachCollector
from reckora.config import settings as engine_settings
from reckora.evidence.archive import Archiver, WaybackArchiver
from reckora.evidence.screenshot import Screenshotter
from reckora.models.entity import Identifier
from reckora.models.enums import IdentifierType
from reckora.orchestrator import Orchestrator
from reckora.persistence.repository import SubjectRepository
from reckora.reasoning.client import ReasoningClient
from reckora.reasoning.hypothesize import hypothesize
from reckora.reasoning.summarize import summarize
from reckora.reports.html import to_dossier_html
from reckora.reports.json_export import to_dossier_dict
from reckora.reports.markdown import to_dossier_md
from reckora.reports.pdf import to_dossier_pdf
from reckora_api.auth.models import UserRecord
from reckora_api.config import APISettings
from reckora_api.deps import current_user, get_orchestrator, get_subject_repo
from reckora_api.investigations.schemas import (
    IdentifierIn,
    InvestigationRequest,
    SavedDossierPayload,
    SubjectSummary,
)


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
) -> SavedDossierPayload:
    """Run a Reckora investigation and persist the result.

    Mirrors ``reckora investigate --save`` from the CLI: ``--archive``,
    ``--screenshot`` and ``--ai`` map onto request fields, the saved dossier
    is returned in full so the client doesn't need a follow-up GET.
    """
    del user  # auth-only dependency

    seed = _identifier_from(payload.seed)
    extras = [_identifier_from(e) for e in payload.extras]

    archiver: Archiver | None = WaybackArchiver() if payload.archive else None
    api_settings: APISettings = request.app.state.settings
    screenshotter: Screenshotter | None = (
        _build_screenshotter(api_settings) if payload.screenshot else None
    )
    extra_collectors: list[Collector] = [_build_breach_collector()] if payload.breach else []
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
        client = ReasoningClient(
            api_key=engine_settings.openai_api_key,
            model=engine_settings.openai_model,
        )
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

    summary_row = repo.save(
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
    )
    payload_dict = to_dossier_dict(
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
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
    )


@router.get("/subjects", response_model=list[SubjectSummary])
def list_subjects(
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[SubjectSummary]:
    """Return the most recently saved dossiers, newest first."""
    del user
    rows = repo.list_recent(limit=limit)
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
        )
        for r in rows
    ]


@router.get("/subjects/{subject_id}", response_model=SavedDossierPayload)
def get_subject(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
) -> SavedDossierPayload:
    """Return the rehydrated dossier for ``subject_id``."""
    del user
    dossier = repo.get(subject_id)
    if dossier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    payload_dict = to_dossier_dict(
        subject=dossier.subject,
        traces=dossier.traces,
        edges=dossier.edges,
        summary=dossier.summary,
        hypotheses=dossier.hypotheses,
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
    fmt: Annotated[str, Query(alias="format", pattern=r"^(md|json|html|pdf)$")] = "html",
) -> Response:
    """Render a saved dossier in the requested format."""
    del user
    dossier = repo.get(subject_id)
    if dossier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    if fmt == "html":
        body = to_dossier_html(
            subject=dossier.subject,
            traces=dossier.traces,
            edges=dossier.edges,
            summary=dossier.summary,
            hypotheses=dossier.hypotheses,
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
            )
        )
    if fmt == "pdf":
        pdf_bytes = to_dossier_pdf(
            subject=dossier.subject,
            traces=dossier.traces,
            edges=dossier.edges,
            summary=dossier.summary,
            hypotheses=dossier.hypotheses,
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
    )
    return PlainTextResponse(content=md, media_type="text/markdown; charset=utf-8")


@router.delete(
    "/subjects/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_subject(
    subject_id: str,
    user: Annotated[UserRecord, Depends(current_user)],
    repo: Annotated[SubjectRepository, Depends(get_subject_repo)],
) -> Response:
    """Delete a saved dossier."""
    del user
    if not repo.delete(subject_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no saved dossier with id {subject_id!r}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
