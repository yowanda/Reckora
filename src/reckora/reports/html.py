"""HTML dossier export.

A self-contained HTML document — no external CSS, no JS — so the dossier can
be opened from disk, archived, or piped into a reverse proxy without any
extra plumbing. The Phase 2 web UI will reuse the same Jinja2 template as a
component once the server seam lands.

The renderer mirrors :func:`reckora.reports.markdown.to_dossier_md`'s API so
the CLI can switch on ``--format`` without branching collector logic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jinja2

from ..anomaly import AnomalySeverity, detect_anomalies
from ..models.entity import Edge, Subject, Trace

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Reckora dossier — {{ seed_kind }}:{{ seed_value }}</title>
<style>
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
       max-width: 920px; margin: 2.5rem auto; padding: 0 1.25rem;
       color: #111; background: #fafafa; }
@media (prefers-color-scheme: dark) {
  body { color: #eaeaea; background: #15171a; }
  .card { background: #1d2025; border-color: #2a2e34; }
  code, pre { background: #11141a; color: #eaeaea; }
  .meta { color: #9aa0a6; }
}
h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .75rem;
     border-bottom: 1px solid #d0d0d0; padding-bottom: .25rem; }
.meta { color: #555; font-size: .85rem; }
.card { background: #fff; border: 1px solid #e3e3e3; border-radius: 6px;
        padding: .85rem 1rem; margin: .6rem 0; }
.kv { display: grid; grid-template-columns: 11rem 1fr; gap: .15rem .75rem;
      font-size: .92rem; }
.kv dt { color: #555; font-weight: 500; }
.kv dd { margin: 0; word-break: break-word; }
code { font: 0.88rem/1.4 ui-monospace, "SF Mono", Menlo, monospace;
       background: #f0f1f3; padding: 0 .25rem; border-radius: 3px; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 999px;
         font-size: .75rem; font-weight: 600; vertical-align: middle; }
.badge-high   { background: #1f7a4d; color: #fff; }
.badge-medium { background: #b88600; color: #fff; }
.badge-low    { background: #99312a; color: #fff; }
ul.idents { list-style: none; padding: 0; margin: 0; }
ul.idents li { display: inline-block; margin: .15rem .3rem .15rem 0;
               padding: .15rem .55rem; background: #eef1f5;
               border-radius: 999px; font-size: .85rem; }
@media (prefers-color-scheme: dark) {
  ul.idents li { background: #232831; }
  code { background: #11141a; }
}
.empty { color: #888; font-style: italic; }
.ai { white-space: pre-wrap; font-size: .95rem; }
ul.anomalies { list-style: none; padding: 0; margin: 0; }
ul.anomalies li { padding: .55rem .75rem; margin: .35rem 0;
                  border-left: 4px solid #d0d0d0; background: #fff;
                  border-radius: 0 6px 6px 0; }
ul.anomalies li.sev-high   { border-left-color: #99312a; }
ul.anomalies li.sev-medium { border-left-color: #b88600; }
ul.anomalies li.sev-low    { border-left-color: #1f7a4d; }
ul.anomalies .kind { font-size: .8rem; color: #555; text-transform: uppercase;
                     letter-spacing: .04em; margin-left: .4rem; }
ul.anomalies .refs { font-size: .8rem; color: #666; margin-top: .25rem; }
@media (prefers-color-scheme: dark) {
  ul.anomalies li { background: #1d2025; }
  ul.anomalies .kind { color: #9aa0a6; }
  ul.anomalies .refs { color: #9aa0a6; }
}
</style>
</head>
<body>
<h1>Reckora dossier — <code>{{ seed_kind }}:{{ seed_value }}</code></h1>
<div class="meta">generated {{ generated_at }} · subject <code>{{ subject_id }}</code></div>

<h2>Identifiers</h2>
{% if identifiers %}
<ul class="idents">
{% for ident in identifiers %}
  <li><code>{{ ident.type.value }}</code> {{ ident.value }}</li>
{% endfor %}
</ul>
{% else %}
<p class="empty">none</p>
{% endif %}

<h2>Traces</h2>
{% if traces %}
{% for t in traces %}
<div class="card">
  <div><strong>{{ t.source.value }}</strong> · <code>{{ t.identifier.value }}</code></div>
  <dl class="kv">
    <dt>evidence</dt>
    <dd><code>{{ t.evidence.payload_sha256[:16] }}…</code>
        fetched {{ t.evidence.fetched_at.isoformat() }}</dd>
    <dt>source</dt>
    <dd><a href="{{ t.evidence.source_url }}">{{ t.evidence.source_url }}</a></dd>
    {% if t.evidence.archive_url %}
    <dt>archive</dt>
    <dd><a href="{{ t.evidence.archive_url }}">{{ t.evidence.archive_url }}</a></dd>
    {% endif %}
    {% if t.evidence.screenshot_path %}
    <dt>screenshot</dt>
    <dd><a href="{{ t.evidence.screenshot_path }}">{{ t.evidence.screenshot_path }}</a></dd>
    {% endif %}
    {% for k, v in t.fields.items() if v not in (None, "", []) %}
    <dt>{{ k }}</dt>
    <dd><code>{{ v }}</code></dd>
    {% endfor %}
  </dl>
</div>
{% endfor %}
{% else %}
<p class="empty">no traces</p>
{% endif %}

<h2>Anomalies</h2>
{% if anomalies %}
<ul class="anomalies">
{% for a in anomalies %}
  <li class="sev-{{ a.severity.value }}">
    <span class="badge badge-{{ severity_band(a.severity) }}">{{ a.severity.value|upper }}</span>
    <span class="kind">{{ a.kind.value }}</span>
    <div>{{ a.message }}</div>
    {% if a.supporting_evidence %}
    <div class="refs">
      {% for sha in a.supporting_evidence %}<code>{{ sha[:16] }}…</code>{% if not loop.last %} {% endif %}{% endfor %}
    </div>
    {% endif %}
  </li>
{% endfor %}
</ul>
{% else %}
<p class="empty">no anomalies detected</p>
{% endif %}

<h2>Correlation edges</h2>
{% if edges %}
{% for e in edges %}
<div class="card">
  <div>
    <code>{{ e.source.value }}</code> ↔ <code>{{ e.target.value }}</code>
    · <strong>{{ e.kind.value }}</strong>
    <span class="badge badge-{{ confidence_band(e.confidence) }}">
      {{ "%.0f"|format(e.confidence * 100) }}%
    </span>
  </div>
  {% if e.reasons %}
  <ul>
    {% for r in e.reasons %}<li>{{ r }}</li>{% endfor %}
  </ul>
  {% endif %}
</div>
{% endfor %}
{% else %}
<p class="empty">no edges</p>
{% endif %}

{% if summary %}
<h2>AI summary</h2>
<div class="card ai">{{ summary }}</div>
{% endif %}
{% if hypotheses %}
<h2>AI hypotheses</h2>
<div class="card ai">{{ hypotheses }}</div>
{% endif %}
</body>
</html>
"""


def _confidence_band(value: float) -> str:
    if value >= 0.7:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def _severity_band(severity: AnomalySeverity) -> str:
    """Reuse the confidence-badge palette for anomaly severities.

    HIGH severity = LOW confidence band (red), so the visual weight matches
    "this is bad".
    """
    return {
        AnomalySeverity.HIGH: "low",
        AnomalySeverity.MEDIUM: "medium",
        AnomalySeverity.LOW: "high",
    }[severity]


def to_dossier_html(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
) -> str:
    """Render a complete dossier as a self-contained HTML document."""
    env = jinja2.Environment(
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.globals["confidence_band"] = _confidence_band
    env.globals["severity_band"] = _severity_band
    template = env.from_string(_TEMPLATE)
    return template.render(
        seed_kind=subject.seed_identifier.type.value,
        seed_value=subject.seed_identifier.value,
        subject_id=subject.id,
        generated_at=datetime.now(UTC).isoformat(),
        identifiers=subject.identifiers,
        traces=traces,
        anomalies=detect_anomalies(traces),
        edges=edges,
        summary=summary,
        hypotheses=hypotheses,
    )
