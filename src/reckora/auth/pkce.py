"""PKCE (Proof Key for Code Exchange) helpers — RFC 7636.

PKCE lets a public OAuth client (one without a confidential client
secret) prove that the same agent that *started* the authorize step
is also the one *redeeming* the authorization code, by binding the
two with a one-shot ``code_verifier`` / ``code_challenge`` pair.

The ChatGPT OAuth client (``app_EMoamEEZ73f0CkXaXp7hrann``) is a
public client and only accepts the ``S256`` challenge method, so this
module hard-codes SHA-256 + base64url-no-pad. There is no ``plain``
fallback by design.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

# RFC 7636 §4.1 mandates a 43..128-char verifier; OpenAI's authorize
# endpoint enforces this at validation time, so we reject out-of-range
# requests at the source rather than letting them fail mid-flow.
_MIN_VERIFIER_LEN = 43
_MAX_VERIFIER_LEN = 128
_DEFAULT_VERIFIER_LEN = 64


def generate_code_verifier(length: int = _DEFAULT_VERIFIER_LEN) -> str:
    """Return a cryptographically random PKCE ``code_verifier``.

    ``secrets.token_urlsafe`` already produces RFC 7636-compatible
    characters (``[A-Za-z0-9_-]``), so we just trim to the requested
    length. Anything outside the 43..128 spec window is rejected
    eagerly so callers don't get a confusing 400 from the authorize
    endpoint.
    """
    if length < _MIN_VERIFIER_LEN or length > _MAX_VERIFIER_LEN:
        raise ValueError(
            f"PKCE code_verifier length must be in [{_MIN_VERIFIER_LEN}, {_MAX_VERIFIER_LEN}],"
            f" got {length}"
        )
    # ``token_urlsafe(n)`` produces ~1.33·n characters of entropy; we
    # over-generate then truncate so the final string is exactly
    # ``length`` chars even after any padding stripping.
    raw = secrets.token_urlsafe(length + 8)
    return raw[:length]


def generate_code_challenge(code_verifier: str) -> str:
    """Return the S256 ``code_challenge`` for ``code_verifier``.

    ``challenge = base64url-no-pad(SHA-256(verifier))`` per RFC 7636
    §4.2. The verifier is ASCII per spec (URL-safe alphabet) so we
    encode as ASCII rather than UTF-8 to surface non-conforming
    callers loudly via ``UnicodeEncodeError``.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
