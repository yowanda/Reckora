"""GitHub REST API collector.

Resolves a `username` Identifier against `GET /users/{username}` and emits a
single Trace with normalised profile fields. The raw API response is dropped
from the inline evidence (we keep only the SHA-256) because GitHub user
payloads carry a lot of noise we do not need downstream.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

GITHUB_API_BASE = "https://api.github.com"


class GitHubCollector(Collector):
    """Collect a public GitHub user profile."""

    name: ClassVar[str] = "github_api"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.USERNAME.value})

    def __init__(self, client: Any = None, token: str | None = None) -> None:
        super().__init__(client)
        self._token = token

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Reckora/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        client = await self._http()
        url = f"{GITHUB_API_BASE}/users/{identifier.value}"
        resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        fields = self._normalise(data)
        evidence = make_evidence(url, data, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.GITHUB_API,
                fields=fields,
                evidence=evidence,
            ),
        ]

    @staticmethod
    def _normalise(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": "github",
            "profile_url": data.get("html_url"),
            "display_name": data.get("name"),
            "bio": data.get("bio"),
            "avatar_url": data.get("avatar_url"),
            "location": data.get("location"),
            "company": data.get("company"),
            "blog": data.get("blog"),
            "email": data.get("email"),
            "twitter_username": data.get("twitter_username"),
            "followers": data.get("followers"),
            "public_repos": data.get("public_repos"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }
