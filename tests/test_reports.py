"""Tests for the dossier renderers."""

from __future__ import annotations

import json

from reckora.correlation.engine import correlate
from reckora.models.entity import Identifier, Subject, Trace
from reckora.models.enums import IdentifierType
from reckora.reports.json_export import to_dossier_dict, to_dossier_json
from reckora.reports.markdown import to_dossier_md


def _make_subject(traces: list[Trace]) -> Subject:
    seed = Identifier(type=IdentifierType.USERNAME, value="alice")
    return Subject(
        id="subj-test",
        seed_identifier=seed,
        identifiers=[seed],
        traces=traces,
    )


def test_dossier_dict_round_trips_via_json(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    subject = _make_subject(traces)

    payload = to_dossier_dict(subject=subject, traces=traces, edges=edges)
    text = to_dossier_json(subject=subject, traces=traces, edges=edges)
    assert json.loads(text) == payload
    assert payload["subject"]["id"] == "subj-test"
    assert len(payload["traces"]) == 2
    assert len(payload["timeline"]) == 2
    timestamps = [entry["timestamp"] for entry in payload["timeline"]]
    assert timestamps == sorted(timestamps)
    # The two fixtures intentionally carry divergent display names
    # ("Alice A" vs "alice"), so the anomaly detector picks that up.
    assert len(payload["anomalies"]) == 1
    assert payload["anomalies"][0]["kind"] == "name_divergence"
    assert payload["ai"] == {"summary": None, "hypotheses": None}


def test_dossier_md_contains_key_sections(
    github_trace_alice: Trace, web_trace_alice_twin: Trace
) -> None:
    traces = [github_trace_alice, web_trace_alice_twin]
    edges = correlate(traces)
    subject = _make_subject(traces)

    md = to_dossier_md(
        subject=subject,
        traces=traces,
        edges=edges,
        summary="Likely the same person.",
        hypotheses="- H1: alice on github == alice on example.org",
    )
    assert md.startswith("# Reckora dossier — username:alice")
    assert "## Identifiers" in md
    assert "## Traces" in md
    assert "## Timeline" in md
    assert "## Anomalies" in md
    assert "## Correlation edges" in md
    assert "## AI summary" in md
    assert "Likely the same person." in md
    assert "## AI hypotheses" in md
    # Timeline and Anomalies both appear after Traces and before Correlation edges.
    assert md.index("## Traces") < md.index("## Timeline") < md.index("## Correlation edges")
    assert md.index("## Traces") < md.index("## Anomalies") < md.index("## Correlation edges")


def test_dossier_md_handles_no_traces() -> None:
    seed = Identifier(type=IdentifierType.USERNAME, value="ghost")
    subject = Subject(id="subj-empty", seed_identifier=seed, identifiers=[seed])
    md = to_dossier_md(subject=subject, traces=[], edges=[])
    assert "_no traces_" in md
    assert "_no events_" in md
    assert "_no anomalies detected_" in md
    assert "_no edges_" in md
    assert "## AI summary" not in md
