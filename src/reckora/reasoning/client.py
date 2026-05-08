"""Thin async wrapper around the OpenAI chat-completion API.

The wrapper exists so the rest of the reasoning layer depends on a single
interface (`ReasoningClient.complete`) and so callers do not need to know
about model names, temperature, or auth plumbing. Phase 3 will swap the
backend for additional providers behind the same interface.
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI


class ReasoningClient:
    """Async chat-completion client. Lazy-initialises its OpenAI client."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        *,
        temperature: float = 0.2,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._model = model
        self._temperature = temperature
        self._client: AsyncOpenAI | None = None

    @property
    def model(self) -> str:
        return self._model

    def _client_or_raise(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured")
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def complete(self, system: str, user: str) -> str:
        """Run a single chat completion. Returns the assistant message text."""
        client = self._client_or_raise()
        resp = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self._temperature,
        )
        return resp.choices[0].message.content or ""
