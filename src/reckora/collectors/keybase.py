"""Keybase collector.

Resolves a ``username`` Identifier against the public Keybase user-lookup
endpoint at ``https://keybase.io/_/api/1.0/user/lookup.json?usernames={id}``
— no key, no registration, the same anonymous read-only endpoint the
public ``keybase`` CLI hits for ``keybase id <user>``.

Keybase is a useful collector for OSINT correlation because the platform
is itself an *aggregator* of identity proofs: a single Keybase profile
typically links a Twitter handle, GitHub account, Reddit account, one or
more DNS-based domain proofs and a PGP public key, all signed by the
same device key. Surfacing those linked accounts as a structured
``proofs`` array means downstream correlation rules can pivot from one
identifier to another without ever having to scrape a profile page.

The collector keeps the canonical SHA-256 of the raw envelope as the
audit anchor and drops the inline body (``keep_raw=False``) because
Keybase user payloads can carry many KB of public-key bundles and
device chains we do not need downstream.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

KEYBASE_API_BASE = "https://keybase.io/_/api/1.0"
KEYBASE_PROFILE_BASE = "https://keybase.io"

# Keybase usernames are 2-16 characters: ASCII letters, digits, and
# underscores. The endpoint will reject anything else with an
# ``INPUT_ERROR`` status; pre-filtering saves a round trip on obvious
# misses (Bitcoin addresses, emails, hex hashes, …) and also keeps the
# orchestrator from leaking malformed identifiers into Keybase's logs.
# Keybase normalises every username to lowercase server-side, so we
# accept any casing here and lowercase before we hit the API.
KEYBASE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")

# Keybase encodes proof state as an int. ``1`` is the only value that
# means "this proof is currently live and verified"; everything else
# (``0`` = pending, ``2`` = revoked, ``3`` = permanently failed, …) is
# either noise or stale data we should not surface as a fresh signal.
_PROOF_STATE_OK = 1


class KeybaseCollector(Collector):
    """Collect a public Keybase user profile and its identity proofs."""

    name: ClassVar[str] = "keybase"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Reckora/0.1",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        username = identifier.value
        if not KEYBASE_USERNAME_RE.match(username):
            return []
        # Keybase canonicalises to lowercase server-side, but the
        # ``usernames=`` query parameter is *case-sensitive* in the
        # validation step: an uppercase character there returns
        # ``INPUT_ERROR`` (status code 100) instead of just looking the
        # account up. Lowercase before the round-trip so casing drift in
        # upstream callers doesn't turn into a silent miss.
        lookup = username.lower()
        client = await self._http()
        url = f"{KEYBASE_API_BASE}/user/lookup.json?usernames={lookup}"
        resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return []
        # Keybase reports lookup outcomes through ``status.code`` rather
        # than HTTP status codes: ``0`` = OK, ``100`` = INPUT_ERROR
        # (e.g. malformed username), other codes signal transport or
        # auth issues. Treat anything other than OK as a miss so the
        # orchestrator's miss-vs-error semantics stay consistent with
        # the GitHub and Hacker News collectors.
        status = data.get("status") or {}
        if not isinstance(status, dict) or status.get("code") != 0:
            return []
        them = data.get("them")
        if not isinstance(them, list) or not them:
            return []
        user = them[0]
        # ``them[0] is None`` is Keybase's idiom for "this account does
        # not exist" — the bulk-lookup endpoint always returns a
        # same-length array even when entries are unknown.
        if not isinstance(user, dict):
            return []
        fields = self._normalise(user, username=username)
        evidence = make_evidence(url, data, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.KEYBASE_API,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(user: dict[str, Any], *, username: str) -> dict[str, Any]:
        basics = _as_dict(user.get("basics"))
        profile = _as_dict(user.get("profile"))
        public_keys = _as_dict(user.get("public_keys"))
        primary_key = _as_dict(public_keys.get("primary"))
        # Match the GitHub / Hacker News collectors' ``id`` semantics:
        # prefer the server-canonical username (always lowercased on
        # Keybase) and fall back to the requested value so the trace
        # always carries a username field.
        canonical_raw = basics.get("username")
        canonical_id = canonical_raw if isinstance(canonical_raw, str) else username
        ctime_raw = basics.get("ctime")
        created_iso = (
            datetime.fromtimestamp(int(ctime_raw), tz=UTC).isoformat()
            if isinstance(ctime_raw, int | float)
            else None
        )
        proofs = _extract_proofs(user.get("proofs_summary"))
        fingerprint_raw = primary_key.get("key_fingerprint")
        pgp_fingerprint = fingerprint_raw if isinstance(fingerprint_raw, str) else None
        has_pgp_key = bool(pgp_fingerprint)
        bio_raw = profile.get("bio")
        bio = bio_raw if isinstance(bio_raw, str) else None
        display_raw = profile.get("full_name")
        display_name = display_raw if isinstance(display_raw, str) else None
        location_raw = profile.get("location")
        location = location_raw if isinstance(location_raw, str) else None
        # An account is "active" if Keybase sees any verified identity
        # signal: at least one live proof, a published PGP key, or a
        # filled-out profile bio / display name. Empty accounts
        # (registered but never linked anything) still surface as a
        # trace with ``is_active=False`` so the absence of activity is
        # itself an intelligence finding rather than a collection miss.
        is_active = bool(proofs or has_pgp_key or bio or display_name)
        return {
            "platform": "keybase",
            "username": canonical_id,
            "profile_url": f"{KEYBASE_PROFILE_BASE}/{canonical_id}",
            "display_name": display_name,
            "bio": bio,
            "location": location,
            "created_at": created_iso,
            "proofs": proofs,
            "has_pgp_key": has_pgp_key,
            "pgp_fingerprint": pgp_fingerprint,
            "is_active": is_active,
        }


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` if it is a dict, otherwise an empty dict.

    Keybase's user payload nests freely-typed sub-dicts (``basics``,
    ``profile``, ``public_keys`` ...) and any of them can legitimately be
    missing or returned as ``None`` for sparsely-populated accounts. Funnel
    every nested access through this helper so the rest of the normaliser
    can rely on always getting a real ``dict`` back without sprinkling
    ``isinstance`` guards on every line (which mypy can't track across
    sequential ``.get`` calls anyway).
    """
    return value if isinstance(value, dict) else {}


def _extract_proofs(proofs_summary: Any) -> list[dict[str, str]]:
    """Distil Keybase's ``proofs_summary`` blob into a flat list.

    Each returned entry is ``{"platform": str, "identity": str, "url": str}``.
    Only currently-live proofs (``state == 1``) are surfaced — pending,
    revoked or permanently failed proofs are either noise (the user
    hasn't finished posting them yet) or stale (they explicitly belong
    to someone else) and would mislead correlation downstream.
    """
    if not isinstance(proofs_summary, dict):
        return []
    raw = proofs_summary.get("all")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("state") != _PROOF_STATE_OK:
            continue
        proof_type = entry.get("proof_type")
        nametag = entry.get("nametag")
        url = entry.get("service_url") or entry.get("proof_url")
        if not isinstance(proof_type, str) or not isinstance(nametag, str):
            continue
        out.append(
            {
                "platform": proof_type,
                "identity": nametag,
                "url": url if isinstance(url, str) else "",
            }
        )
    return out
