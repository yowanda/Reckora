"""High-level cross-trace anchoring: Merkle root + OpenTimestamps receipts.

This module is the orchestrator-friendly seam over
:mod:`reckora.evidence.merkle` (pure compute) and
:mod:`reckora.evidence.timestamp` (HTTP I/O). Callers hand it a list of
traces and get back a tamper-evident :class:`Anchor` they can persist next
to the dossier.

An :class:`Anchor` carries:

* ``merkle_root`` — the dossier-wide hex SHA-256 the calendars committed to.
* ``leaf_hashes`` — the *sorted* leaf list the root commits over, so a
  verifier can recompute the root from the persisted dossier without having
  to re-derive sort order from raw traces.
* ``receipts`` — one :class:`CalendarReceipt` per calendar that responded.
* ``created_at`` — when the anchor was minted (server-side wall clock, UTC).

The anchor is *opt-in* — investigations only mint one when the caller
explicitly asks for it (``--anchor`` on the CLI, ``anchor: true`` on the
API). That keeps the default investigation path free of any external
dependency on the OpenTimestamps calendar fleet.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.entity import Trace
from .merkle import compute_dossier_root
from .timestamp import (
    DEFAULT_CALENDARS,
    CalendarReceipt,
    OpenTimestampsClient,
)


class Anchor(BaseModel):
    """A persisted tamper-evident anchor for one dossier.

    The model is frozen so it can be embedded in dossier payloads without
    callers worrying about post-hoc mutation. Receipts are stored as a
    list because ``submit_digest`` calls multiple calendars in parallel
    and any subset may have responded.
    """

    model_config = ConfigDict(frozen=True)

    merkle_root: str
    leaf_hashes: list[str]
    receipts: list[CalendarReceipt] = Field(default_factory=list)
    created_at: datetime


async def anchor_traces(
    traces: list[Trace],
    *,
    client: OpenTimestampsClient | None = None,
    calendars: tuple[str, ...] = DEFAULT_CALENDARS,
) -> Anchor:
    """Anchor a list of traces and return a fully-populated :class:`Anchor`.

    Computes the Merkle root locally, then submits the root digest to the
    configured OpenTimestamps calendars (default: the same fleet the
    upstream ``ots`` CLI uses). Calendar failures are best-effort — the
    returned anchor still contains the locally-computed root so a verifier
    can re-derive it from the dossier even when *every* calendar is down.

    Pass a pre-built ``client`` when you already have an
    :class:`OpenTimestampsClient` (e.g. inside a FastAPI request that wants
    to share a connection pool); otherwise a fresh client is created and
    closed for the duration of the call.
    """
    root, leaves = compute_dossier_root(traces)

    owned_client = client is None
    ts_client = client or OpenTimestampsClient(calendars=calendars)
    try:
        receipts = await ts_client.submit_digest(root)
    finally:
        if owned_client:
            await ts_client.aclose()

    return Anchor(
        merkle_root=root,
        leaf_hashes=leaves,
        receipts=receipts,
        created_at=datetime.now(UTC),
    )
