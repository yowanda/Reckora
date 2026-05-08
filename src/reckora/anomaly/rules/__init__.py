"""Anomaly rules. Each rule is a callable ``(traces, *, now) -> list[Anomaly]``."""

from __future__ import annotations

from . import domain_expiry, name_divergence, phone_validity, temporal

__all__ = ["domain_expiry", "name_divergence", "phone_validity", "temporal"]
