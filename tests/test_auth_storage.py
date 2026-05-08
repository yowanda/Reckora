"""Unit tests for ``reckora.auth.storage``."""

from __future__ import annotations

import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reckora.auth.oauth import OAuthCredentials
from reckora.auth.storage import (
    DEFAULT_CREDENTIALS_PATH,
    delete_credentials,
    load_credentials,
    save_credentials,
)


def _make_creds(*, access: str = "atk", refresh: str = "rtk") -> OAuthCredentials:
    return OAuthCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        id_token="idt",
    )


def test_default_credentials_path_lives_under_xdg() -> None:
    assert DEFAULT_CREDENTIALS_PATH.name == "auth.json"
    assert DEFAULT_CREDENTIALS_PATH.parent.name == "reckora"


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    creds = _make_creds()

    written = save_credentials(creds, path=target)
    assert written == target
    assert target.exists()

    loaded = load_credentials(path=target)
    assert loaded is not None
    assert loaded.access_token == creds.access_token
    assert loaded.refresh_token == creds.refresh_token
    # ``datetime.fromisoformat`` is lossy below microseconds — compare
    # to the second.
    assert loaded.expires_at.replace(microsecond=0) == creds.expires_at.replace(microsecond=0)
    assert loaded.id_token == creds.id_token


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "auth.json"
    save_credentials(_make_creds(), path=target)
    assert target.exists()


def test_save_writes_atomically_via_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-``write_text`` must never leave a partial ``auth.json``."""
    target = tmp_path / "auth.json"
    target.write_text(
        json.dumps(
            {
                "access_token": "old",
                "refresh_token": "old",
                "expires_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    real_write = Path.write_text
    boom = RuntimeError("disk full")

    def _explode_on_tmp(self: Path, *args: object, **kwargs: object) -> int:
        if self.suffix == ".tmp":
            raise boom
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _explode_on_tmp)

    with pytest.raises(RuntimeError, match="disk full"):
        save_credentials(_make_creds(access="new"), path=target)

    # Original file untouched (no half-written ``new`` token visible).
    surviving = json.loads(target.read_text())
    assert surviving["access_token"] == "old"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX file mode")
def test_save_chmods_to_0600(tmp_path: Path) -> None:
    """On POSIX the file must be unreadable to other users."""
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(), path=target)
    mode = stat.S_IMODE(target.stat().st_mode)
    # Owner read/write, no group/other.
    assert mode == 0o600


def test_save_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(access="first"), path=target)
    save_credentials(_make_creds(access="second"), path=target)
    loaded = load_credentials(path=target)
    assert loaded is not None
    assert loaded.access_token == "second"


def test_load_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert load_credentials(path=tmp_path / "missing.json") is None


def test_load_returns_none_for_corrupt_json(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    target.write_text("not json at all{{{")
    assert load_credentials(path=target) is None


def test_load_returns_none_for_missing_required_keys(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    target.write_text(json.dumps({"access_token": "a"}))  # no refresh_token / expires_at
    assert load_credentials(path=target) is None


def test_load_returns_none_for_invalid_expires_at(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    target.write_text(
        json.dumps(
            {
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": "not-a-datetime",
            }
        )
    )
    assert load_credentials(path=target) is None


def test_delete_removes_file_and_returns_true(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    save_credentials(_make_creds(), path=target)
    assert delete_credentials(path=target) is True
    assert not target.exists()


def test_delete_returns_false_when_already_missing(tmp_path: Path) -> None:
    target = tmp_path / "auth.json"
    assert delete_credentials(path=target) is False


def test_xdg_config_home_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The default credentials path must follow ``$XDG_CONFIG_HOME``.

    Critical so containerised deployments that set ``$XDG_CONFIG_HOME``
    to a writable mount don't end up writing to ``$HOME/.config`` (which
    might be read-only).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "myconfig"))
    # Re-import so the module-level constant is re-evaluated; alternative
    # is to call the private helper, which we verify lives next to the
    # public ``save_credentials`` API.
    from reckora.auth.storage import _default_credentials_path

    path = _default_credentials_path()
    assert path == tmp_path / "myconfig" / "reckora" / "auth.json"
