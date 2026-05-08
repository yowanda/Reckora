"""Anomaly detection — rule-based integrity checks across collected Traces.

The anomaly detector inspects a list of Traces and surfaces findings that
either undermine the chain (future-dated evidence, inconsistent timestamps,
expired domains) or contradict each other (display-name divergence). It
*never* invents facts and *never* asserts new identifiers — it only labels
the dossier with verifiable, evidence-cited integrity issues so the
reviewer can decide what to make of them.

This is the rule-based half of layer 5 ("AI Reasoning") of the Reckora
roadmap; the LLM-powered half lives in :mod:`reckora.reasoning`.
"""

from __future__ import annotations

from .engine import detect_anomalies
from .models import Anomaly, AnomalyKind, AnomalySeverity

__all__ = [
    "Anomaly",
    "AnomalyKind",
    "AnomalySeverity",
    "detect_anomalies",
]
