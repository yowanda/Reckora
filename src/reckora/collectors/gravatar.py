"""Gravatar collector.

Resolves an ``email`` Identifier against the public Gravatar profile
JSON endpoint at ``https://www.gravatar.com/{md5_hash}.json`` — no key,
no registration. Gravatar derives the hash from the *lowercased,
trimmed* email address and exposes whatever public profile fields the
account holder filled in (display name, ``preferredUsername``, location,
profile photo URL, plus an ``accounts[]`` array of verified linked
accounts on Twitter, GitHub, LinkedIn, …).

Gravatar is high-signal for OSINT correlation because the identifier is
not the email itself but its MD5 hash: the email never leaves the
collector, and yet a positive match yields a chain of cross-platform
identity claims that the entity-resolution layer can fan out from. The
collector also surfaces the canonical ``profile_photo_url`` so the
downstream avatar-perceptual-hash collector can pick it up via a fresh
URL identifier without needing a separate scrape pass.

The collector keeps the canonical SHA-256 of the raw envelope as the
audit anchor and drops the inline body (``keep_raw=False``) because
profile payloads can include long bios, multiple linked accounts, and
embedded URLs we don't need to re-store inline.
"""

from __future__ import annotations

import hashlib
from typing import Any, ClassVar

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

GRAVATAR_API_BASE = "https://www.gravatar.com"


def _hash_email(email: str) -> str:
    """Return the Gravatar MD5 hash for ``email``.

    Per `Gravatar's hashing rules <https://docs.gravatar.com/general/hash/>`_:
    1. Trim leading/trailing whitespace
    2. Force lowercase
    3. MD5 hash, hex-encoded

    Pure function — no I/O — so it's trivial to unit-test. We accept
    callers that pass an already-trimmed value too because trimming
    twice is a no-op.
    """
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


class GravatarCollector(Collector):
    """Collect a public Gravatar profile and its linked accounts."""

    name: ClassVar[str] = "gravatar"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.EMAIL.value})

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Reckora/0.1",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        email = identifier.value.strip()
        if not email or "@" not in email:
            return []
        email_hash = _hash_email(email)
        client = await self._http()
        url = f"{GRAVATAR_API_BASE}/{email_hash}.json"
        resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        # Gravatar returns ``"User not found"`` (a literal JSON string,
        # HTTP 200) for hashes that aren't registered, in addition to
        # the more conventional 404. Treat both as a miss so the
        # orchestrator's miss-vs-error semantics stay consistent with
        # the GitHub / Hacker News / Keybase collectors.
        try:
            data = resp.json()
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        entries = data.get("entry")
        if not isinstance(entries, list) or not entries:
            return []
        entry = entries[0]
        if not isinstance(entry, dict):
            return []
        fields = self._normalise(entry, email=email, email_hash=email_hash)
        evidence = make_evidence(url, data, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.GRAVATAR_API,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(
        entry: dict[str, Any],
        *,
        email: str,
        email_hash: str,
    ) -> dict[str, Any]:
        # Gravatar returns ``profileUrl`` already fully-qualified in the
        # production response, but legacy accounts sometimes expose only
        # the relative form ``/{hash}``. Construct a defensive fallback
        # so downstream consumers always get a usable URL.
        profile_url_raw = entry.get("profileUrl")
        profile_url = (
            profile_url_raw
            if isinstance(profile_url_raw, str) and profile_url_raw.startswith("http")
            else f"{GRAVATAR_API_BASE}/{email_hash}"
        )
        display_raw = entry.get("displayName")
        display_name = display_raw if isinstance(display_raw, str) else None
        username_raw = entry.get("preferredUsername")
        preferred_username = username_raw if isinstance(username_raw, str) else None
        location_raw = entry.get("currentLocation")
        location = location_raw if isinstance(location_raw, str) else None
        bio_raw = entry.get("aboutMe")
        bio = bio_raw if isinstance(bio_raw, str) else None
        photo_url = _extract_photo_url(entry)
        accounts = _extract_accounts(entry.get("accounts"))
        # An account is "active" if Gravatar exposes any concrete
        # public-facing claim: a display name, a preferred username, a
        # bio, or at least one verified linked account. Accounts with
        # only an avatar but no profile data still surface as a trace
        # so the absence of metadata is itself a finding.
        is_active = bool(display_name or preferred_username or bio or accounts)
        return {
            "platform": "gravatar",
            "email_hash": email_hash,
            "profile_url": profile_url,
            "preferred_username": preferred_username,
            "display_name": display_name,
            "bio": bio,
            "location": location,
            "profile_photo_url": photo_url,
            "accounts": accounts,
            "is_active": is_active,
        }


def _extract_photo_url(entry: dict[str, Any]) -> str | None:
    """Pick the most stable profile-photo URL Gravatar exposes.

    Gravatar offers up to three locations for the photo: a direct
    ``thumbnailUrl``, a ``photos[]`` array (mostly historical), and an
    ``avatar_url`` field on newer accounts. Prefer the explicit
    ``thumbnailUrl`` because that's the one Gravatar serves on profile
    pages today.
    """
    thumb = entry.get("thumbnailUrl")
    if isinstance(thumb, str) and thumb:
        return thumb
    photos = entry.get("photos")
    if isinstance(photos, list):
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            value = photo.get("value")
            if isinstance(value, str) and value:
                return value
    avatar = entry.get("avatar_url")
    return avatar if isinstance(avatar, str) and avatar else None


def _extract_accounts(raw: Any) -> list[dict[str, str]]:
    """Distil Gravatar's ``accounts`` blob into a flat list.

    Each returned entry is ``{"platform": str, "username": str, "url": str}``.
    Gravatar exposes the platform name in the ``shortname`` field
    (``twitter`` / ``github`` / ``linkedin`` / …), the handle in
    ``username``, and the canonical profile URL in ``url``. We drop
    entries missing any of those three because partial linkage data
    would mislead downstream correlation.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        shortname = entry.get("shortname")
        username = entry.get("username")
        url = entry.get("url")
        if not isinstance(shortname, str) or not shortname:
            continue
        if not isinstance(username, str) or not username:
            continue
        if not isinstance(url, str) or not url:
            continue
        out.append(
            {
                "platform": shortname,
                "username": username,
                "url": url,
            }
        )
    return out
