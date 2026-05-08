"""Tests for the embedding-based bio-similarity backend.

These tests never load a real ``sentence-transformers`` model — they wire a
small in-process ``BioEmbedder`` so the rule's plumbing stays fast and
hermetic. The actual Hugging Face model lives behind the ``[embeddings]``
extra and is exercised manually by maintainers (it pulls a ~22 MB checkpoint
which we don't want CI to download).
"""

from __future__ import annotations

import math

import pytest

from reckora.correlation.embeddings import (
    BioEmbedder,
    SentenceTransformerEmbedder,
    cosine_similarity,
)
from reckora.correlation.engine import correlate
from reckora.correlation.rules.bio_similarity import score
from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource


class _StaticEmbedder:
    """Deterministic embedder backed by a static lookup table.

    Keeps the tests offline, dependency-free, and reproducible across
    machines. Inputs not in the table embed to ``[]`` so we can exercise
    the lexical-fallback path without monkey-patching.
    """

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed_one(self, text: str) -> list[float]:
        return list(self._table.get(text, []))


def test_static_embedder_satisfies_protocol() -> None:
    embedder: BioEmbedder = _StaticEmbedder({})
    assert isinstance(embedder, BioEmbedder)


def test_cosine_similarity_identical_is_one() -> None:
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_obtuse_is_negative() -> None:
    s = cosine_similarity([1.0, 0.0], [-1.0, 0.0])
    assert s == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ([], [1.0]),
        ([1.0], []),
        ([1.0], [1.0, 0.0]),
        ([0.0, 0.0], [1.0, 0.0]),
        ([1.0, 0.0], [0.0, 0.0]),
    ],
)
def test_cosine_similarity_degenerate_inputs_return_zero(a: list[float], b: list[float]) -> None:
    assert cosine_similarity(a, b) == 0.0


def test_score_uses_embedder_when_provided() -> None:
    # Two bios with no shared tokens but parallel embedding vectors —
    # the lexical baseline scores them at zero, the embedder pulls the
    # rule above its threshold.
    embedder = _StaticEmbedder(
        {
            "infosec engineer": [1.0, 0.0, 0.0],
            "security researcher": [0.95, 0.05, 0.0],
        }
    )

    contrib = score(
        "infosec engineer",
        "security researcher",
        threshold=0.5,
        embedder=embedder,
    )

    assert contrib is not None
    assert contrib.rule == "bio_similarity"
    assert "embedding cosine similarity" in contrib.reason
    # Higher ceiling than the lexical path so embedder hits land at >=0.7.
    assert 0.5 <= contrib.weight <= 0.8


def test_score_falls_back_to_lexical_when_embedder_returns_empty() -> None:
    # The static embedder doesn't know these strings, so it returns
    # ``[]`` for both — the rule MUST fall back to the lexical path
    # rather than treating the missing embeddings as authoritative
    # zero-similarity.
    embedder = _StaticEmbedder({})

    contrib = score(
        "Security researcher and OSINT enthusiast.",
        "OSINT researcher; security and incident response.",
        threshold=0.3,
        embedder=embedder,
    )

    assert contrib is not None
    # Reason text differentiates the two backends — a fallback hit must
    # advertise the lexical path so dossier readers can tell.
    assert "token-cosine" in contrib.reason


def test_score_below_threshold_with_embedder_returns_none() -> None:
    embedder = _StaticEmbedder(
        {
            "alpha": [1.0, 0.0, 0.0],
            "beta": [0.0, 1.0, 0.0],
        }
    )

    assert score("alpha", "beta", threshold=0.5, embedder=embedder) is None


def test_score_none_inputs_with_embedder() -> None:
    embedder = _StaticEmbedder({"a": [1.0]})
    assert score(None, "a", embedder=embedder) is None
    assert score("a", None, embedder=embedder) is None
    assert score("", "a", embedder=embedder) is None


def test_engine_passes_embedder_to_bio_rule() -> None:
    # Two traces with disjoint bio token sets but parallel embeddings.
    # Without an embedder, the lexical rule scores zero; with one, the
    # engine emits a SIMILAR_BIO edge.
    bio_a = "infosec engineer"
    bio_b = "security researcher"
    embedder = _StaticEmbedder(
        {
            bio_a: [1.0, 0.0, 0.0],
            bio_b: [0.95, 0.05, 0.0],
        }
    )

    ident_a = Identifier(type=IdentifierType.USERNAME, value="alpha")
    ident_b = Identifier(type=IdentifierType.URL, value="https://example.org/@beta")
    ev_a = make_evidence("https://example.com/a", {"x": 1})
    ev_b = make_evidence("https://example.com/b", {"x": 2})
    ta = Trace(
        identifier=ident_a,
        source=TraceSource.GITHUB_API,
        fields={"bio": bio_a},
        evidence=ev_a,
    )
    tb = Trace(
        identifier=ident_b,
        source=TraceSource.WEB_PROFILE,
        fields={"bio": bio_b},
        evidence=ev_b,
    )

    edges_lexical = [e for e in correlate([ta, tb]) if e.kind == EdgeKind.SIMILAR_BIO]
    assert edges_lexical == []

    edges_embed = [
        e for e in correlate([ta, tb], bio_embedder=embedder) if e.kind == EdgeKind.SIMILAR_BIO
    ]
    assert len(edges_embed) == 1
    assert "embedding cosine similarity" in edges_embed[0].reasons[0]


def test_sentence_transformer_embedder_module_name_is_default() -> None:
    embedder = SentenceTransformerEmbedder()
    assert embedder.model_name == SentenceTransformerEmbedder.DEFAULT_MODEL


def test_sentence_transformer_embedder_empty_text_returns_empty_vector() -> None:
    # Short-circuits before touching the model so it never tries to
    # download anything in CI.
    embedder = SentenceTransformerEmbedder()
    assert embedder.embed_one("") == []


def test_sentence_transformer_embedder_uses_injected_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model loading is lazy and goes through a real ``encode()`` shape.

    We monkey-patch ``SentenceTransformer`` itself so the test stays
    offline — the assertion is that the embedder funnels its inputs
    through the standard ``encode([text])`` API and decodes the result
    to a Python list.
    """

    class _FakeTensor:
        def __init__(self, values: list[float]) -> None:
            self._values = values

        def tolist(self) -> list[float]:
            return list(self._values)

    class _FakeModel:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls: list[list[str]] = []

        def encode(
            self,
            texts: list[str],
            *,
            convert_to_numpy: bool,
            show_progress_bar: bool,
        ) -> list[_FakeTensor]:
            assert convert_to_numpy is False
            assert show_progress_bar is False
            self.calls.append(list(texts))
            # Pretend each token contributes a 1.0 to the vector.
            return [_FakeTensor([float(len(t)) for t in texts])]

    fake_model_holder: dict[str, _FakeModel] = {}

    class _FakeST:
        def __new__(cls, name: str) -> _FakeModel:  # type: ignore[misc]
            model = _FakeModel(name)
            fake_model_holder["model"] = model
            return model

    fake_module = type(
        "FakeSentenceTransformersModule",
        (),
        {"SentenceTransformer": _FakeST},
    )()
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", fake_module)

    embedder = SentenceTransformerEmbedder("test-model")
    vec = embedder.embed_one("hello")

    assert math.isfinite(vec[0])
    assert vec == [5.0]  # len("hello")
    model = fake_model_holder["model"]
    assert model.calls == [["hello"]]
    assert model.name == "test-model"

    # Second call reuses the loaded model — no duplicate constructor.
    embedder.embed_one("hi")
    assert len(model.calls) == 2
