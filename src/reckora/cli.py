"""Reckora command-line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .collectors.github_api import GitHubCollector
from .collectors.web_profile import WebProfileCollector
from .collectors.whois_rdap import WhoisRdapCollector
from .config import settings
from .models.entity import Edge, Identifier, Subject, Trace
from .models.enums import IdentifierType
from .orchestrator import Orchestrator
from .reasoning.client import ReasoningClient
from .reasoning.hypothesize import hypothesize
from .reasoning.summarize import summarize
from .reports.json_export import to_dossier_json
from .reports.markdown import to_dossier_md

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


async def _run(
    seed: Identifier,
    extras: list[Identifier],
    use_ai: bool,
) -> tuple[Subject, list[Trace], list[Edge], str | None, str | None]:
    orchestrator = _build_orchestrator()
    subject, traces, edges = await orchestrator.investigate(
        seed,
        extra_identifiers=extras,
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
        typer.Option("--output", "-o", help="Write the dossier to a file (.json or .md)."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Stdout format: md|json."),
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
) -> None:
    """Run a Reckora investigation against a seed Identifier."""
    seed = _identifier_from(value, kind)
    extras: list[Identifier] = []
    for raw in extra or []:
        if ":" not in raw:
            raise typer.BadParameter(f"--extra must be in 'kind:value' form, got {raw!r}")
        k, v = raw.split(":", 1)
        extras.append(_identifier_from(v, k))

    subject, traces, edges, summary_md, hypotheses_md = asyncio.run(_run(seed, extras, ai))

    if output is not None:
        if output.suffix.lower() == ".json":
            payload = to_dossier_json(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
            )
        else:
            payload = to_dossier_md(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
            )
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {output}")
        return

    if fmt == "json":
        typer.echo(
            to_dossier_json(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
            )
        )
    else:
        typer.echo(
            to_dossier_md(
                subject=subject,
                traces=traces,
                edges=edges,
                summary=summary_md,
                hypotheses=hypotheses_md,
            )
        )


@app.command()
def version() -> None:
    """Print the Reckora version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
