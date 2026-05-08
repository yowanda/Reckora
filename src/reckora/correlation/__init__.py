"""Correlation engine — turns Traces into confidence-scored Edges."""

from __future__ import annotations

from .confidence import ConfidenceContribution, combine
from .embeddings import BioEmbedder, SentenceTransformerEmbedder, cosine_similarity
from .engine import correlate

__all__ = [
    "BioEmbedder",
    "ConfidenceContribution",
    "SentenceTransformerEmbedder",
    "combine",
    "correlate",
    "cosine_similarity",
]
