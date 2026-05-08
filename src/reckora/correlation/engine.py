"""Correlation engine — applies all rules across each pair of Traces.

Each (trace_a, trace_b) pair is evaluated by every applicable rule. Each rule
that fires emits a `ConfidenceContribution`; the engine groups contributions
by `EdgeKind` and produces one `Edge` per (pair, kind) whose confidence is
the noisy-OR of the contributions.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations

from ..models.entity import Edge, Identifier, Trace
from ..models.enums import EdgeKind, IdentifierType
from .confidence import ConfidenceContribution, combine
from .embeddings import BioEmbedder
from .rules import avatar_phash, bio_similarity, timezone_overlap, username_mutation


def correlate(
    traces: Iterable[Trace],
    *,
    bio_embedder: BioEmbedder | None = None,
) -> list[Edge]:
    """Return Edges for every Trace pair that any rule fires on.

    ``bio_embedder`` is plumbed through to the bio-similarity rule so the
    caller can opt into dense-vector cosine similarity (typically backed
    by ``sentence-transformers`` via the ``[embeddings]`` extra) instead
    of the default lexical token-cosine baseline. Passing ``None`` keeps
    the historical behaviour and is dependency-free.
    """
    trace_list = list(traces)
    edges: list[Edge] = []
    for ta, tb in combinations(trace_list, 2):
        ia, ib = ta.identifier, tb.identifier
        if ia == ib:
            continue
        ev_hashes = [ta.evidence.payload_sha256, tb.evidence.payload_sha256]

        if ia.type == IdentifierType.USERNAME and ib.type == IdentifierType.USERNAME:
            c = username_mutation.score(ia.value, ib.value)
            if c is not None:
                edges.append(_edge(ia, ib, EdgeKind.USERNAME_MUTATION, [c], ev_hashes))

        ha = ta.fields.get("avatar_phash")
        hb = tb.fields.get("avatar_phash")
        if isinstance(ha, str) and isinstance(hb, str):
            c = avatar_phash.score(ha, hb)
            if c is not None:
                edges.append(_edge(ia, ib, EdgeKind.SAME_AVATAR, [c], ev_hashes))

        bio_a = ta.fields.get("bio")
        bio_b = tb.fields.get("bio")
        c = bio_similarity.score(
            bio_a if isinstance(bio_a, str) else None,
            bio_b if isinstance(bio_b, str) else None,
            embedder=bio_embedder,
        )
        if c is not None:
            edges.append(_edge(ia, ib, EdgeKind.SIMILAR_BIO, [c], ev_hashes))

        ah = ta.fields.get("activity_hours_utc")
        bh = tb.fields.get("activity_hours_utc")
        if isinstance(ah, list) and isinstance(bh, list):
            try:
                hours_a = [int(h) for h in ah]
                hours_b = [int(h) for h in bh]
            except (TypeError, ValueError):
                hours_a, hours_b = [], []
            c = timezone_overlap.score(hours_a, hours_b)
            if c is not None:
                edges.append(_edge(ia, ib, EdgeKind.TIMEZONE_OVERLAP, [c], ev_hashes))

    return edges


def _edge(
    a: Identifier,
    b: Identifier,
    kind: EdgeKind,
    contribs: list[ConfidenceContribution],
    ev_hashes: list[str],
) -> Edge:
    return Edge(
        source=a,
        target=b,
        kind=kind,
        confidence=combine(contribs),
        reasons=[c.reason for c in contribs],
        supporting_evidence=ev_hashes,
    )
