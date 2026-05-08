"""Subject ownership and sharing primitives.

This package owns the API-side authorisation tables — ``subject_owners``
(1:1 owner per subject) and ``subject_shares`` (m:n explicit shares).
The engine's :mod:`reckora.persistence` layer stays user-agnostic; everything
here is grafted on at the API boundary so the CLI can keep persisting
dossiers without knowing about RBAC.
"""

from reckora_api.access.repository import AccessRepository

__all__ = ["AccessRepository"]
