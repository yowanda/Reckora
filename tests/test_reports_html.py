"""Tests for the HTML dossier renderer."""

from __future__ import annotations

from datetime import UTC, datetime

from reckora.evidence.chain import make_evidence
from reckora.models.entity import Edge, Identifier, Subject, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource
from reckora.reports.html import _confidence_band, to_dossier_html


def _build_dossier() -> tuple[Subject, list[Trace], list[Edge]]:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    extra = Identifier(type=IdentifierType.URL, value="https://example.org/@alice")
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trace_a = Trace(
        identifier=seed,
        source=TraceSource.GITHUB_API,
        fields={"platform": "github", "bio": "researcher", "empty": "", "missing": None},
        evidence=make_evidence(
            "https://api.github.com/users/alice",
            {"login": "alice"},
            fetched_at=fixed,
        ),
    )
    trace_b = Trace(
        identifier=extra,
        source=TraceSource.WEB_PROFILE,
        fields={"platform": "example.org", "bio": "researcher"},
        evidence=make_evidence(
            "https://example.org/@alice",
            {"title": "alice"},
            fetched_at=fixed,
        ),
    )
    edge = Edge(
        source=seed,
        target=extra,
        kind=EdgeKind.SIMILAR_BIO,
        confidence=0.85,
        reasons=["bio overlap"],
        supporting_evidence=[
            trace_a.evidence.payload_sha256,
            trace_b.evidence.payload_sha256,
        ],
    )
    subject = Subject(
        id="subj-html00000001",
        seed_identifier=seed,
        identifiers=[seed, extra],
        traces=[trace_a, trace_b],
    )
    return subject, [trace_a, trace_b], [edge]


def test_html_contains_seed_and_identifiers() -> None:
    subject, traces, edges = _build_dossier()
    html = to_dossier_html(subject=subject, traces=traces, edges=edges)
    assert "<!DOCTYPE html>" in html
    assert "username:alice" in html
    assert "https://example.org/@alice" in html
    assert subject.id in html


def test_html_renders_traces_with_evidence_hash() -> None:
    subject, traces, edges = _build_dossier()
    html = to_dossier_html(subject=subject, traces=traces, edges=edges)
    short_hash = traces[0].evidence.payload_sha256[:16]
    assert short_hash in html
    assert "github_api" in html
    assert "researcher" in html


def test_html_skips_empty_field_values() -> None:
    subject, traces, edges = _build_dossier()
    html = to_dossier_html(subject=subject, traces=traces, edges=edges)
    assert ">empty<" not in html
    assert ">missing<" not in html


def test_html_renders_confidence_badge_and_reasons() -> None:
    subject, traces, edges = _build_dossier()
    html = to_dossier_html(subject=subject, traces=traces, edges=edges)
    assert "85%" in html
    assert "badge-high" in html
    assert "bio overlap" in html


def test_html_renders_summary_and_hypotheses_only_when_present() -> None:
    subject, traces, edges = _build_dossier()
    plain = to_dossier_html(subject=subject, traces=traces, edges=edges)
    assert "AI summary" not in plain
    assert "AI hypotheses" not in plain

    enriched = to_dossier_html(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Subject is a security researcher.",
        hypotheses="Possibly active on additional platforms.",
    )
    assert "AI summary" in enriched
    assert "Subject is a security researcher." in enriched
    assert "AI hypotheses" in enriched


def test_html_escapes_user_supplied_strings() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="<script>alert(1)</script>")
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trace = Trace(
        identifier=seed,
        source=TraceSource.GITHUB_API,
        fields={"bio": "<b>researcher</b>"},
        evidence=make_evidence(
            "https://api.github.com/users/x",
            {"login": "x"},
            fetched_at=fixed,
        ),
    )
    subject = Subject(
        id="subj-escape00000001",
        seed_identifier=seed,
        identifiers=[seed],
        traces=[trace],
    )
    html = to_dossier_html(
        subject=subject, traces=[trace], edges=[], summary="<img src=x onerror=1>"
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_confidence_band_thresholds() -> None:
    assert _confidence_band(0.95) == "high"
    assert _confidence_band(0.7) == "high"
    assert _confidence_band(0.69) == "medium"
    assert _confidence_band(0.4) == "medium"
    assert _confidence_band(0.39) == "low"
    assert _confidence_band(0.0) == "low"
