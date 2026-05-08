"""AI reasoning layer — operates over verified evidence, never raw scraping."""

from __future__ import annotations

from .client import ReasoningClient
from .hypothesize import hypothesize
from .summarize import summarize

__all__ = ["ReasoningClient", "hypothesize", "summarize"]
