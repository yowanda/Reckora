"""JSON dossier export."""

from __future__ import annotations

import json
from typing import Any

from ..anomaly import detect_anomalies
from ..models.entity import Edge, Subject, Trace
from .timeline import build_timeline


def to_dossier_dict(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
) -> dict[str, Any]:
    """Render a complete dossier as a plain Python dict."""
    return {
        "subject": subject.model_dump(mode="json"),
        "traces": [t.model_dump(mode="json") for t in traces],
        "timeline": [e.model_dump(mode="json") for e in build_timeline(traces)],
        "anomalies": [a.model_dump(mode="json") for a in detect_anomalies(traces)],
        "edges": [e.model_dump(mode="json") for e in edges],
        "ai": {
            "summary": summary,
            "hypotheses": hypotheses,
        },
    }


def to_dossier_json(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
) -> str:
    """Render a complete dossier as a pretty JSON string."""
    return json.dumps(
        to_dossier_dict(
            subject=subject,
            traces=traces,
            edges=edges,
            summary=summary,
            hypotheses=hypotheses,
        ),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
