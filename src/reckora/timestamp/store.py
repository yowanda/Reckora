"""On-disk store for OpenTimestamps receipts.

Receipts are kept *outside* the SQLite dossier database on purpose:

* They are independently meaningful — a third-party auditor can hand
  the bare ``.ots`` file to ``ots verify`` (the upstream CLI) to
  cross-check what Reckora reports.
* They have a different life-cycle from the dossier itself. A pending
  receipt may need to be re-fetched from the calendar weeks later as
  Bitcoin upgrades it from "pending calendar" to "anchored at block
  N"; storing it as a sidecar makes that path trivial.
* The SQLite schema stays untouched, so this layer can ship without
  a migration step.

Each receipt is written as a small JSON envelope holding the base64
``.ots`` blob plus the leaf-hash list captured at stamp time. The
filename is ``<subject_id>.json`` so listing the directory shows
exactly which dossiers have a commitment without parsing every file.
"""

from __future__ import annotations

import json
from pathlib import Path

from .receipt import DossierTimestamp


class TimestampStore:
    """File-system store for :class:`DossierTimestamp` records.

    The store is a thin wrapper around a single directory. We don't
    use SQLite here because (a) we want the receipts to be
    independently inspectable and (b) we don't want to grow a second
    schema-migration story for a feature that emits one tiny JSON
    blob per dossier.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Directory where receipts are persisted."""
        return self._root

    def _path_for(self, subject_id: str) -> Path:
        # Subject ids are deliberately opaque (e.g. ``subj-abcdef123456``);
        # we still defensive-check that the caller hasn't fed us a
        # path-traversal attempt before joining with the store root.
        if "/" in subject_id or "\\" in subject_id or subject_id in {"", ".", ".."}:
            raise ValueError(f"refusing to use unsafe subject id {subject_id!r}")
        return self._root / f"{subject_id}.json"

    def save(self, stamp: DossierTimestamp) -> Path:
        """Persist ``stamp`` and return the on-disk path."""
        path = self._path_for(stamp.subject_id)
        path.write_text(
            json.dumps(stamp.to_json(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def load(self, subject_id: str) -> DossierTimestamp | None:
        """Return the persisted record or ``None`` if there's no receipt."""
        path = self._path_for(subject_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return DossierTimestamp.from_json(data)

    def delete(self, subject_id: str) -> bool:
        """Remove a stored receipt; returns True if a file was deleted."""
        path = self._path_for(subject_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list_subject_ids(self) -> list[str]:
        """Return every subject id that currently has a receipt on disk."""
        return sorted(p.stem for p in self._root.glob("*.json") if p.is_file())
