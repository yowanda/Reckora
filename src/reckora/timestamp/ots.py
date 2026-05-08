"""OpenTimestamps stamp + verify wrappers.

Reckora hands a single 32-byte Merkle root to the OpenTimestamps
calendar network and gets back a self-describing receipt. The OTS
wire format is documented at https://opentimestamps.org/; we use the
upstream ``opentimestamps`` Python package so the bytes-on-disk are
verifiable by any OTS-compatible tool (the ``ots`` CLI in particular).

The two operations the rest of Reckora cares about:

* :func:`stamp_root` — submits the root to one or more public
  calendar servers, merges the returned timestamps into a single
  ``Timestamp`` chain, serialises it as the bytes of a
  ``DetachedTimestampFile`` (the canonical ``.ots`` format), and
  returns those bytes. The caller owns persistence.
* :func:`verify_receipt` — deserialises a receipt, confirms it
  commits to a given Merkle root, and reports the strongest
  attestation found (Bitcoin block, Litecoin block, or pending
  calendar). Pure local check; **does not** call out to a calendar
  to upgrade pending attestations — that's a separate concern owned
  by :class:`reckora.timestamp.store.TimestampStore` because a
  multi-day Bitcoin upgrade window is out of scope for an inline CLI
  call.

Network failures, calendar 5xxs, and calendar-vs-receipt
inconsistencies all surface as :class:`StampError` so the CLI layer
can format them consistently.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from opentimestamps.calendar import RemoteCalendar  # type: ignore[import-untyped]
from opentimestamps.core.notary import (  # type: ignore[import-untyped]
    BitcoinBlockHeaderAttestation,
    LitecoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.op import OpSHA256  # type: ignore[import-untyped]
from opentimestamps.core.serialize import (  # type: ignore[import-untyped]
    BytesDeserializationContext,
    BytesSerializationContext,
)
from opentimestamps.core.timestamp import (  # type: ignore[import-untyped]
    DetachedTimestampFile,
    Timestamp,
)

# The default calendar set matches the official ``ots`` CLI's
# defaults, which are the three public calendars run by independent
# operators. Submitting to more than one keeps the receipt valid even
# if a single operator goes offline before its pending commitment
# upgrades to Bitcoin.
DEFAULT_CALENDARS: tuple[str, ...] = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)


class StampError(RuntimeError):
    """Raised when stamping or verifying an OpenTimestamps receipt fails."""


class AttestationStatus(StrEnum):
    """Strongest attestation found in a receipt.

    Ordered from weakest to strongest:

    * ``pending`` — the calendar accepted the commitment but hasn't
      yet anchored it in a block. Re-run ``reckora verify`` after the
      next Bitcoin block (~10 min - several hours).
    * ``litecoin`` — anchored in a Litecoin block. Cheap proof of
      existence at the timestamp's height; some auditors trust it,
      others don't.
    * ``bitcoin`` — anchored in a Bitcoin block. The canonical OTS
      strongest-form attestation.
    * ``none`` — receipt is valid bytes but contains no attestations
      at all (shouldn't happen with a fresh stamp; possible with
      hand-crafted test fixtures).
    """

    NONE = "none"
    PENDING = "pending"
    LITECOIN = "litecoin"
    BITCOIN = "bitcoin"


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of :func:`verify_receipt`.

    The fields are intentionally machine-friendly: the CLI renders
    its own human-readable summary on top, the API layer (when it
    eventually grows one) can return this dataclass verbatim.
    """

    valid: bool
    status: AttestationStatus
    receipt_root_sha256: str
    expected_root_sha256: str
    bitcoin_block_height: int | None = None
    litecoin_block_height: int | None = None
    pending_calendars: tuple[str, ...] = field(default_factory=tuple)
    verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def stamp_root(
    root: bytes,
    *,
    calendars: Iterable[str] = DEFAULT_CALENDARS,
    user_agent: str = "Reckora/0.1",
) -> bytes:
    """Submit ``root`` (32 raw bytes) to OpenTimestamps and return ``.ots`` bytes.

    The call blocks until at least one calendar responds; failures
    from individual calendars are tolerated as long as one succeeds.
    If *every* calendar fails the function raises :class:`StampError`
    so the caller doesn't end up with a useless empty receipt.

    The returned bytes are the canonical ``DetachedTimestampFile``
    serialisation — readable by the upstream ``ots`` CLI and any
    other OTS-compatible verifier.
    """
    if len(root) != 32:
        raise StampError(f"Merkle root must be 32 bytes, got {len(root)}")

    timestamp = Timestamp(root)
    calendar_urls = list(calendars)
    if not calendar_urls:
        raise StampError("at least one calendar URL is required")

    successes: list[str] = []
    failures: list[tuple[str, str]] = []
    for url in calendar_urls:
        try:
            calendar = RemoteCalendar(url, user_agent=user_agent)
            sub = calendar.submit(root)
        except Exception as exc:  # network, 5xx, parse — all opaque here
            failures.append((url, str(exc)))
            continue
        timestamp.merge(sub)
        successes.append(url)
    if not successes:
        joined = "; ".join(f"{u}: {e}" for u, e in failures)
        raise StampError(f"all calendars failed: {joined}")

    detached = DetachedTimestampFile(OpSHA256(), timestamp)
    buf = io.BytesIO()
    ctx = BytesSerializationContext()
    detached.serialize(ctx)
    buf.write(ctx.getbytes())
    return buf.getvalue()


