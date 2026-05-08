"""Markdown dossier export."""

from __future__ import annotations

from datetime import UTC, datetime

from ..anomaly import detect_anomalies
from ..models.entity import Edge, Subject, Trace


def to_dossier_md(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
) -> str:
    """Render a complete dossier as a Markdown document."""
    lines: list[str] = []
    seed = subject.seed_identifier
    lines.append(f"# Reckora dossier — {seed.type.value}:{seed.value}")
    lines.append("")
    lines.append(f"_generated: {datetime.now(UTC).isoformat()}_")
    lines.append("")

    lines.append("## Identifiers")
    if subject.identifiers:
        for ident in subject.identifiers:
            lines.append(f"- `{ident.type.value}` -> `{ident.value}`")
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Traces")
    if traces:
        for t in traces:
            lines.append(f"### `{t.source.value}` :: {t.identifier.value}")
            short = t.evidence.payload_sha256[:16]
            lines.append(f"- evidence: `{short}…` (fetched {t.evidence.fetched_at.isoformat()})")
            lines.append(f"- source: {t.evidence.source_url}")
            if t.evidence.archive_url:
                lines.append(f"- archive: {t.evidence.archive_url}")
            if t.evidence.screenshot_path:
                lines.append(f"- screenshot: {t.evidence.screenshot_path}")
            for k, v in t.fields.items():
                if v in (None, "", []):
                    continue
                lines.append(f"- {k}: `{v}`")
            lines.append("")
    else:
        lines.append("_no traces_")
        lines.append("")

    lines.append("## Anomalies")
    anomalies = detect_anomalies(traces)
    if anomalies:
        for a in anomalies:
            short_refs = [sha[:16] for sha in a.supporting_evidence]
            evidence = " ".join(f"`{s}…`" for s in short_refs)
            lines.append(
                f"- **{a.severity.value.upper()}** · `{a.kind.value}` — {a.message} ({evidence})"
            )
    else:
        lines.append("_no anomalies detected_")
    lines.append("")

    lines.append("## Correlation edges")
    if edges:
        for e in edges:
            lines.append(
                f"- `{e.source.value}` ↔ `{e.target.value}` — "
                f"**{e.kind.value}** (confidence {e.confidence:.2f})"
            )
            for r in e.reasons:
                lines.append(f"  - {r}")
    else:
        lines.append("_no edges_")
    lines.append("")

    if summary:
        lines.append("## AI summary")
        lines.append(summary)
        lines.append("")

    if hypotheses:
        lines.append("## AI hypotheses")
        lines.append(hypotheses)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
