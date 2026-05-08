"""Bio similarity rule.

Two backends share the same ``score()`` interface:

* **Lexical token-cosine** (default) — counts shared word tokens, computes
  cosine similarity of the resulting frequency vectors. Dependency-free,
  hermetic for tests, and correct for the trivial case ("OSINT researcher"
  vs "OSINT researcher and incident response").
* **Dense embedding cosine** (opt-in via the ``[embeddings]`` extra) — runs
  the bios through a ``BioEmbedder`` (typically
  :class:`reckora.correlation.embeddings.SentenceTransformerEmbedder`) and
  takes cosine similarity of the resulting vectors. Catches semantic
  matches the lexical baseline misses ("infosec engineer" ↔ "security
  researcher" share no tokens but describe the same role).

Callers select the backend by passing an ``embedder`` to ``score()``; the
correlation engine plumbs one through :func:`reckora.correlation.engine.correlate`
when its caller wants the embedding path. Both backends emit the same
``ConfidenceContribution`` shape so the engine's downstream fusion is
unchanged.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..confidence import ConfidenceContribution
from ..embeddings import BioEmbedder, cosine_similarity

WORD_RE = re.compile(r"\w+")


def tokenise(text: str) -> Counter[str]:
    """Lowercase + word-tokenise. Returns a token-frequency Counter."""
    return Counter(WORD_RE.findall(text.lower()))


def cosine(a: Counter[str], b: Counter[str]) -> float:
    """Cosine similarity of two token-frequency vectors. Range [0.0, 1.0]."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[t] * b[t] for t in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def score(
    bio_a: str | None,
    bio_b: str | None,
    *,
    threshold: float = 0.5,
    embedder: BioEmbedder | None = None,
) -> ConfidenceContribution | None:
    """Return a contribution iff the two bios are similar above ``threshold``.

    When ``embedder`` is given the rule runs on dense-vector cosine
    similarity; otherwise it falls back to the lexical token-cosine
    baseline. Both backends produce the same ``ConfidenceContribution``
    shape (rule name, weight, reason) so the engine's downstream fusion
    is identical regardless of which path fired.

    A misbehaving embedder that returns ``[]`` for either input
    transparently degrades to the lexical baseline rather than producing
    a spurious zero score — the embedding path is treated as a *better*
    similarity oracle, never a *required* one.
    """
    if not bio_a or not bio_b:
        return None
    if embedder is not None:
        contrib = _score_embedding(bio_a, bio_b, threshold=threshold, embedder=embedder)
        if contrib is not None:
            return contrib
    return _score_lexical(bio_a, bio_b, threshold=threshold)


def _score_lexical(
    bio_a: str,
    bio_b: str,
    *,
    threshold: float,
) -> ConfidenceContribution | None:
    """Token-cosine path — Reckora's original, dependency-free baseline."""
    s = cosine(tokenise(bio_a), tokenise(bio_b))
    if s < threshold:
        return None
    weight = min(0.7, 0.5 * s + 0.2)
    return ConfidenceContribution(
        rule="bio_similarity",
        weight=weight,
        reason=f"bio token-cosine similarity {s:.2f} (threshold {threshold:.2f})",
    )


def _score_embedding(
    bio_a: str,
    bio_b: str,
    *,
    threshold: float,
    embedder: BioEmbedder,
) -> ConfidenceContribution | None:
    """Dense-embedding cosine path — opt-in via ``[embeddings]`` extra.

    Returns ``None`` if either bio embeds to an empty vector (signalling
    the embedder declined to embed) so callers can fall back to the
    lexical baseline rather than treating the embedding as authoritative.
    A higher weight ceiling (0.8 vs 0.7) reflects the stronger signal
    embeddings give: synonyms, paraphrases and translations now register
    as similar instead of disjoint.
    """
    vec_a = embedder.embed_one(bio_a)
    vec_b = embedder.embed_one(bio_b)
    if not vec_a or not vec_b:
        return None
    s = cosine_similarity(vec_a, vec_b)
    if s < threshold:
        return None
    weight = min(0.8, 0.5 * s + 0.3)
    return ConfidenceContribution(
        rule="bio_similarity",
        weight=weight,
        reason=f"bio embedding cosine similarity {s:.2f} (threshold {threshold:.2f})",
    )
