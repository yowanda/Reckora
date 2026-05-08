"""Reckora command-line interface."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .collectors.github_api import GitHubCollector
from .collectors.web_profile import WebProfileCollector
from .collectors.whois_rdap import WhoisRdapCollector
from .config import settings
from .evidence.archive import Archiver, WaybackArchiver
from .evidence.screenshot import Screenshotter
from .models.entity import Edge, Identifier, Subject, Trace
from .models.enums import IdentifierType
from .orchestrator import Orchestrator
from .persistence import SQLiteSubjectRepository
from .reasoning.client import ReasoningClient
from .reasoning.hypothesize import hypothesize
from .reasoning.summarize import summarize
from .reports.html import to_dossier_html
from .reports.json_export import to_dossier_json
from .reports.markdown import to_dossier_md
from .reports.pdf import to_dossier_pdf

app = typer.Typer(
    help="Reckora — AI-Native OSINT Investigation System.",
    no_args_is_help=True,
    add_completion=False,
)


def _identifier_from(value: str, kind: str) -> Identifier:
    try:
        identifier_type = IdentifierType(kind)
    except ValueError as exc:
        valid = ", ".join(e.value for e in IdentifierType)
        raise typer.BadParameter(
            f"unknown identifier kind {kind!r}; expected one of: {valid}"
        ) from exc
    return Identifier(type=identifier_type, value=value)


def _build_orchestrator() -> Orchestrator:
    return Orchestrator(
        [
            GitHubCollector(token=settings.github_token),
            WhoisRdapCollector(),
            WebProfileCollector(),
        ]
    )


def _render_dossier(
    fmt: str,
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None,
    hypotheses: str | None,
) -> str | bytes:
    """Render a dossier in one of the supported formats.

    Returns ``str`` for text formats (md / json / html) and ``bytes`` for
    binary formats (pdf).
    """
    if fmt == "json":
        return to_dossier_json(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
        )
    if fmt == "html":
        return to_dossier_html(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
        )
    if fmt == "md":
        return to_dossier_md(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
        )
    if fmt == "pdf":
        return to_dossier_pdf(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
        )
    raise typer.BadParameter(f"unknown format {fmt!r}; expected one of: md, json, html, pdf")


def _format_from_path(path: Path) -> str:
    """Infer dossier format from output file extension."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".pdf":
        return "pdf"
    return "md"


def _emit(payload: str | bytes, *, output: Path | None) -> None:
    """Write a rendered dossier to ``output`` (if given) or stdout."""
    if output is not None:
        if isinstance(payload, bytes):
            output.write_bytes(payload)
        else:
            output.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {output}")
        return
    if isinstance(payload, bytes):
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    typer.echo(payload)


def _build_screenshotter(output_dir: Path) -> Screenshotter:
    """Construct a default :class:`Screenshotter` (Playwright) for the CLI.

    The import is local so the slim default install (without the
    ``[screenshots]`` extra) doesn't pay any Playwright import cost.
    """
    from .evidence.screenshot import PlaywrightScreenshotter

    return PlaywrightScreenshotter(output_dir=output_dir)


async def _run(
    seed: Identifier,
    extras: list[Identifier],
    use_ai: bool,
    archiver: Archiver | None = None,
    screenshotter: Screenshotter | None = None,
) -> tuple[Subject, list[Trace], list[Edge], str | None, str | None]:
    orchestrator = _build_orchestrator()
    subject, traces, edges = await orchestrator.investigate(
        seed,
        extra_identifiers=extras,
        archiver=archiver,
        screenshotter=screenshotter,
    )

    summary_md: str | None = None
    hypotheses_md: str | None = None
    if use_ai:
        client = ReasoningClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
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

    return subject, traces, edges, summary_md, hypotheses_md


