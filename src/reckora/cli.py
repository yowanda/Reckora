"""Reckora command-line interface."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .agent import AgentLoop, Researcher, ToolBudget
from .auth.login import OAuthLoginError, interactive_login
from .auth.oauth import OAuthCredentials, refresh_credentials
from .auth.storage import (
    DEFAULT_CREDENTIALS_PATH,
    delete_credentials,
    load_credentials,
    save_credentials,
)
from .collectors.avatar import AvatarCollector
from .collectors.breach import BreachCollector
from .collectors.dns_records import DNSCollector
from .collectors.doc_leak import DocLeakCollector
from .collectors.email import EmailCollector
from .collectors.github_api import GitHubCollector
from .collectors.gravatar import GravatarCollector
from .collectors.hackernews import HackerNewsCollector
from .collectors.keybase import KeybaseCollector
from .collectors.phone import PhoneCollector
from .collectors.reddit import RedditCollector
from .collectors.social_presence import SocialPresenceProbeCollector
from .collectors.tiktok import TikTokCollector
from .collectors.wallet_btc import BitcoinChainCollector
from .collectors.wallet_eth import EthereumChainCollector
from .collectors.wallet_sol import SolanaChainCollector
from .collectors.web_profile import WebProfileCollector
from .collectors.whois_rdap import WhoisRdapCollector
from .collectors.x_twitter import XCollector
from .config import settings
from .evidence.anchor import Anchor, anchor_traces
from .evidence.archive import Archiver, WaybackArchiver
from .evidence.screenshot import Screenshotter
from .models.detect import detect_identifier_kind
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
from .reports.ndjson import to_dossier_ndjson
from .reports.pdf import to_dossier_pdf

app = typer.Typer(
    help="Reckora — AI-Native OSINT Investigation System.",
    no_args_is_help=True,
    add_completion=False,
)
auth_app = typer.Typer(
    help=(
        "Manage ChatGPT OAuth credentials so the reasoning layer can run "
        "on a ChatGPT Plus / Pro subscription instead of an API key."
    ),
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


_InvestigationResult = tuple[
    Subject, list[Trace], list[Edge], str | None, str | None, Anchor | None
]


def _identifier_from(value: str, kind: str | None) -> Identifier:
    """Build an Identifier from a CLI ``value`` + optional ``--kind``.

    When ``kind`` is omitted (or the literal ``"auto"``), we run the
    auto-detector. A genuinely undetectable value raises
    :class:`typer.BadParameter` so the user knows to pass ``--kind``
    explicitly instead of getting a confusing collector-level error.
    """
    stripped = value.strip()
    if kind is None or kind == "auto":
        detected = detect_identifier_kind(stripped)
        if detected is None:
            valid = ", ".join(e.value for e in IdentifierType)
            raise typer.BadParameter(
                f"could not auto-detect identifier kind for {value!r}; "
                f"pass --kind explicitly. Valid kinds: {valid}"
            )
        return Identifier(type=detected, value=stripped)
    try:
        identifier_type = IdentifierType(kind)
    except ValueError as exc:
        valid = ", ".join(e.value for e in IdentifierType)
        raise typer.BadParameter(
            f"unknown identifier kind {kind!r}; expected one of: {valid}"
        ) from exc
    return Identifier(type=identifier_type, value=stripped)


def _build_orchestrator(*, breach_enabled: bool = False) -> Orchestrator:
    collectors: list[object] = [
        GitHubCollector(token=settings.github_token),
        HackerNewsCollector(),
        KeybaseCollector(),
        GravatarCollector(),
        RedditCollector(),
        XCollector(),
        TikTokCollector(),
        SocialPresenceProbeCollector(),
        WhoisRdapCollector(),
        DNSCollector(),
        WebProfileCollector(),
        PhoneCollector(),
        EmailCollector(),
        BitcoinChainCollector(),
        EthereumChainCollector(api_key=settings.etherscan_api_key),
        SolanaChainCollector(),
        AvatarCollector(),
    ]
    if breach_enabled:
        # Feature-flagged opt-in: only added when --breach is set so that
        # stock investigations never call HIBP / doc-leak. The HIBP
        # collector itself additionally short-circuits to [] when
        # ``HIBP_API_KEY`` is unset; the doc-leak collector probes public
        # search endpoints (no key required) so it always runs once the
        # toggle is on.
        collectors.append(BreachCollector(api_key=settings.hibp_api_key))
        collectors.append(DocLeakCollector())
    return Orchestrator(collectors)  # type: ignore[arg-type]


def _render_dossier(
    fmt: str,
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None,
    hypotheses: str | None,
    anchor: Anchor | None = None,
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
            anchor=anchor,
        )
    if fmt == "ndjson":
        return to_dossier_ndjson(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
            anchor=anchor,
        )
    if fmt == "html":
        return to_dossier_html(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
            anchor=anchor,
        )
    if fmt == "md":
        return to_dossier_md(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
            anchor=anchor,
        )
    if fmt == "pdf":
        return to_dossier_pdf(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
            anchor=anchor,
        )
    raise typer.BadParameter(
        f"unknown format {fmt!r}; expected one of: md, json, ndjson, html, pdf"
    )


def _format_from_path(path: Path) -> str:
    """Infer dossier format from output file extension."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".ndjson", ".jsonl"}:
        return "ndjson"
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
    breach_enabled: bool = False,
    anchor_enabled: bool = False,
    ai_iterations: int = 0,
    ai_tools: bool = False,
    ai_tool_calls: int = 8,
) -> tuple[Subject, list[Trace], list[Edge], str | None, str | None, Anchor | None]:
    orchestrator = _build_orchestrator(breach_enabled=breach_enabled)
    subject, traces, edges = await orchestrator.investigate(
        seed,
        extra_identifiers=extras,
        archiver=archiver,
        screenshotter=screenshotter,
    )

    anchor: Anchor | None = None
    if anchor_enabled:
        if not traces:
            raise typer.BadParameter(
                "--anchor requires at least one trace, but the investigation produced none."
            )
        anchor = await anchor_traces(traces)

    summary_md: str | None = None
    hypotheses_md: str | None = None
    if use_ai:
        # Pre-flight check: if the user asks for ``--ai`` without
        # *any* credentials configured, fail loudly *before* spending
        # a network round-trip on the orchestrator's collectors. We
        # check both env-var-provided API keys and on-disk OAuth
        # credentials so users on either auth path get the same
        # ergonomic.
        if not settings.openai_api_key and load_credentials() is None:
            raise typer.BadParameter(
                "--ai requires either OPENAI_API_KEY or a ChatGPT OAuth login. "
                "Run `reckora auth login` to authenticate with your "
                "ChatGPT Plus / Pro account, or set OPENAI_API_KEY."
            )
        client = ReasoningClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            oauth_model=settings.openai_oauth_model,
        )
        try:
            if ai_iterations >= 1:
                # Recursive AgentLoop: drives the propose/verify/collect
                # cycle ``ai_iterations`` times, optionally letting the
                # LLM call ``web_search``/``fetch_url`` between rounds
                # to gather evidence the rule-based collectors missed.
                researcher: Researcher | None = None
                if ai_tools:
                    # Both OPENAI_API_KEY and ChatGPT OAuth drive
                    # function calling now (the latter via the Codex
                    # Responses API). ReasoningClient resolves
                    # credentials lazily and the AgentLoop disables
                    # the researcher gracefully if neither is
                    # configured.
                    researcher = Researcher.with_default_tools(
                        client=client,
                        seed=seed,
                        budget=ToolBudget(calls_remaining=ai_tool_calls),
                    )
                loop = AgentLoop(
                    orchestrator,
                    client,
                    max_iterations=ai_iterations,
                    researcher=researcher,
                )
                result = await loop.run(seed)
                # Replace the orchestrator-only state with the loop's
                # expanded state. The loop re-runs correlation each
                # iteration so its trace/edge sets are authoritative.
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
        finally:
            await client.aclose()

    return subject, traces, edges, summary_md, hypotheses_md, anchor


