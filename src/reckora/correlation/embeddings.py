"""Bio-embedding adapters for the correlation layer.

The default :func:`reckora.correlation.rules.bio_similarity.score` runs on a
lexical token-cosine similarity — fast, dependency-free, and hermetic. That
catches "Security researcher" ↔ "security and incident response" but trips on
synonyms ("infosec engineer" ↔ "security researcher" share zero tokens
despite describing the same role).

This module introduces an optional embedding-based path: a ``BioEmbedder``
abstraction plus a concrete ``SentenceTransformerEmbedder`` backed by the
``sentence-transformers`` library (off the default install path; pull it in
with ``uv sync --extra embeddings``). Callers wire an embedder through
:func:`reckora.correlation.engine.correlate` and the bio-similarity rule
swaps token cosine for embedding cosine — keeping the same edge kind
(``EdgeKind.SIMILAR_BIO``) and the same probabilistic-OR fusion downstream
so the change is opaque to the rest of the engine.

The ``sentence-transformers`` import is deferred to first use so that
``import reckora.correlation`` stays cheap on hosts that did not install
the optional extra.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


@runtime_checkable
class BioEmbedder(Protocol):
    """Anything that can map a bio string to a dense vector.

    Implementations MUST be deterministic for a fixed input — the
    correlation engine treats the embeddings as content-addressable so
    the same bios yield the same edge confidence across runs.

    Returning ``[]`` is allowed and signals "no embedding" — the rule
    falls back to its lexical baseline rather than crashing.
    """

    def embed_one(self, text: str) -> list[float]:
        """Return the embedding vector for a single string."""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length dense vectors.

    Range is ``[-1.0, 1.0]`` for arbitrary vectors but ``[0.0, 1.0]`` for
    sentence-transformers models that emit non-negative magnitudes (the
    default). Returns ``0.0`` for either input being empty / zero-norm so
    a misbehaving embedder cannot produce ``NaN`` confidence downstream.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=True))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


class SentenceTransformerEmbedder:
    """``BioEmbedder`` backed by a ``sentence-transformers`` model.

    The model is loaded lazily on first use so that constructing the
    embedder is cheap (the orchestrator can build one even when the
    correlation engine is never reached, e.g. for diagnostic CLI
    invocations). Subsequent calls reuse the loaded weights.

    The default model (``sentence-transformers/all-MiniLM-L6-v2``) is the
    same 22 MB checkpoint the upstream library uses in its quickstart —
    small enough to download on a laptop, mainstream enough that any
    deployment that already pulls ``sentence-transformers`` for an
    adjacent feature gets it cached for free.
    """

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    @property
    def model_name(self) -> str:
        """Name of the underlying sentence-transformers checkpoint."""
        return self._model_name

    def embed_one(self, text: str) -> list[float]:
        """Encode ``text`` to a dense vector. Empty text returns ``[]``."""
        if not text:
            return []
        model = self._load_model()
        # ``encode`` accepts a list and returns one vector per input; we
        # take the singleton path so callers don't have to think about
        # batching for the score-pair call site. Production-grade
        # batching belongs in a higher layer that wants to amortise the
        # forward pass across many traces.
        vector = model.encode([text], convert_to_numpy=False, show_progress_bar=False)[0]
        # ``encode(convert_to_numpy=False)`` returns a torch tensor;
        # ``.tolist()`` makes the result JSON-serialisable and severs
        # the torch dependency from the rest of the engine.
        return [float(x) for x in vector.tolist()]

    def _load_model(self) -> SentenceTransformer:
        """Lazily import and instantiate the sentence-transformers model."""
        if self._model is None:
            # Local import keeps ``sentence-transformers`` (and its
            # heavy torch dependency) out of the default Reckora import
            # path — only callers that actually opt into embeddings pay
            # the load cost.
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - exercised on hosts w/o extra
                raise RuntimeError(
                    "sentence-transformers is not installed; install the optional "
                    "[embeddings] extra (uv sync --extra embeddings) to use "
                    "SentenceTransformerEmbedder."
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model
