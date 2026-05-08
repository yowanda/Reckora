"""OpenTimestamps Calendar HTTP client.

The OpenTimestamps protocol lets anyone anchor a SHA-256 digest into the
Bitcoin blockchain *for free* by submitting it to a public *calendar
server*. The calendar aggregates many submissions into a single Merkle tree
and writes the aggregate root into a Bitcoin transaction at the next mining
opportunity. The *receipt* the calendar returns is a binary commitment file
that — once Bitcoin includes the calendar's transaction — can be upgraded to
a full proof linking the original digest to a block hash.

This module implements only the *submission* half of the protocol because
that is what Reckora needs at investigation time: post a 32-byte digest to
``POST /digest`` and persist the binary receipt alongside the dossier. The
follow-up *upgrade* (refreshing the receipt once Bitcoin confirms it) is
intentionally left to the user via the standalone ``ots`` CLI from
`opentimestamps-client`_, since waiting on Bitcoin confirmation is hours-to-
days work and has no place in a synchronous investigation request.

.. _opentimestamps-client: https://github.com/opentimestamps/opentimestamps-client

The calendar URLs in :data:`DEFAULT_CALENDARS` are the ones the official
``ots`` CLI defaults to. Submitting to several is *intentional*: if any one
calendar disappears the dossier still has a verifiable receipt from the
others, since the digest committed to is identical across submissions.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)


DEFAULT_CALENDARS: tuple[str, ...] = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://alice.btc.calendar.opentimestamps.org",
)
"""Default public OpenTimestamps calendars.

Mirrors the upstream ``ots`` CLI defaults so a Reckora-anchored dossier
verifies with the standard tooling without extra configuration.
"""


_DEFAULT_TIMEOUT_SECONDS = 30.0
_DIGEST_BYTES = 32


class CalendarReceipt(BaseModel):
    """Binary commitment a single calendar returned for one digest.

    The receipt itself is the Calendar Server response body — an
    OpenTimestamps-format binary blob that the ``ots`` CLI knows how to
    upgrade and verify. We store it base64-encoded so the JSON dossier and
    SQLite columns stay text-only.
    """

    model_config = ConfigDict(frozen=True)

    calendar_url: str
    receipt_b64: str
    submitted_at: datetime


class OpenTimestampsClient:
    """Async HTTP wrapper around the OpenTimestamps Calendar protocol.

    The client is intentionally minimal — it talks to the public ``/digest``
    endpoint, parses no proof structure, and treats every HTTP-level failure
    as best-effort (a calendar being momentarily unreachable should not
    cancel the whole anchor). Pass an ``httpx.AsyncClient`` in to share a
    connection pool with the rest of the engine, or let the constructor
    spin up its own.
    """

    def __init__(
        self,
        *,
        calendars: Sequence[str] = DEFAULT_CALENDARS,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not calendars:
            raise ValueError("OpenTimestampsClient requires at least one calendar URL")
        self._calendars: tuple[str, ...] = tuple(calendars)
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def calendars(self) -> tuple[str, ...]:
        return self._calendars

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def submit_digest(self, digest_hex: str) -> list[CalendarReceipt]:
        """Submit one 32-byte digest to every configured calendar.

        Returns one :class:`CalendarReceipt` per calendar that responded
        with a non-empty 200. Calendars that error out are logged and
        skipped so a single down endpoint never breaks anchoring; the
        caller can still surface "anchored to N of M calendars" if it
        cares about quorum.
        """
        digest = _decode_digest(digest_hex)
        receipts: list[CalendarReceipt] = []
        for calendar_url in self._calendars:
            receipt = await self._submit_one(calendar_url, digest)
            if receipt is not None:
                receipts.append(receipt)
        return receipts

    async def _submit_one(self, calendar_url: str, digest: bytes) -> CalendarReceipt | None:
        url = f"{calendar_url.rstrip('/')}/digest"
        try:
            response = await self._client.post(
                url,
                content=digest,
                headers={
                    "Accept": "application/vnd.opentimestamps.v1",
                    "Content-Type": "application/vnd.opentimestamps.v1",
                    "User-Agent": "Reckora/0.1 (+https://github.com/yowanda/Reckora)",
                },
            )
        except httpx.HTTPError:
            log.exception("opentimestamps submission to %s raised", calendar_url)
            return None
        if response.status_code != 200 or not response.content:
            log.warning(
                "opentimestamps calendar %s returned status=%s body_len=%s",
                calendar_url,
                response.status_code,
                len(response.content),
            )
            return None
        return CalendarReceipt(
            calendar_url=calendar_url,
            receipt_b64=base64.b64encode(response.content).decode("ascii"),
            submitted_at=datetime.now(UTC),
        )


def _decode_digest(digest_hex: str) -> bytes:
    """Validate and decode a hex SHA-256 digest into 32 raw bytes."""
    if len(digest_hex) != _DIGEST_BYTES * 2:
        raise ValueError(
            f"opentimestamps digest must be {_DIGEST_BYTES * 2} hex chars, "
            f"got {len(digest_hex)}: {digest_hex!r}"
        )
    try:
        return bytes.fromhex(digest_hex)
    except ValueError as exc:
        raise ValueError(f"opentimestamps digest is not valid hex: {digest_hex!r}") from exc