@app.command()
def investigate(
    value: Annotated[
        str, typer.Argument(help="Identifier value (e.g. a username, domain, profile URL).")
    ],
    kind: Annotated[
        str | None,
        typer.Option(
            "--kind",
            "-k",
            help=(
                "Identifier kind: username|email|domain|url|phone|wallet|avatar. "
                "Auto-detected from VALUE when omitted."
            ),
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the dossier to a file (.json, .ndjson, .md, .html, or .pdf).",
        ),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Stdout format: md|json|ndjson|html|pdf."),
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
    breach: Annotated[
        bool,
        typer.Option(
            "--breach",
            help=(
                "Enable the data-leak surface: HIBP breach lookup for "
                "email identifiers (requires HIBP_API_KEY) plus a public "
                "doc-share / paste-site probe (Scribd, pdfcoffee, "
                "pdfslide, SlideShare, Issuu, 4shared, archive.org, "
                "Pastebin) for username + email. Off by default."
            ),
        ),
    ] = False,
    anchor: Annotated[
        bool,
        typer.Option(
            "--anchor",
            help=(
                "Compute a cross-trace Merkle root and submit it to public "
                "OpenTimestamps calendars for tamper-evident timestamping."
            ),
        ),
    ] = False,
    ai_iterations: Annotated[
        int,
        typer.Option(
            "--ai-iterations",
            help=(
                "Number of recursive AgentLoop rounds (0 = passive summary only). "
                "Each round lets the LLM propose follow-up identifiers, runs them "
                "through the verifier and confidence-floor gate, then re-correlates."
            ),
            min=0,
        ),
    ] = 0,
    ai_tools: Annotated[
        bool,
        typer.Option(
            "--ai-tools",
            help=(
                "Allow the AgentLoop's LLM to call web_search and fetch_url so it "
                "can gather evidence beyond what the rule-based collectors found. "
                "Requires --ai-iterations >= 1 and OPENAI_API_KEY."
            ),
        ),
    ] = False,
    ai_tool_calls: Annotated[
        int,
        typer.Option(
            "--ai-tool-calls",
            help="Per-iteration tool-call budget when --ai-tools is enabled.",
            min=1,
        ),
    ] = 8,
) -> None:
    """Run a Reckora investigation against a seed Identifier."""
    if ai_iterations >= 1 and not ai:
        raise typer.BadParameter("--ai-iterations >= 1 requires --ai")
    if ai_tools and ai_iterations < 1:
        raise typer.BadParameter("--ai-tools requires --ai-iterations >= 1")
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

    async def _go() -> _InvestigationResult:
        try:
            return await _run(
                seed,
                extras,
                ai,
                archiver=archiver,
                screenshotter=screenshotter,
                breach_enabled=breach,
                anchor_enabled=anchor,
                ai_iterations=ai_iterations,
                ai_tools=ai_tools,
                ai_tool_calls=ai_tool_calls,
            )
        finally:
            for resource in (archiver, screenshotter):
                close = getattr(resource, "aclose", None)
                if close is not None:
                    await close()

    subject, traces, edges, summary_md, hypotheses_md, anchor_record = asyncio.run(_go())

    if save:
        with SQLiteSubjectRepository(db or settings.db_path) as repo:
            repo.save(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
                anchor=anchor_record,
            )
        typer.echo(f"saved {subject.id}", err=True)

    payload = _render_dossier(
        _format_from_path(output) if output is not None else fmt,
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary_md,
        hypotheses=hypotheses_md,
        anchor=anchor_record,
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
        typer.Option("--format", "-f", help="Output format: md|json|ndjson|html|pdf."),
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the dossier to a file (.json, .ndjson, .md, .html, or .pdf).",
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
        anchor=dossier.anchor,
    )
    _emit(payload, output=output)


@app.command(name="verify-anchor")
def verify_anchor(
    subject_id: Annotated[str, typer.Argument(help="Subject id (e.g. subj-abcdef123456).")],
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite database path."),
    ] = None,
) -> None:
    """Verify a saved dossier's cross-trace Merkle anchor.

    Recomputes the Merkle root from the dossier's persisted traces and
    compares it against the root recorded in the anchor at investigation
    time. A mismatch means the dossier's evidence (or the persisted
    anchor) has been tampered with since anchoring; a match plus the
    OpenTimestamps calendar receipts is the cryptographic basis for
    saying "these traces existed in this exact form on or before
    ``anchor.created_at``".
    """
    from .evidence.merkle import compute_dossier_root

    with SQLiteSubjectRepository(db or settings.db_path) as repo:
        dossier = repo.get(subject_id)
    if dossier is None:
        raise typer.BadParameter(f"no saved dossier with id {subject_id!r}")
    if dossier.anchor is None:
        typer.echo(
            f"{subject_id} has no anchor — re-run investigate with --anchor.",
            err=True,
        )
        raise typer.Exit(code=1)

    recomputed_root, recomputed_leaves = compute_dossier_root(dossier.traces)
    anchor = dossier.anchor

    typer.echo(f"subject:        {subject_id}")
    typer.echo(f"anchored:       {anchor.created_at.isoformat()}")
    typer.echo(f"recorded root:  {anchor.merkle_root}")
    typer.echo(f"recomputed:     {recomputed_root}")
    typer.echo(f"leaves:         {len(recomputed_leaves)} (recorded {len(anchor.leaf_hashes)})")
    if anchor.receipts:
        typer.echo("calendars:")
        for receipt in anchor.receipts:
            typer.echo(
                f"  - {receipt.calendar_url}  (submitted {receipt.submitted_at.isoformat()})"
            )
    else:
        typer.echo("calendars:      (none responded — root preserved locally)")

    if anchor.merkle_root != recomputed_root or sorted(anchor.leaf_hashes) != recomputed_leaves:
        typer.echo("\nVERIFY: FAIL — recomputed root does not match the anchor.", err=True)
        raise typer.Exit(code=2)
    typer.echo("\nVERIFY: OK — recomputed root matches the anchor.")


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


