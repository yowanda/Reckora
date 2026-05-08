# Reckora Roadmap

The 10 layers of the Reckora vision and where each one lives in the codebase.

| # | Layer | Status | Notes |
|---|---|---|---|
| 1 | Input | partial | `IdentifierType` covers username, email, domain, url, phone, wallet, avatar. Active collectors: username, domain, url. Phone / wallet / avatar input — Phase 3. |
| 2 | Collection | partial | GitHub API, WHOIS / RDAP, web profile (OG / `<title>`). Breach lookup, blockchain tracing, archive lookup — Phase 2–3. |
| 3 | Normalization | done | Each collector emits a uniform `Trace.fields` schema. Evidence is canonicalised (sorted keys, no whitespace, UTF-8) before hashing. |
| 4 | Correlation | done (rule-based) | Rules: `username_mutation`, `avatar_phash`, `timezone_overlap`, `bio_similarity`. Probabilistic-OR fusion. Embedding-based bio similarity (`sentence-transformers`) — Phase 3. |
| 5 | AI Reasoning | done | Async OpenAI client with `summarize` and `hypothesize`. Prompt forbids invented facts and requires `ev:<8-hex>` citations. Anomaly detector — Phase 3. |
| 6 | Confidence Scoring | done | Per-edge `confidence: float [0, 1]`, `reasons[]`, `supporting_evidence[]` (payload SHAs). |
| 7 | Evidence Chain | partial | Source URL + timestamp + SHA-256 of canonicalised payload. Screenshot capture and archive.org snapshot — Phase 2. |
| 8 | Graph | done (in-process) | NetworkX `MultiDiGraph[str]`. Neo4j adapter — Phase 2. |
| 9 | Reporting | partial | JSON + Markdown dossier. HTML / PDF dossier and timeline reconstruction — Phase 2. |
| 10 | Web UI | not yet | CLI only. Dashboard / graph viewer / report viewer — Phase 2. |

## Phase plan

- **Phase 1 — MVP skeleton** (this PR): entity-first data model, evidence chain, three collectors, rule-based correlation engine, evidence-bounded AI reasoning, CLI dossier, CI matrix on Python 3.11 + 3.12.
- **Phase 2 — Persistence & UI**: SQLite storage behind a repository seam, forensic screenshot capture, archive.org snapshot, HTML / PDF dossier, web UI with graph viewer, optional Neo4j adapter.
- **Phase 3 — Sensor expansion**: phone collector, crypto wallet collector (Etherscan / Blockstream), `sentence-transformers` bio embeddings, anomaly detector, breach lookup behind a feature flag.
- **Phase 4 — Autonomous agents**: hypothesis-driven recursive identifier expansion gated by confidence floors, AI-proposed collector plans verified by rule-based engines.
- **Phase 5 — Collaborative platform**: multi-user investigations, shared evidence library, role-based reporting.

## Strategic posture

We deliberately do **not** compete on site count. Reckora's edge is intelligence quality:

- entity resolution rather than enumeration
- explainable per-edge confidence with cited evidence
- evidence-bounded AI that cannot invent facts
- a graph-first dossier rather than a list of hits
