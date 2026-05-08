"""Avatar collector — fetches an image URL and emits a perceptual-hash trace.

The correlation engine already ships with an ``avatar_phash`` rule that
fires when two Traces both expose a ``avatar_phash`` field whose values
are within a small Hamming distance — but until now nothing wrote that
field. The :class:`AvatarCollector` closes the loop: given an
``IdentifierType.AVATAR`` whose value is a fully-qualified image URL, it
fetches the bytes, computes a dHash (the family the correlation rule
expects), and emits a single normalised :class:`Trace` containing:

- ``url`` — verbatim avatar URL (host + path)
- ``content_type`` — server-reported MIME, lower-cased and stripped
- ``bytes_size`` — byte length of the response body
- ``bytes_sha256`` — content-hash of the raw bytes (not the canonical
  payload — distinct from the evidence chain's payload SHA so dossier
  renderers can dedupe identical avatars across subjects)
- ``width`` / ``height`` / ``mode`` / ``format`` — image metadata read
  via Pillow (``RGB``, ``RGBA``, ``L``, …; ``PNG``, ``JPEG``, …)
- ``avatar_phash`` — 64-bit dHash hex string, the field the existing
  ``avatar_phash`` correlation rule reads
- ``avatar_phash_perceptual`` — perceptual-hash hex (dct-based, more
  robust to recolouring / global brightness shifts; used by future
  rules and surfaced in the dossier today)
- ``avatar_ahash`` — average-hash hex (cheapest variant; useful for a
  quick sanity check that the three hashes don't all collide)
- ``is_active`` — always ``True`` for a successfully fetched avatar

The collector silently no-ops on non-image content-types and on bodies
larger than a configurable cap so a malicious server can't OOM the
host. 4xx responses are treated as ``[]`` (an analyst's typo on a URL
shouldn't abort the investigation); 5xx is re-raised for the
orchestrator's per-collector logger.

The raw image bytes are never stored on the evidence row
(``keep_raw=False``) — only the SHA-256 of the canonicalised normalised
payload is preserved, so the chain stays auditable without bloating
the saved dossier with binary blobs.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any, ClassVar

import httpx
import imagehash
from PIL import Image, UnidentifiedImageError

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

# 5 MiB ceiling — well above the largest avatar a major social platform
# ships (GitHub caps user avatars at ~1 MiB), comfortably below anything
# that would blow out memory when decoded by Pillow.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class AvatarCollector(Collector):
    """Fetch an avatar URL, decode it, and emit a perceptual-hash trace.

    Parameters
    ----------
    client:
        Optional pre-configured ``httpx.AsyncClient`` (used by the
        orchestrator / tests to share a single client and inject mocks).
    user_agent:
        Sent on every request. Defaults to ``"Reckora/0.1"``.
    max_bytes:
        Hard cap on response body size; bodies larger than this are
        treated as no-ops to keep memory bounded.
    hash_size:
        Edge length of the dHash / pHash / aHash grid. Defaults to 8,
        producing 64-bit (16-hex-char) hashes — same default as the
        existing ``avatar_phash`` correlation rule.
    """

    name: ClassVar[str] = "avatar_http"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.AVATAR.value})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "Reckora/0.1",
        max_bytes: int = DEFAULT_MAX_BYTES,
        hash_size: int = 8,
    ) -> None:
        super().__init__(client)
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._hash_size = hash_size

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            # We accept any image MIME — server-side negotiation isn't
            # worth the complexity. The validator below enforces an
            # ``image/`` prefix on the response Content-Type instead.
            "Accept": "image/*",
        }

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        url = identifier.value.strip()
        if not _is_http_url(url):
            # Avatar identifiers passed via `data:` URIs, raw paths, or
            # unsupported schemes silently no-op so the orchestrator
            # never has to swallow a scheme error.
            return []

        client = await self._http()
        resp = await client.get(url, headers=self._headers())
        if 400 <= resp.status_code < 500:
            # 4xx covers user error (typo'd URL, hot-link block, deleted
            # avatar, auth-walled image). The absence of an avatar is
            # itself an intelligence finding but not a failure.
            return []
        resp.raise_for_status()

        body = resp.content
        if len(body) > self._max_bytes:
            return []

        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            # The server returned non-image bytes (HTML 200 from a
            # paywall, redirect to a login page, etc.). No-op.
            return []

        try:
            image = Image.open(io.BytesIO(body))
            image.load()
        except (UnidentifiedImageError, OSError):
            # Pillow couldn't decode the bytes — treat as no-op rather
            # than crash. The bytes_sha256 is still useful for dedupe
            # but without dimensions / hashes the trace is too thin to
            # justify emitting.
            return []

        fields = self._normalise(
            url=url,
            body=body,
            content_type=content_type,
            image=image,
        )
        evidence = make_evidence(url, fields, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.AVATAR_HTTP,
                fields=fields,
                evidence=evidence,
            ),
        ]

    def _normalise(
        self,
        *,
        url: str,
        body: bytes,
        content_type: str,
        image: Image.Image,
    ) -> dict[str, Any]:
        bytes_sha256 = hashlib.sha256(body).hexdigest()
        # Pillow loads in the image's native mode; convert to RGB for the
        # hashes so palette-mode (P) GIFs / PNGs hash to the same bits as
        # their decoded RGB equivalents and the dHash field stays stable.
        for_hashing = image if image.mode in {"RGB", "L"} else image.convert("RGB")
        dhash_hex = str(imagehash.dhash(for_hashing, hash_size=self._hash_size))
        phash_hex = str(imagehash.phash(for_hashing, hash_size=self._hash_size))
        ahash_hex = str(imagehash.average_hash(for_hashing, hash_size=self._hash_size))
        return {
            "url": url,
            "content_type": content_type,
            "bytes_size": len(body),
            "bytes_sha256": bytes_sha256,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            # Pillow exposes the source format as ``image.format`` (e.g.
            # ``"PNG"``); preserve the original string for the dossier
            # without lower-casing so the rendered field matches what an
            # analyst would see in any image viewer.
            "format": image.format,
            "avatar_phash": dhash_hex,
            "avatar_phash_perceptual": phash_hex,
            "avatar_ahash": ahash_hex,
            "is_active": True,
        }


def _is_http_url(value: str) -> bool:
    """Return True iff ``value`` is an http(s):// URL with a host segment.

    Cheap shape check, not a fully-spec-compliant validator — we only
    need enough signal to skip non-http identifiers before hitting the
    network. ``urlparse`` is intentionally not used so an unparseable
    string degrades to ``False`` instead of an exception.
    """
    if not value:
        return False
    lower = value.lower()
    for scheme in _ALLOWED_SCHEMES:
        prefix = f"{scheme}://"
        if lower.startswith(prefix):
            rest = value[len(prefix) :]
            host = rest.split("/", 1)[0]
            return bool(host) and "." in host
    return False