@app.command()
def investigate(
    value: Annotated[
        str, typer.Argument(help="Identifier value (e.g. a username, domain, profile URL).")
    ],
    kind: Annotated[
        str,
        typer.Option("--kind", "-k", help="Identifier kind: username|email|domain|url|...."),
    ] = "username",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the dossier to a file (.json, .md, .html, or .pdf).",
        ),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Stdout format: md|json|html|pdf."),
    ] = "md",
    ai: Annotated[
        bool,
        typer.Option("--ai", help="Run the LLM reasoning layer (summary + hypotheses)."),
    ] = False,
    extra: Annotated[
        list[str] | None,
        typer.Option(
            "--extra",
            help="Extra identifier in 'kind:value' form (repeatable).",
        ),
    ] = None,
    save: Annotated[
        bool,
        typer.Option(
            "--save",
            help="Persist this dossier to the SQLite store so it can be reopened later.",
        ),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="SQLite database path (defaults to RECKORA_DB_PATH or ./reckora.db).",
        ),
    ] = None,
    archive: Annotated[
        bool,
        typer.Option(
            "--archive",
            help=(
                "Mint a Wayback Machine snapshot for each evidence URL "
                "(best-effort, slow; off by default)."
            ),
        ),
    ] = False,
    screenshot: Annotated[
        bool,
        typer.Option(
            "--screenshot",
            help=(
                "Capture a forensic PNG of each evidence URL via headless Chromium "
                "(requires the 'screenshots' extra; off by default)."
            ),
        ),
    ] = False,
    screenshots_dir: Annotated[
        Path,
        typer.Option(
            "--screenshots-dir",
            help="Directory where captured PNGs are written (created if missing).",
        ),
    ] = Path("screenshots"),
) -> None:
    """Run a Reckora investigation against a seed Identifier."""
    seed = _identifier_from(value, kind)
    extras: list[Identifier] = []
    for raw in extra or []:
        if ":" not in raw:
            raise typer.BadParameter(f"--extra must be in 'kind:value' form, got {raw!r}")
        k, v = raw.split(":", 1)
        extras.append(_identifier_from(v, k))

    archiver: Archiver | None = WaybackArchiver() if archive else None
    screenshotter: Screenshotter | None = (
        _build_screenshotter(screenshots_dir) if screenshot else None
    )

    async def _go() -> tuple[Subject, list[Trace], list[Edge], str | None, str | None]:
        try:
            return await _run(
                seed,
                extras,
                ai,
                archiver=archiver,
                screenshotter=screenshotter,
            )
        finally:
            for resource in (archiver, screenshotter):
                close = getattr(resource, "aclose", None)
                if close is not None:
                    await close()

    subject, traces, edges, summary_md, hypotheses_md = asyncio.run(_go())

    if save:
        with SQLiteSubjectRepository(db or settings.db_path) as repo:
            repo.save(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
            )
        typer.echo(f"saved {subject.id}", err=True)

    payload = _render_dossier(
        _format_from_path(output) if output is not None else fmt,
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
    )
    _emit(payload, output=output)


@app.command(name="list")
def list_dossiers(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of dossiers to list."),
    ] = 20,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite database path."),
    ] = None,
) -> None:
    """List recently saved dossiers, newest first."""
    with SQLiteSubjectRepository(db or settings.db_path) as repo:
        rows = repo.list_recent(limit=limit)
    if not rows:
        typer.echo("(no saved dossiers)")
        return
    for r in rows:
        flags = []
        if r.has_summary:
            flags.append("summary")
        if r.has_hypotheses:
            flags.append("hypotheses")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        typer.echo(
            f"{r.id}\t{r.created_at.isoformat()}\t"
            f"{r.seed_identifier.type.value}:{r.seed_identifier.value}\t"
            f"traces={r.trace_count} edges={r.edge_count}{flag_str}"
        )


@app.command()
def show(
    subject_id: Annotated[str, typer.Argument(help="Subject id (e.g. subj-abcdef123456).")],
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: md|json|html|pdf."),
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the dossier to a file (.json, .md, .html, or .pdf).",
        ),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite database path."),
    ] = None,
) -> None:
    """Render a previously-saved dossier from the SQLite store."""
    with SQLiteSubjectRepository(db or settings.db_path) as repo:
        dossier = repo.get(subject_id)
    if dossier is None:
        raise typer.BadParameter(f"no saved dossier with id {subject_id!r}")

    payload = _render_dossier(
        _format_from_path(output) if output is not None else fmt,
        subject=dossier.subject,
        traces=dossier.traces,
        edges=dossier.edges,
        summary=dossier.summary,
        hypotheses=dossier.hypotheses,
    )
    _emit(payload, output=output)


@app.command()
def delete(
    subject_id: Annotated[str, typer.Argument(help="Subject id to delete.")],
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite database path."),
    ] = None,
) -> None:
    """Delete a saved dossier from the SQLite store."""
    with SQLiteSubjectRepository(db or settings.db_path) as repo:
        removed = repo.delete(subject_id)
    if not removed:
        raise typer.BadParameter(f"no saved dossier with id {subject_id!r}")
    typer.echo(f"deleted {subject_id}")


@app.command()
def version() -> None:
    """Print the Reckora version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
