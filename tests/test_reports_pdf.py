"""Tests for the PDF dossier renderer."""

from __future__ import annotations

from datetime import UTC, datetime

from reckora.evidence.chain import make_evidence
from reckora.models.entity import Edge, Identifier, Subject, Trace
from reckora.models.enums import EdgeKind, IdentifierType, TraceSource
from reckora.reports.pdf import _confidence_band, to_dossier_pdf

PDF_MAGIC = b"%PDF-"


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
        id="subj-pdf000000001",
        seed_identifier=seed,
        identifiers=[seed, extra],
        traces=[trace_a, trace_b],
    )
    return subject, [trace_a, trace_b], [edge]


def test_pdf_starts_with_magic_header() -> None:
    subject, traces, edges = _build_dossier()
    body = to_dossier_pdf(subject=subject, traces=traces, edges=edges)
    assert isinstance(body, bytes)
    assert body.startswith(PDF_MAGIC)
    # PDFs always end with %%EOF (with optional trailing newline / whitespace).
    assert b"%%EOF" in body[-32:]


def test_pdf_grows_with_richer_dossier() -> None:
    subject, traces, edges = _build_dossier()
    minimal = to_dossier_pdf(
        subject=subject,
        traces=[],
        edges=[],
    )
    full = to_dossier_pdf(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Subject is a security researcher.",
        hypotheses="Possibly active on additional platforms.",
    )
    assert len(full) > len(minimal)


def test_pdf_grows_with_timeline_entries() -> None:
    """Adding traces (which feed the timeline) makes the PDF bigger than an
    edge-only dossier of the same shape."""
    subject, traces, edges = _build_dossier()
    edges_only = to_dossier_pdf(subject=subject, traces=[], edges=edges)
    full = to_dossier_pdf(subject=subject, traces=traces, edges=edges)
    assert full.startswith(PDF_MAGIC)
    assert edges_only.startswith(PDF_MAGIC)
    assert len(full) > len(edges_only)


def test_pdf_renders_archive_url_when_present() -> None:
    subject, traces, edges = _build_dossier()
    snap = "https://web.archive.org/web/2026/https://api.github.com/users/alice"
    augmented = [
        traces[0].model_copy(
            update={"evidence": traces[0].evidence.model_copy(update={"archive_url": snap})}
        ),
        traces[1],
    ]
    with_archive = to_dossier_pdf(subject=subject, traces=augmented, edges=edges)
    without_archive = to_dossier_pdf(subject=subject, traces=traces, edges=edges)
    # Same shape, but the archive variant carries an extra link annotation.
    assert with_archive.startswith(PDF_MAGIC)
    assert without_archive.startswith(PDF_MAGIC)
    assert len(with_archive) > len(without_archive)


def test_pdf_renders_screenshot_path_when_present() -> None:
    subject, traces, edges = _build_dossier()
    shot = "/screenshots/alice.png"
    augmented = [
        traces[0].model_copy(
            update={"evidence": traces[0].evidence.model_copy(update={"screenshot_path": shot})}
        ),
        traces[1],
    ]
    with_shot = to_dossier_pdf(subject=subject, traces=augmented, edges=edges)
    without_shot = to_dossier_pdf(subject=subject, traces=traces, edges=edges)
    assert with_shot.startswith(PDF_MAGIC)
    assert without_shot.startswith(PDF_MAGIC)
    assert len(with_shot) > len(without_shot)


def test_pdf_includes_summary_and_hypotheses_only_when_present() -> None:
    subject, traces, edges = _build_dossier()
    bare = to_dossier_pdf(subject=subject, traces=traces, edges=edges)
    enriched = to_dossier_pdf(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Subject is a security researcher.",
        hypotheses="Possibly active on additional platforms.",
    )
    assert len(enriched) > len(bare)


def test_pdf_renderer_handles_unsafe_strings_without_raising() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="<script>alert(1)</script>")
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trace = Trace(
        identifier=seed,
        source=TraceSource.GITHUB_API,
        fields={"bio": "<b>researcher</b> & friends"},
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
    body = to_dossier_pdf(
        subject=subject,
        traces=[trace],
        edges=[],
        summary="<img src=x onerror=1> & more",
    )
    assert body.startswith(PDF_MAGIC)


def test_confidence_band_thresholds() -> None:
    assert _confidence_band(0.95) == "HIGH"
    assert _confidence_band(0.7) == "HIGH"
    assert _confidence_band(0.69) == "MEDIUM"
    assert _confidence_band(0.4) == "MEDIUM"
    assert _confidence_band(0.39) == "LOW"
    assert _confidence_band(0.0) == "LOW"
