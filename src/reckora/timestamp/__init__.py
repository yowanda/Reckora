"""Layer 7 — cross-trace Merkle commitment + OpenTimestamps anchor.

The evidence chain (``Evidence.payload_sha256``) gives us per-row
content addressing, but a third-party auditor still has to trust that
*the dossier itself* hasn't been re-arranged after the fact. Layer 7
closes that gap by:

1. Computing a deterministic **Merkle root** over every
   ``payload_sha256`` in the dossier (sorted leaves, double-SHA256
   binary tree — the same algorithm Bitcoin uses for its block-tx
   commitment).
2. Submitting the root to the public **OpenTimestamps** calendar
   network, which aggregates submissions and anchors them in the
   Bitcoin blockchain (no API key, no payment, no PII leaving the host
   beyond the 32-byte root). The returned ``.ots`` receipt is stored
   alongside the dossier on disk.
3. Verifying after the fact: rebuild the Merkle root from the
   dossier's current evidence rows, load the ``.ots`` receipt, confirm
   the receipt commits to the *same* root and report whatever
   attestations the OpenTimestamps chain currently carries (pending
   calendar / Bitcoin block / Litecoin block).

Public API:

* :func:`compute_dossier_root` — pure, no network. Builds the
  Merkle root from a dossier's evidence hashes.
* :func:`stamp_dossier` — submits the root to OpenTimestamps and
  returns an opaque receipt blob. Network-touching.
* :func:`verify_dossier` — rebuilds the root, parses the receipt,
  and returns a :class:`VerificationResult`.
* :class:`TimestampStore` — file-based receipt storage at
  ``<db_dir>/timestamps/<subject_id>.ots``.

Everything below is deliberately decoupled from the SQLite repository
so the timestamping layer can sit on top of any future storage backend
without forcing a schema migration.
"""

from .merkle import EMPTY_MERKLE_ROOT, build_merkle_root, leaves_from_hashes
from .ots import (
    AttestationStatus,
    StampError,
    VerificationResult,
    stamp_root,
    verify_receipt,
)
from .receipt import (
    DossierTimestamp,
    compute_dossier_root,
    stamp_dossier,
    verify_dossier,
)
from .store import TimestampStore

__all__ = [
    "EMPTY_MERKLE_ROOT",
    "AttestationStatus",
    "DossierTimestamp",
    "StampError",
    "TimestampStore",
    "VerificationResult",
    "build_merkle_root",
    "compute_dossier_root",
    "leaves_from_hashes",
    "stamp_dossier",
    "stamp_root",
    "verify_dossier",
    "verify_receipt",
]
