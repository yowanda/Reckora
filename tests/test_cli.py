"""Smoke tests for the Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from reckora import __version__
from reckora.cli import app
from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource


class _FakeCollector(Collector):
    name = "fake"
    supported = frozenset({"username"})

    async def collect(self, identifier: Identifier) -> list[Trace]:
        evidence = make_evidence(
            f"https://fake/{identifier.value}",
            {"login": identifier.value},
        )
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.WEB_PROFILE,
                fields={"platform": "fake", "display_name": identifier.value},
                evidence=evidence,
            )
        ]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_investigate_md_to_stdout(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(app, ["investigate", "alice", "--kind", "username"])
    assert result.exit_code == 0, result.stdout
    assert "Reckora dossier" in result.stdout
    assert "alice" in result.stdout


def test_investigate_writes_json_to_file(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "dossier.json"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--output", str(out)],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(out.read_text())
    assert payload["subject"]["seed_identifier"]["value"] == "alice"
    assert len(payload["traces"]) == 1


def test_investigate_with_extras(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--extra",
                "username:al1ce",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    values = {i["value"] for i in payload["subject"]["identifiers"]}
    assert {"alice", "al1ce"}.issubset(values)


def test_investigate_invalid_kind(runner: CliRunner) -> None:
    result = runner.invoke(app, ["investigate", "alice", "--kind", "nonsense"])
    assert result.exit_code != 0
    assert "unknown identifier kind" in (result.stderr + result.stdout).lower()


def test_investigate_invalid_extra_format(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        ["investigate", "alice", "--kind", "username", "--extra", "no-colon"],
    )
    assert result.exit_code != 0


def test_kind_enum_round_trip() -> None:
    assert IdentifierType("username") is IdentifierType.USERNAME
    assert IdentifierType("domain") is IdentifierType.DOMAIN