# ----------------------------------------------------------------------
#  reckora auth ...
# ----------------------------------------------------------------------
#
# The auth subcommand surface lets a user log in to a ChatGPT Plus /
# Pro account so Reckora's reasoning layer can run without an
# OpenAI Platform API key. Backed by the OAuth helpers in
# :mod:`reckora.auth`. Each command keeps its side effects scoped to
# the credentials file (``~/.config/reckora/auth.json`` by default)
# so a misuse can be undone with a single ``reckora auth logout``.


_AUTH_TOS_BANNER = (
    "Reckora is about to open your default browser at "
    "auth.openai.com to obtain a ChatGPT OAuth token. The same "
    "client_id is used by the official OpenAI Codex CLI; usage "
    "counts against your ChatGPT Plus / Pro plan, not your "
    "Platform API tier. Press Ctrl+C in the next 5 minutes to "
    "abort."
)


def _format_credentials_status(creds: OAuthCredentials, *, path: Path) -> str:
    """Render a human-readable single-line status for a credentials object."""
    import math

    now = datetime.now(UTC)
    delta = creds.expires_at - now
    if delta.total_seconds() <= 0:
        when = "expired"
    else:
        # Round *up* to whole seconds: if a token was minted to expire
        # in exactly 7200s, the few microseconds of drift between
        # construction and rendering would otherwise downgrade the
        # readout to ``1h59m``. ``math.ceil`` keeps the display
        # truthful — the token really does have at least that much
        # life left.
        secs = math.ceil(delta.total_seconds())
        if secs < 60:
            when = f"in {secs}s"
        elif secs < 3600:
            when = f"in {secs // 60}m"
        elif secs % 3600 == 0:
            when = f"in {secs // 3600}h"
        else:
            when = f"in {secs // 3600}h{(secs % 3600) // 60}m"
    return (
        f"logged in (token expires {when} at "
        f"{creds.expires_at.isoformat(timespec='seconds')}; "
        f"credentials at {path})"
    )


