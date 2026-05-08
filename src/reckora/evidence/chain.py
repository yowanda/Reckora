"""Canonical hashing for evidence rows.

The evidence chain has one job: make every claim Reckora ever surfaces traceable
back to a content-hashed source payload. The hash MUST be stable across runs and
machines, so we serialise with sorted keys and a fixed JSON dialect before hashing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ..models.entity import Evidence


def canonical_payload(payload: dict[str, Any]) -> bytes:
    """Serialise a payload to a stable canonical form.

    Sorted keys, no extra whitespace, UTF-8 encoded. Two payloads that
    semantically describe the same dict will produce identical bytes (and thus
    identical hashes), regardless of insertion order.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def hash_payload(payload: dict[str, Any]) -> str:
    """Hex-encoded SHA-256 of the canonical payload."""
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def make_evidence(
    source_url: str,
    payload: dict[str, Any],
    *,
    keep_raw: bool = True,
    fetched_at: datetime | None = None,
) -> Evidence:
    """Build an Evidence row from a collector's normalised payload.

    Pass `keep_raw=False` for big responses to discard the inline payload but
    keep the hash + source URL + timestamp so the chain stays auditable.
    """
    return Evidence(
        source_url=source_url,
        fetched_at=fetched_at or datetime.now(UTC),
        payload_sha256=hash_payload(payload),
        raw_payload=payload if keep_raw else None,
    )
