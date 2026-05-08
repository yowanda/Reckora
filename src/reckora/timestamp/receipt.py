"""High-level dossier-aware wrappers around the Merkle + OTS primitives.

The Merkle and OTS modules are deliberately ignorant of Reckora's
domain model — they take and return bytes. This module is the seam
that connects them to :class:`reckora.persistence.repository.SavedDossier`,
so the CLI (and any future API endpoint) doesn't have to know how a
dossier's evidence rows are laid out.

Function summary:

* :func:`compute_dossier_root` — walks every ``trace.evidence.payload_sha256``
  in the dossier and returns the deterministic 32-byte Merkle root.
* :func:`stamp_dossier` — computes the root, stamps it via OpenTimestamps,
  bundles the receipt + leaf metadata into a :class:`DossierTimestamp`
  ready to persist.
* :func:`verify_dossier` — rebuilds the root, compares to the stored
  receipt, returns the OTS verification result.

The :class:`DossierTimestamp` payload is intentionally a dataclass
that round-trips through JSON: callers persist it however they want
(SQLite blob, sidecar file, S3, etc.) without inheriting any
storage-layer dependency from this module.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..persistence.repository import SavedDossier
from .merkle import build_merkle_root, leaves_from_hashes
from .ots import (
    DEFAULT_CALENDARS,
    StampError,
    VerificationResult,
    stamp_root,
    verify_receipt,
)


@dataclass(frozen=True)
class DossierTimestamp:
    """Persistence-friendly snapshot of a dossier's OTS commitment.

    The receipt itself is the canonical ``.ots`` bytes; we keep a
    base64 copy in the JSON form so the same dataclass survives a
    round-trip through any text-only transport (HTTP API, log line,
    audit-trail row).

    ``leaf_hashes`` is captured at stamp time so a later verifier can
    spot-check *which* evidence rows the receipt covered, even after
    the dossier has had new traces appended (Layer 7 commits to
    *snapshot at stamp time*, not "the dossier's current contents").
    """

    subject_id: str
    merkle_root_sha256: str
    receipt_b64: str
    calendars: tuple[str, ...]
    leaf_hashes: tuple[str, ...]
    stamped_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def receipt_bytes(self) -> bytes:
        """Decode the stored ``.ots`` blob back to raw bytes."""
        return base64.b64decode(self.receipt_b64)

    @property
    def merkle_root_bytes(self) -> bytes:
        """Raw 32-byte Merkle root (convenience)."""
        return bytes.fromhex(self.merkle_root_sha256)

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict.

        ``stamped_at`` is rendered in RFC 3339 with explicit UTC so
        the same dossier timestamp can be parsed by any language
        without bespoke date handling.
        """
        return {
            "subject_id": self.subject_id,
            "merkle_root_sha256": self.merkle_root_sha256,
            "receipt_b64": self.receipt_b64,
            "calendars": list(self.calendars),
            "leaf_hashes": list(self.leaf_hashes),
            "stamped_at": self.stamped_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DossierTimestamp:
        """Inverse of :meth:`to_json`."""
        stamped = data["stamped_at"]
        stamped_at = datetime.fromisoformat(stamped) if isinstance(stamped, str) else stamped
        return cls(
            subject_id=str(data["subject_id"]),
            merkle_root_sha256=str(data["merkle_root_sha256"]),
            receipt_b64=str(data["receipt_b64"]),
            calendars=tuple(data.get("calendars", ())),
            leaf_hashes=tuple(data.get("leaf_hashes", ())),
            stamped_at=stamped_at,
        )


def _evidence_hashes(dossier: SavedDossier) -> list[str]:
    """Pull every ``payload_sha256`` from the dossier's traces.

    Traces missing an evidence row (legacy data, pre-Layer-1 dossiers)
    are skipped silently — a partial commitment is still useful.
    """
    out: list[str] = []
    for trace in dossier.traces:
        ev = trace.evidence
        if ev is None:
            continue
        if not ev.payload_sha256:
            continue
        out.append(ev.payload_sha256)
    return out


def compute_dossier_root(dossier: SavedDossier) -> tuple[bytes, list[str]]:
    """Return ``(merkle_root_bytes, leaf_hashes_hex_sorted)``.

    Pure: no network, no disk I/O, no clock. The leaf-hash list is
    returned alongside the root so callers (notably :func:`stamp_dossier`)
    can record exactly which evidence rows were committed to.
    """
    hashes = _evidence_hashes(dossier)
    leaves = leaves_from_hashes(hashes)
    root = build_merkle_root(leaves)
    return root, sorted(hashes)


def stamp_dossier(
    dossier: SavedDossier,
    *,
    calendars: Iterable[str] = DEFAULT_CALENDARS,
    user_agent: str = "Reckora/0.1",
) -> DossierTimestamp:
    """Compute the Merkle root, stamp it, and return a persistable record.

    Raises :class:`StampError` if every calendar fails — the caller
    owns whether to retry or surface the error to a human. The empty
    Merkle root (i.e. dossier with no evidence) is also rejected
    here; stamping a 32-byte zero blob is technically possible but
    has no audit value.
    """
    root, leaf_hashes = compute_dossier_root(dossier)
    if root == b"\x00" * 32:
        raise StampError(f"dossier {dossier.id!r} has no evidence rows to commit to")
    receipt_bytes = stamp_root(
        root,
        calendars=calendars,
        user_agent=user_agent,
    )
    cal_tuple = tuple(calendars) if not isinstance(calendars, tuple) else calendars
    return DossierTimestamp(
        subject_id=dossier.subject.id,
        merkle_root_sha256=root.hex(),
        receipt_b64=base64.b64encode(receipt_bytes).decode("ascii"),
        calendars=cal_tuple,
        leaf_hashes=tuple(leaf_hashes),
    )


def verify_dossier(dossier: SavedDossier, stamp: DossierTimestamp) -> VerificationResult:
    """Rebuild the Merkle root and verify it against the stored receipt.

    The check is *snapshot-faithful*: we rebuild the root from the
    leaf hashes captured at stamp time (``stamp.leaf_hashes``) rather
    than from the dossier's current traces. That means appending a
    new trace later will *not* invalidate the receipt — the receipt
    is a commitment to the dossier as it stood when the user ran
    ``reckora timestamp``, which is the right semantic for an audit
    trail. A separate ``current-state`` mismatch is reported via
    ``valid=False`` only when the receipt itself is inconsistent.
    """
    leaves = leaves_from_hashes(stamp.leaf_hashes)
    rebuilt = build_merkle_root(leaves)
    if rebuilt.hex() != stamp.merkle_root_sha256:
        # Receipt's leaf list disagrees with its own root — corrupt.
        return VerificationResult(
            valid=False,
            status=verify_receipt(stamp.receipt_bytes, rebuilt).status,
            receipt_root_sha256=stamp.merkle_root_sha256,
            expected_root_sha256=rebuilt.hex(),
        )
    # We deliberately ignore the unused dossier parameter beyond the
    # type signature here: snapshot-faithful verification means the
    # current dossier contents are *additional* context, not the
    # source of truth. Future versions may add a "current-state
    # divergence" warning, but that's out of scope for the MVP.
    _ = dossier
    return verify_receipt(stamp.receipt_bytes, rebuilt)
