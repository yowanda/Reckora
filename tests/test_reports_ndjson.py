"""Tests for the NDJSON dossier renderer."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from reckora.correlation.engine import correlate
from reckora.evidence.anchor import Anchor
from reckora.evidence.timestamp import CalendarReceipt
from reckora.models.entity import Identifier, Subject, Trace
from reckora.models.enums import IdentifierType
from reckora.reports.ndjson import iter_dossier_records, to_dossier_ndjson


def _make_subject(traces: list[Trace]) -> Subject:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    return Subject(
        id="subj-test",
        seed_identifier=seed,
        identifiers=[seed],
        traces=traces,
    )


def _parse(text: str) -> list[dict[str, object]]:
    """Parse NDJSON output back into a list of dicts; used for assertions."""
    return [json.loads(line) for line in text.splitlines() if line]


def test_emits_one_object_per_line(github_trace_alice: Trace, web_trace_alice_twin: Trace) -> None:
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    subject = _make_subject(traces)

    text = to_dossier_ndjson(subject=subject, traces=traces, edges=edges)

    # Every non-empty line is valid JSON.
    lines = [line for line in text.splitlines() if line]
    assert lines, "expected at least one record"
    parsed = [json.loads(line) for line in lines]
    for record in parsed:
        assert "record" in record
        # Each envelope has exactly two top-level keys: ``record`` and the
        # discriminator-named payload key.
        assert set(record.keys()) == {"record", record["record"]}


def test_records_appear_in_canonical_order(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    subject = _make_subject(traces)

    text = to_dossier_ndjson(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Looks like the same person.",
        hypotheses="- H1",
    )
    record_types = [r["record"] for r in _parse(text)]

    # Subject is always first.
    assert record_types[0] == "subject"
    # Traces precede timeline / anomalies.
    assert record_types.index("trace") < record_types.index("timeline")
    # AI sections come after edges (when present).
    assert record_types.index("edge") < record_types.index("ai_summary")
    assert record_types.index("ai_summary") < record_types.index("ai_hypotheses")


def test_skips_optional_sections_when_payload_absent(
    github_trace_alice: Trace,
) -> None:
    """No AI summary, no anchor -> no ``ai_*`` or ``anchor`` records."""
    traces = [github_trace_alice]
    subject = _make_subject(traces)
    text = to_dossier_ndjson(subject=subject, traces=traces, edges=[])
    record_types = [r["record"] for r in _parse(text)]
    assert "ai_summary" not in record_types
    assert "ai_hypotheses" not in record_types
    assert "anchor" not in record_types


def test_includes_anchor_when_provided(github_trace_alice: Trace) -> None:
    anchor = Anchor(
        merkle_root="a" * 64,
        leaf_hashes=["b" * 64],
        created_at=datetime.now(UTC),
        receipts=[
            CalendarReceipt(
                calendar_url="https://a.calendar.example",
                receipt_b64="QUJD",
                submitted_at=datetime.now(UTC),
            ),
        ],
    )
    traces = [github_trace_alice]
    text = to_dossier_ndjson(
        subject=_make_subject(traces),
        traces=traces,
        edges=[],
        anchor=anchor,
    )
    types = [r["record"] for r in _parse(text)]
    assert types[-1] == "anchor"


def test_renders_trailing_newline() -> None:
    """Output must end on ``\\n`` so consumers can blindly concatenate."""
    seed = Identifier(type=IdentifierType.USERNAME, value="ghost")
    subject = Subject(id="subj-empty", seed_identifier=seed, identifiers=[seed])
    text = to_dossier_ndjson(subject=subject, traces=[], edges=[])
    assert text.endswith("\n")


def test_empty_input_produces_only_subject_line() -> None:
    """No traces / no edges still emits the subject record."""
    seed = Identifier(type=IdentifierType.USERNAME, value="ghost")
    subject = Subject(id="subj-empty", seed_identifier=seed, identifiers=[seed])
    text = to_dossier_ndjson(subject=subject, traces=[], edges=[])
    parsed = _parse(text)
    assert len(parsed) == 1
    assert parsed[0]["record"] == "subject"


def test_iter_dossier_records_does_not_render_strings(
    github_trace_alice: Trace,
) -> None:
    """The streaming helper yields envelope dicts, not JSON strings,
    so callers can plug it into their own serialiser (e.g. an HTTP
    streaming response)."""
    traces = [github_trace_alice]
    records = list(
        iter_dossier_records(
            subject=_make_subject(traces),
            traces=traces,
            edges=[],
        )
    )
    assert records[0]["record"] == "subject"
    assert isinstance(records[0]["subject"], dict)


def test_output_is_byte_deterministic(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    """Two renders of the same dossier must produce identical bytes,
    because the NDJSON itself may be hashed downstream."""
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    subject = _make_subject(traces)
    a = to_dossier_ndjson(subject=subject, traces=traces, edges=edges)
    b = to_dossier_ndjson(subject=subject, traces=traces, edges=edges)
    assert a == b


def test_jq_friendly_projection(github_trace_alice: Trace, web_trace_alice_twin: Trace) -> None:
    """``select(.record == "edge") | .edge`` is the documented
    consumption pattern; verify the shape matches it."""
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    text = to_dossier_ndjson(subject=_make_subject(traces), traces=traces, edges=edges)
    edge_payloads = [r["edge"] for r in _parse(text) if r["record"] == "edge"]
    assert len(edge_payloads) == len(edges)
    for payload in edge_payloads:
        assert isinstance(payload, dict)
        # An Edge always has a kind, two endpoints, and a confidence score.
        assert "kind" in payload
        assert "confidence" in payload
