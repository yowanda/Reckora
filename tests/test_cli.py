"""Smoke tests for the Typer CLI."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from reckora import __version__
from reckora.cli import app
from reckora.collectors.base import Collector
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource

if TYPE_CHECKING:
    from reckora.evidence.anchor import Anchor


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


def test_investigate_with_screenshot_flag(runner: CliRunner) -> None:
    shot = "/screenshots/alice.png"

    class _FakeScreenshotter:
        async def screenshot(self, source_url: str) -> str | None:
            return shot

        async def aclose(self) -> None:
            return None

    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch("reckora.cli._build_screenshotter", return_value=_FakeScreenshotter()),
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
                "--screenshot",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["traces"][0]["evidence"]["screenshot_path"] == shot


def test_investigate_without_screenshot_flag_skips_capture(runner: CliRunner) -> None:
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "json"],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["traces"][0]["evidence"]["screenshot_path"] is None


def test_kind_enum_round_trip() -> None:
    assert IdentifierType("username") is IdentifierType.USERNAME
    assert IdentifierType("domain") is IdentifierType.DOMAIN


# ---------------------------------------------------------------------------
# Layer 7: --anchor + verify-anchor
#
# The anchor path covers two surfaces: the ``--anchor`` flag on
# ``investigate`` (which must compute a Merkle root, submit it to the
# OpenTimestamps fleet, persist it, and render it into every dossier
# format) and the ``verify-anchor`` command (which must rehash the saved
# traces and compare against the persisted root). We patch
# ``reckora.cli.anchor_traces`` so the tests never go to the public
# OpenTimestamps calendars.
# ---------------------------------------------------------------------------


def _fake_anchor_factory(
    leaf_overrides: list[str] | None = None,
) -> Callable[[Sequence[Trace]], Awaitable[Anchor]]:
    """Build a stand-in for ``anchor_traces`` that returns a deterministic
    :class:`Anchor` derived from the actual trace leaves.
    """
    from datetime import UTC, datetime

    from reckora.evidence.anchor import Anchor
    from reckora.evidence.merkle import compute_dossier_root
    from reckora.evidence.timestamp import CalendarReceipt

    async def _fake(traces: Sequence[Trace]) -> Anchor:
        if leaf_overrides is None:
            root, leaves = compute_dossier_root(traces)
        else:
            root = "ab" * 32
            leaves = sorted(leaf_overrides)
        return Anchor(
            merkle_root=root,
            leaf_hashes=leaves,
            receipts=[
                CalendarReceipt(
                    calendar_url="https://stub.calendar.example",
                    receipt_b64="ZmFrZQ==",
                    submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    return _fake


def test_investigate_anchor_flag_emits_anchor_in_dossier(runner: CliRunner) -> None:
    """``--anchor`` plumbs through ``anchor_traces`` and the rendered JSON
    dossier carries the resulting Merkle root + receipts."""
    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch("reckora.cli.anchor_traces", side_effect=_fake_anchor_factory()),
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
                "--anchor",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    anchor = payload["anchor"]
    assert anchor is not None
    assert len(anchor["merkle_root"]) == 64
    # Single trace -> single leaf -> root == leaf.
    assert anchor["merkle_root"] == payload["traces"][0]["evidence"]["payload_sha256"]
    assert anchor["leaf_hashes"] == [payload["traces"][0]["evidence"]["payload_sha256"]]
    assert [r["calendar_url"] for r in anchor["receipts"]] == ["https://stub.calendar.example"]


def test_investigate_anchor_flag_renders_into_markdown_dossier(runner: CliRunner) -> None:
    """The markdown renderer must include a 'Cross-trace anchor' section
    when --anchor is set."""
    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch("reckora.cli.anchor_traces", side_effect=_fake_anchor_factory()),
    ):
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--anchor"],
        )
    assert result.exit_code == 0, result.stdout
    assert "## Cross-trace anchor" in result.stdout
    assert "merkle_root:" in result.stdout
    assert "stub.calendar.example" in result.stdout


def test_investigate_without_anchor_flag_omits_anchor(runner: CliRunner) -> None:
    """Default (no --anchor) must NOT mint or render an anchor — anchoring
    is an opt-in that depends on a public network endpoint."""
    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--format", "json"],
        )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["anchor"] is None
    assert "Cross-trace anchor" not in result.stdout


def test_investigate_anchor_with_no_traces_errors(runner: CliRunner) -> None:
    """Anchoring an investigation that produced zero traces is meaningless
    — the CLI must refuse rather than silently emit a root over an empty
    set."""

    class _EmptyCollector(Collector):
        name = "empty"
        supported = frozenset({"username"})

        async def collect(self, identifier: Identifier) -> list[Trace]:
            return []

    with patch("reckora.cli._build_orchestrator") as build_orch:
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_EmptyCollector()])
        result = runner.invoke(
            app,
            ["investigate", "alice", "--kind", "username", "--anchor"],
        )
    assert result.exit_code != 0
    assert "anchor" in (result.stderr + result.stdout).lower()


def test_investigate_anchor_persists_for_show_and_verify(runner: CliRunner, tmp_path: Path) -> None:
    """--anchor + --save round-trips through SQLite so ``show`` re-renders
    the anchor section and ``verify-anchor`` succeeds against an
    untampered dossier."""
    db_path = tmp_path / "reckora.db"
    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch("reckora.cli.anchor_traces", side_effect=_fake_anchor_factory()),
    ):
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--anchor",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0, save_result.stdout
    saved = json.loads(save_result.stdout)
    saved_id = saved["subject"]["id"]
    expected_root = saved["anchor"]["merkle_root"]

    show_result = runner.invoke(
        app,
        ["show", saved_id, "--db", str(db_path), "--format", "json"],
    )
    assert show_result.exit_code == 0, show_result.stdout
    rehydrated = json.loads(show_result.stdout)
    assert rehydrated["anchor"]["merkle_root"] == expected_root

    verify_result = runner.invoke(app, ["verify-anchor", saved_id, "--db", str(db_path)])
    assert verify_result.exit_code == 0, verify_result.stdout
    assert "VERIFY: OK" in verify_result.stdout
    assert expected_root in verify_result.stdout


def test_verify_anchor_fails_when_recorded_root_mismatches(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Tampering with the anchor's recorded root after persistence must
    flip ``verify-anchor`` from OK to FAIL with a non-zero exit."""
    db_path = tmp_path / "reckora.db"
    # Mint an anchor whose recorded root deliberately disagrees with the
    # actual leaf hashes — simulates the post-hoc tampering verify-anchor
    # is meant to catch.
    with (
        patch("reckora.cli._build_orchestrator") as build_orch,
        patch(
            "reckora.cli.anchor_traces",
            side_effect=_fake_anchor_factory(leaf_overrides=["00" * 32]),
        ),
    ):
        from reckora.orchestrator import Orchestrator

        build_orch.return_value = Orchestrator([_FakeCollector()])
        save_result = runner.invoke(
            app,
            [
                "investigate",
                "alice",
                "--kind",
                "username",
                "--anchor",
                "--save",
                "--db",
                str(db_path),
                "--format",
                "json",
            ],
        )
    assert save_result.exit_code == 0, save_result.stdout
    saved_id = json.loads(save_result.stdout)["subject"]["id"]

    verify_result = runner.invoke(app, ["verify-anchor", saved_id, "--db", str(db_path)])
    assert verify_result.exit_code == 2
    assert "VERIFY: FAIL" in (verify_result.stderr + verify_result.stdout)


def test_verify_anchor_when_no_anchor_recorded_exits_one(runner: CliRunner, tmp_path: Path) -> None:
    """A dossier saved *without* --anchor has nothing to verify — the
    command must exit 1 rather than misleadingly print VERIFY: OK."""
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

    verify_result = runner.invoke(app, ["verify-anchor", saved_id, "--db", str(db_path)])
    assert verify_result.exit_code == 1
    assert "no anchor" in (verify_result.stderr + verify_result.stdout).lower()


def test_verify_anchor_unknown_subject_errors(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "reckora.db"
    result = runner.invoke(app, ["verify-anchor", "subj-missing", "--db", str(db_path)])
    assert result.exit_code != 0
