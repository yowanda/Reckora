"""Evidence chain — canonical hashing, evidence record construction."""

from __future__ import annotations

from .anchor import Anchor, anchor_traces
from .chain import canonical_payload, hash_payload, make_evidence
from .merkle import compute_dossier_root, merkle_root, trace_leaves
from .timestamp import (
    DEFAULT_CALENDARS,
    CalendarReceipt,
    OpenTimestampsClient,
)

__all__ = [
    "DEFAULT_CALENDARS",
    "Anchor",
    "CalendarReceipt",
    "OpenTimestampsClient",
    "anchor_traces",
    "canonical_payload",
    "compute_dossier_root",
    "hash_payload",
    "make_evidence",
    "merkle_root",
    "trace_leaves",
]
