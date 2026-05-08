"""End-to-end tests for the ``reckora auth ...`` CLI subcommand surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from reckora.auth.login import OAuthLoginError
from reckora.auth.oauth import OAuthCredentials, refresh_credentials
from reckora.auth.storage import load_credentials, save_credentials
from reckora.cli import app


@pytest.fixture
def runner() -> CliRunner:
    # Click 8.3+ removed ``mix_stderr``; ``CliRunner.invoke`` separates
    # stdout / stderr by default now, which is exactly what these
    # tests want.
    return CliRunner()


def _make_creds(
    *,
    access: str = "atk",
    refresh: str = "rtk",
    seconds: int = 3600,
) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        id_token="idt",
    )


def test_auth_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    for cmd in ("login", "status", "logout", "refresh"):
        assert cmd in result.stdout


def test_auth_status_says_not_logged_in_without_creds(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    result = runner.invoke(
        app,
        ["auth", "status", "--credentials", str(target)],
    )
    # Exit code 1 == not logged in (so a shell pipeline can branch on it).
    assert result.exit_code == 1
    assert "not logged in" in result.stdout
    assert str(target) in result.stdout


def test_auth_status_reports_logged_in_with_expiry(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(seconds=7200), path=target)

    result = runner.invoke(
        app,
        ["auth", "status", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert "logged in" in result.stdout
    # 7200s ~ 2h.
    assert " in 2h" in result.stdout
    assert str(target) in result.stdout


def test_auth_status_reports_seconds_for_short_expiry(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(seconds=45), path=target)

    result = runner.invoke(
        app,
        ["auth", "status", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert "in " in result.stdout
    assert "s at" in result.stdout


def test_auth_status_reports_minutes_for_intermediate_expiry(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(seconds=900), path=target)
    result = runner.invoke(
        app,
        ["auth", "status", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert " in 15m" in result.stdout


def test_auth_status_reports_expired_for_negative_delta(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(seconds=-10), path=target)
    result = runner.invoke(
        app,
        ["auth", "status", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert "expired" in result.stdout


def test_auth_logout_removes_existing_credentials(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(), path=target)
    assert target.exists()

    result = runner.invoke(
        app,
        ["auth", "logout", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert "logged out" in result.stdout
    assert not target.exists()


def test_auth_logout_is_idempotent_when_already_logged_out(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    result = runner.invoke(
        app,
        ["auth", "logout", "--credentials", str(target)],
    )
    assert result.exit_code == 0
    assert "already logged out" in result.stdout


def test_auth_login_persists_creds_on_success(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    fake = _make_creds(access="from-flow", refresh="rfrom-flow")

    async def _fake_interactive_login(**kwargs: object) -> OAuthCredentials:
        del kwargs
        return fake

    with patch("reckora.cli.interactive_login", _fake_interactive_login):
        result = runner.invoke(
            app,
            ["auth", "login", "--credentials", str(target), "--timeout", "5"],
        )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "logged in" in result.stdout

    loaded = load_credentials(path=target)
    assert loaded is not None
    assert loaded.access_token == "from-flow"
    assert loaded.refresh_token == "rfrom-flow"


def test_auth_login_surfaces_flow_failure(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"

    async def _explode(**kwargs: object) -> OAuthCredentials:
        del kwargs
        raise OAuthLoginError("user closed browser")

    with patch("reckora.cli.interactive_login", _explode):
        result = runner.invoke(
            app,
            ["auth", "login", "--credentials", str(target), "--timeout", "5"],
        )

    assert result.exit_code == 1
    assert "user closed browser" in result.stderr
    assert not target.exists()


def test_auth_refresh_errors_when_not_logged_in(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    result = runner.invoke(
        app,
        ["auth", "refresh", "--credentials", str(target)],
    )
    assert result.exit_code == 1
    assert "not logged in" in result.stderr


def test_auth_refresh_writes_new_token(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(access="old", refresh="rold"), path=target)

    new = _make_creds(access="new", refresh="rnew", seconds=1800)

    async def _fake_refresh(refresh_token: str, *, client: httpx.AsyncClient) -> OAuthCredentials:
        del client
        assert refresh_token == "rold"
        return new

    with patch("reckora.cli.refresh_credentials", _fake_refresh):
        result = runner.invoke(
            app,
            ["auth", "refresh", "--credentials", str(target)],
        )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "logged in" in result.stdout

    loaded = load_credentials(path=target)
    assert loaded is not None
    assert loaded.access_token == "new"
    assert loaded.refresh_token == "rnew"


def test_auth_refresh_surfaces_token_endpoint_error(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(), path=target)

    async def _explode(refresh_token: str, *, client: httpx.AsyncClient) -> OAuthCredentials:
        del refresh_token, client
        raise httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
            response=httpx.Response(401),
        )

    with patch("reckora.cli.refresh_credentials", _explode):
        result = runner.invoke(
            app,
            ["auth", "refresh", "--credentials", str(target)],
        )

    assert result.exit_code == 1
    assert "refresh failed" in result.stderr


# ---------------------------------------------------------------------- compat


# Ensure the imports remain valid even if the CLI helpers are renamed; this
# guards us from a regression where ``refresh_credentials`` is removed from the
# CLI module's public surface but the patch above fails silently.
def test_cli_module_re_exports_oauth_helpers() -> None:
    from reckora import cli

    # The patches in the tests above target ``reckora.cli.<name>``;
    # if either symbol stops being a module-level attribute the patch
    # is silently a no-op, so verify the names exist explicitly.
    assert hasattr(cli, "interactive_login")
    assert hasattr(cli, "refresh_credentials")
    # Identity check: the CLI must use the same callable as the auth
    # package, not a stale local copy.
    assert getattr(cli, "refresh_credentials") is refresh_credentials  # noqa: B009
