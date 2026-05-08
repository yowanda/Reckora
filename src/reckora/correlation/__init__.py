"""Correlation engine — turns Traces into confidence-scored Edges."""

from __future__ import annotations

from .confidence import ConfidenceContribution, combine
from .engine import correlate

__all__ = ["ConfidenceContribution", "combine", "correlate"]
