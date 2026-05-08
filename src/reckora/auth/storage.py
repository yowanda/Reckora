"""On-disk persistence for ChatGPT OAuth credentials.

Layout: a single JSON document at ``~/.config/reckora/auth.json``
(``$XDG_CONFIG_HOME`` honoured), written atomically with mode 0600 so
the access / refresh tokens aren't world-readable. The file is
intentionally separate from any application-level SQLite store so a
``reckora auth logout`` doesn't accidentally drop investigative data.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

from .oauth import OAuthCredentials


def _default_credentials_path() -> Path:
    """Return ``$XDG_CONFIG_HOME/reckora/auth.json`` (or the home
    fallback the XDG spec defines).

    Computed on demand rather than at import so that tests setting
    ``HOME`` / ``XDG_CONFIG_HOME`` in fixtures land in the expected
    sandbox, not in the developer's real config dir.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "reckora" / "auth.json"


DEFAULT_CREDENTIALS_PATH = _default_credentials_path()


def save_credentials(
    creds: OAuthCredentials,
    *,
    path: Path | None = None,
) -> Path:
    """Atomically persist ``creds`` to ``path`` with mode 0600.

    Writes to ``<path>.tmp`` then ``rename``-s onto ``path`` so a
    crash mid-write can never produce a half-written file. Returns
    the resolved final path so callers can echo it.
    """
    target = path or _default_credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expires_at.isoformat(),
        "id_token": creds.id_token,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    # ``Path.chmod`` on the *temp* file pre-rename so the final file
    # is never visible at world-readable perms — important on shared
    # boxes where a curious neighbour user could read tokens between
    # the write and the chmod.
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(target)
    return target


def load_credentials(*, path: Path | None = None) -> OAuthCredentials | None:
    """Load credentials from ``path``, returning ``None`` if absent
    or unparseable.

    A corrupt file is treated as "not logged in" rather than raised
    so a stale half-written credentials file from a previous Reckora
    crash doesn't permanently break the CLI; the user can just
    ``reckora auth login`` again.
    """
    target = path or _default_credentials_path()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return OAuthCredentials(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            id_token=payload.get("id_token"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def delete_credentials(*, path: Path | None = None) -> bool:
    """Remove the credentials file. Returns ``True`` iff something was
    deleted (so CLI ``logout`` can echo ``"already logged out"`` when
    appropriate)."""
    target = path or _default_credentials_path()
    if not target.exists():
        return False
    target.unlink()
    return True
