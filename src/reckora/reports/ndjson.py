"""NDJSON dossier export.

NDJSON (Newline-Delimited JSON) emits one JSON object per line, which
makes a dossier trivially streamable into downstream OSINT tooling
(``jq``, ``rg``, log shippers, dataframes, etc.) without first
deserialising a multi-megabyte single document.

Wire format
-----------
Every line is a single JSON object wrapping one logical record:

.. code-block:: text

    {"record": "subject",      "subject":      {...}}
    {"record": "trace",        "trace":        {...}}
    {"record": "trace",        "trace":        {...}}
    {"record": "timeline",     "timeline":     {...}}
    {"record": "anomaly",      "anomaly":      {...}}
    {"record": "edge",         "edge":         {...}}
    {"record": "ai_summary",   "ai_summary":   {"markdown": "..."}}
    {"record": "ai_hypotheses","ai_hypotheses":{"markdown": "..."}}
    {"record": "anchor",       "anchor":       {...}}

The discriminator is always under the top-level ``record`` key, and the
payload is always nested under a key named after the discriminator. This
shape composes cleanly with ``jq``: ``jq -c 'select(.record=="edge")
| .edge'`` projects all edges as standalone documents.

Records are emitted in a fixed order — subject, traces, timeline,
anomalies, edges, AI summary, AI hypotheses, anchor — so two runs that
yield the same dossier produce byte-identical NDJSON, which keeps the
evidence chain auditable for any downstream consumer that hashes the
report itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..anomaly import detect_anomalies
from ..evidence.anchor import Anchor
from ..models.entity import Edge, Subject, Trace
from .timeline import build_timeline


def iter_dossier_records(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
    anchor: Anchor | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each dossier record as an envelope dict, in canonical order.

    The envelope is ``{"record": <discriminator>, <discriminator>: <payload>}``.
    Optional sections (AI, anchor, anomalies) are skipped when their
    payload is absent rather than emitting empty placeholders.
    """
    yield {"record": "subject", "subject": subject.model_dump(mode="json")}

    for trace in traces:
        yield {"record": "trace", "trace": trace.model_dump(mode="json")}

    for entry in build_timeline(traces):
        yield {"record": "timeline", "timeline": entry.model_dump(mode="json")}

    for anomaly in detect_anomalies(traces):
        yield {"record": "anomaly", "anomaly": anomaly.model_dump(mode="json")}

    for edge in edges:
        yield {"record": "edge", "edge": edge.model_dump(mode="json")}

    if summary is not None:
        yield {"record": "ai_summary", "ai_summary": {"markdown": summary}}

    if hypotheses is not None:
        yield {
            "record": "ai_hypotheses",
            "ai_hypotheses": {"markdown": hypotheses},
        }

    if anchor is not None:
        yield {"record": "anchor", "anchor": anchor.model_dump(mode="json")}


def to_dossier_ndjson(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
    anchor: Anchor | None = None,
) -> str:
    """Render a complete dossier as an NDJSON string.

    Each line is a single JSON object terminated by a single ``\\n``.
    The final line is also terminated, so the output is safe to append
    to or concatenate with another NDJSON stream.

    Within each line we use ``sort_keys=True`` and the compact
    ``(",", ":")`` separator so the rendering is deterministic byte-for-
    byte across runs.
    """
    parts: list[str] = []
    for record in iter_dossier_records(
        subject=subject,
        traces=traces,
        edges=edges,
        summary=summary,
        hypotheses=hypotheses,
        anchor=anchor,
    ):
        parts.append(
            json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
    return "\n".join(parts) + "\n" if parts else ""