@auth_app.command(name="login")
def auth_login(
    credentials_path: Annotated[
        Path | None,
        typer.Option(
            "--credentials",
            help="Override the credentials file path (defaults to ~/.config/reckora/auth.json).",
        ),
    ] = None,
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            help="Seconds to wait for the browser callback before giving up.",
        ),
    ] = 300,
) -> None:
    """Open a browser, complete a ChatGPT OAuth flow, and save credentials."""
    typer.echo(_AUTH_TOS_BANNER)
    target = credentials_path or DEFAULT_CREDENTIALS_PATH
    try:
        creds = asyncio.run(interactive_login(timeout=float(timeout)))
    except OAuthLoginError as exc:
        typer.echo(f"login failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    save_credentials(creds, path=target)
    typer.echo(_format_credentials_status(creds, path=target))


@auth_app.command(name="status")
def auth_status(
    credentials_path: Annotated[
        Path | None,
        typer.Option("--credentials", help="Override the credentials file path."),
    ] = None,
) -> None:
    """Show whether Reckora has a stored ChatGPT OAuth login."""
    target = credentials_path or DEFAULT_CREDENTIALS_PATH
    creds = load_credentials(path=target)
    if creds is None:
        typer.echo(f"not logged in (no credentials at {target})")
        raise typer.Exit(code=1)
    typer.echo(_format_credentials_status(creds, path=target))


@auth_app.command(name="logout")
def auth_logout(
    credentials_path: Annotated[
        Path | None,
        typer.Option("--credentials", help="Override the credentials file path."),
    ] = None,
) -> None:
    """Forget the stored ChatGPT OAuth credentials."""
    target = credentials_path or DEFAULT_CREDENTIALS_PATH
    if delete_credentials(path=target):
        typer.echo(f"logged out (removed {target})")
    else:
        typer.echo("already logged out")


@auth_app.command(name="refresh")
def auth_refresh(
    credentials_path: Annotated[
        Path | None,
        typer.Option("--credentials", help="Override the credentials file path."),
    ] = None,
) -> None:
    """Force a refresh of the access token using the stored refresh token."""
    target = credentials_path or DEFAULT_CREDENTIALS_PATH
    creds = load_credentials(path=target)
    if creds is None:
        typer.echo(f"not logged in (no credentials at {target})", err=True)
        raise typer.Exit(code=1)

    async def _refresh() -> OAuthCredentials:
        # Local import keeps httpx out of `reckora` import path on
        # ``--help`` / ``version`` invocations.
        import httpx

        async with httpx.AsyncClient() as http:
            return await refresh_credentials(creds.refresh_token, client=http)

    try:
        refreshed = asyncio.run(_refresh())
    except Exception as exc:
        # Surface the upstream error verbatim — typically an
        # ``HTTPStatusError`` or ``invalid_grant`` from the token
        # endpoint, both of which carry useful diagnostic detail.
        typer.echo(f"refresh failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    save_credentials(refreshed, path=target)
    typer.echo(_format_credentials_status(refreshed, path=target))


if __name__ == "__main__":
    app()
