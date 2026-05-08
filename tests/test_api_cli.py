"""Smoke tests for the ``reckora-api`` Typer command."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reckora_api.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A clean RECKORA_API_* environment with a per-test SQLite path."""
    monkeypatch.setenv("RECKORA_API_JWT_SECRET", "test-secret-do-not-use-but-long-enough-for-hs256")
    monkeypatch.setenv("RECKORA_DB_PATH", str(tmp_path / "reckora.db"))
    return dict(os.environ)


def test_help_exits_cleanly(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "create-user" in result.stdout
    assert "serve" in result.stdout


def test_create_user_inserts_into_store(
    runner: CliRunner,
    env: dict[str, str],
) -> None:
    result = runner.invoke(
        app,
        ["create-user", "alice", "--password", "supersecret123"],
    )
    assert result.exit_code == 0, result.stdout
    assert "created user alice" in result.stdout

    # Same name a second time should be rejected (uniqueness).
    second = runner.invoke(
        app,
        ["create-user", "alice", "--password", "supersecret123"],
    )
    assert second.exit_code != 0


def test_create_user_rejects_short_password(
    runner: CliRunner,
    env: dict[str, str],
) -> None:
    result = runner.invoke(
        app,
        ["create-user", "alice", "--password", "short"],
    )
    assert result.exit_code != 0


def test_serve_refuses_without_jwt_secret(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECKORA_API_JWT_SECRET", raising=False)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code != 0
    combined = result.stdout
    if hasattr(result, "stderr"):
        combined += result.stderr or ""
    assert "RECKORA_API_JWT_SECRET" in combined


def test_create_app_refuses_without_secret() -> None:
    """The factory itself should fail-closed if the secret is empty."""
    from reckora_api.config import APISettings
    from reckora_api.main import create_app

    with pytest.raises(RuntimeError, match="RECKORA_API_JWT_SECRET"):
        create_app(APISettings(jwt_secret=""))