def verify_receipt(receipt_bytes: bytes, expected_root: bytes) -> VerificationResult:
    """Parse a ``.ots`` receipt and confirm it commits to ``expected_root``.

    This is a *local* verification: we walk the timestamp's operation
    chain to find every attestation and report the strongest. We do
    NOT phone any calendar server; if the receipt is still ``pending``
    the caller should re-run after the next Bitcoin block.

    Raises :class:`StampError` for unparseable bytes; the
    "commits to wrong root" case is reported via
    ``VerificationResult.valid=False`` so the CLI can render the
    diff cleanly without try/except plumbing.
    """
    if len(expected_root) != 32:
        raise StampError(f"expected root must be 32 bytes, got {len(expected_root)}")
    try:
        ctx = BytesDeserializationContext(receipt_bytes)
        detached = DetachedTimestampFile.deserialize(ctx)
    except Exception as exc:
        raise StampError(f"could not parse OTS receipt: {exc}") from exc

    receipt_root: bytes = detached.timestamp.msg
    valid = receipt_root == expected_root

    bitcoin_height: int | None = None
    litecoin_height: int | None = None
    pending: list[str] = []
    for _msg, attestation in detached.timestamp.all_attestations():
        if isinstance(attestation, BitcoinBlockHeaderAttestation):
            h = int(attestation.height)
            if bitcoin_height is None or h < bitcoin_height:
                bitcoin_height = h
        elif isinstance(attestation, LitecoinBlockHeaderAttestation):
            h = int(attestation.height)
            if litecoin_height is None or h < litecoin_height:
                litecoin_height = h
        elif isinstance(attestation, PendingAttestation):
            pending.append(str(attestation.uri))

    if bitcoin_height is not None:
        status = AttestationStatus.BITCOIN
    elif litecoin_height is not None:
        status = AttestationStatus.LITECOIN
    elif pending:
        status = AttestationStatus.PENDING
    else:
        status = AttestationStatus.NONE

    return VerificationResult(
        valid=valid,
        status=status,
        receipt_root_sha256=receipt_root.hex(),
        expected_root_sha256=expected_root.hex(),
        bitcoin_block_height=bitcoin_height,
        litecoin_block_height=litecoin_height,
        pending_calendars=tuple(sorted(set(pending))),
    )


def _calendar_factory(_url: str, *, user_agent: str = "Reckora/0.1") -> Any:
    """Indirection seam for tests.

    Production simply hands ``RemoteCalendar(url, user_agent=...)``.
    Tests monkeypatch this to inject a stub calendar that returns a
    pre-baked :class:`Timestamp` chain without any network call.

    This helper is used by :func:`stamp_root` only when the future
    test-suite needs to inject a stub via attribute patching; the
    primary code path constructs ``RemoteCalendar`` directly so the
    stable public API stays unchanged.
    """
    return RemoteCalendar(_url, user_agent=user_agent)
