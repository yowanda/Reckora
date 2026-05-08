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


def test_investigate_save_then_list_and_show(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0, save_result.stdout
    saved_payload = json.loads(save_result.stdout)
    saved_id = saved_payload["subject"]["id"]

    list_result = runner.invoke(app, ["list", "--db", str(db_path)])
    assert list_result.exit_code == 0, list_result.stdout
    assert saved_id in list_result.stdout
    assert "username:alice" in list_result.stdout

    show_result = runner.invoke(
        app,
        ["show", saved_id, "--db", str(db_path), "--format", "json"],
    )
    assert show_result.exit_code == 0, show_result.stdout
    shown = json.loads(show_result.stdout)
    assert shown["subject"]["id"] == saved_id
    assert shown["subject"]["seed_identifier"]["value"] == "alice"


def test_list_empty_store(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    result = runner.invoke(app, ["list", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "no saved dossiers" in result.stdout


def test_show_unknown_id_errors(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    result = runner.invoke(app, ["show", "subj-missing", "--db", str(db_path)])
    assert result.exit_code != 0


def test_delete_dossier(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0
    saved_id = json.loads(save_result.stdout)["subject"]["id"]

    delete_result = runner.invoke(app, ["delete", saved_id, "--db", str(db_path)])
    assert delete_result.exit_code == 0
    assert saved_id in delete_result.stdout

    second_delete = runner.invoke(app, ["delete", saved_id, "--db", str(db_path)])
    assert second_delete.exit_code != 0


def test_investigate_html_to_stdout(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "html"],
        )
    assert result.exit_code == 0, result.stdout
    assert "<!DOCTYPE html>" in result.stdout
    assert "username:alice" in result.stdout


def test_investigate_writes_html_to_file(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "dossier.html"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--output", str(out)],
        )
    assert result.exit_code == 0, result.stdout
    body = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in body
    assert "alice" in body


def test_investigate_unknown_format_errors(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "xml"],
        )
    assert result.exit_code != 0


def test_show_html_format(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0
    saved_id = json.loads(save_result.stdout)["subject"]["id"]

    show_result = runner.invoke(
        app,
        ["show", saved_id, "--db", str(db_path), "--format", "html"],
    )
    assert show_result.exit_code == 0
    assert "<!DOCTYPE html>" in show_result.stdout
    assert saved_id in show_result.stdout


def test_investigate_with_archive_flag(runner: CliRunner) -> None:
    snap = "https://web.archive.org/web/2026/https://fake/alice"

    class _FakeArchiver:
        async def archive(self, source_url: str) -> str | None:
            return snap

        async def aclose(self) -> None:
            return None

    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch("reckora.cli.WaybackArchiver", return_value=_FakeArchiver()),
    ):
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--archive",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["traces"][0]["evidence"]["archive_url"] == snap


def test_investigate_without_archive_flag_skips_archiving(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "json"],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["traces"][0]["evidence"]["archive_url"] is None


def test_investigate_writes_pdf_to_file(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "dossier.pdf"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--output", str(out)],
        )
    assert result.exit_code == 0, result.stdout
    body = out.read_bytes()
    assert body.startswith(b"%PDF-")
    assert b"%%EOF" in body[-32:]


def test_investigate_pdf_to_stdout(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "pdf"],
        )
    assert result.exit_code == 0, result.stdout
    body = result.stdout_bytes
    assert body.startswith(b"%PDF-")


def test_show_pdf_to_file(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    out = tmp_path / "dossier.pdf"
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0
    saved_id = json.loads(save_result.stdout)["subject"]["id"]

    show_result = runner.invoke(
        app,
        ["show", saved_id, "--db", str(db_path), "--output", str(out)],
    )
    assert show_result.exit_code == 0, show_result.stdout
    body = out.read_bytes()
    assert body.startswith(b"%PDF-")


def test_kind_enum_round_trip() -> None:
    assert IdentifierType("username") is IdentifierType.USERNAME
    assert IdentifierType("domain") is IdentifierType.DOMAIN
