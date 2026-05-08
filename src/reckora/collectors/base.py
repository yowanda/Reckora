"""Collector base class.

A Collector takes an Identifier and emits zero or more Traces. The interface
is intentionally minimal so we can stack collectors of very different shapes
(REST APIs, RDAP servers, scraping HTML pages, blockchain explorers, etc.)
behind one orchestrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

from ..models.entity import Identifier, Trace


class Collector(ABC):
    """Abstract collector. Subclasses set `name` and `supported`."""

    name: ClassVar[str]
    supported: ClassVar[frozenset[str]]

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def supports(self, identifier: Identifier) -> bool:
        """Return whether this collector knows how to process this identifier."""
        return identifier.type.value in self.supported

    async def _http(self) -> httpx.AsyncClient:
        """Return an httpx client. Reuses one if injected, otherwise creates one."""
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    @abstractmethod
    async def collect(self, identifier: Identifier) -> list[Trace]:
        """Run a single collection pass for `identifier`.

        Implementations MUST return an empty list rather than raising on
        "not found"-style misses; they MAY raise on transport errors so the
        orchestrator can decide how to handle them.
        """
