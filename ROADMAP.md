# Reckora Roadmap

AI-native OSINT investigation — entity resolution, evidence-graph reasoning, explainable intelligence.

## Layers

| # | Layer | Status |
|---|---|---|
| 1 | Input — `IdentifierType`: username, email, domain, url, phone, wallet, avatar; auto-detection from a raw string via `reckora.models.detect.detect_identifier_kind` | done |
| 2 | Collection — GitHub, HN, Keybase, Gravatar, Reddit, WHOIS / RDAP, DNS records (NS / MX / TXT / SPF / DMARC / DNSSEC), web profile, email, phone (offline), HIBP (opt-in), wallet (BTC / ETH / SOL), avatar pHash | done |
| 3 | Normalization — uniform `Trace.fields`, canonicalised evidence | done |
| 4 | Correlation — `username_mutation`, `avatar_phash`, `timezone_overlap`, `bio_similarity` (lexical + optional embeddings) | done |
| 5 | AI Reasoning — async OpenAI client + ChatGPT OAuth, evidence-bounded prompts, anomaly detector | done |
| 6 | Confidence Scoring — per-edge `confidence`, `reasons[]`, `supporting_evidence[]` | done |
| 7 | Evidence Chain — SHA-256 + Wayback, screenshots, Merkle root, OpenTimestamps | done |
| 8 | Graph — NetworkX in-process; optional Neo4j adapter (`[neo4j]` extra) | done |
| 9 | Reporting — JSON / NDJSON / Markdown / HTML / PDF dossiers, timeline + anomalies | done |
| 10 | Web UI — FastAPI backend (`apps/api/`) + Vite + React + TS SPA (`apps/web/`) | landed |

## Phases

- **Phase 1 — MVP skeleton.** Landed.
- **Phase 2 — Persistence & UI.** Backend + SPA landed.
- **Phase 3 — descoped** (Cosmos / TRON wallet adapters dropped).
- **Phase 4 — Autonomous agents.** `AgentLoop` + ChatGPT OAuth login. Landed.
- **Phase 5 — Collaborative platform.** See checklist below.

## Phase 5 — Collaborative platform

Multi-user investigations on top of the FastAPI backend. All modules live under `apps/api/reckora_api/`.

| Step | Feature | Module | Status |
|---|---|---|---|
| 1 | Comments + assignees | `collab/` | landed |
| 2 | Cross-dossier shared evidence | `xref/` | landed |
| 3 | Per-dossier activity feed | `activity/` | landed |
| 4 | RBAC + subject ownership / sharing | `access/` | landed |
| 5 | Comment reactions | `reactions/` | landed |
| 6 | Per-dossier labels + global catalog | `labels/` | landed |
| 7 | Per-dossier status state machine | `dossier_status/` | landed |
| 8 | Watchers / following | `watchers/` | landed |
| 9 | Comment threading / one-level replies | `collab/` | landed |
| 10 | `@username` mentions + per-actor feed | `mentions/` | landed |
| 11 | Per-actor pinned dossiers | `pins/` | landed |
| 12 | Per-actor private notes | `notes/` | landed |
| 13 | Visit stamps + unread counts | `visits/` | landed |
| 14 | Per-actor TODO checklist | `todos/` | landed |

Endpoint surface for each module is summarised in the API table in [README.md](./README.md). The SPA at `apps/web/` consumes them via a typed `openapi-fetch` client generated from `/openapi.json`.

## Layout

```
Reckora/
├── src/reckora/        # engine — collectors, correlation, persistence, reports, CLI
└── apps/
    ├── api/reckora_api/   # FastAPI backend (Phase 2, landed)
    └── web/               # Vite + React + TS frontend (Layer 10, landed)
```

The backend is a thin FastAPI shell on top of the engine (`Orchestrator` + `SubjectRepository` + `reckora.reports`). The SPA is a Vite + React + TS app driven by a typed client generated from `/openapi.json` (`openapi-fetch` + `openapi-typescript`); CI typechecks and builds it on every change to `apps/web/**`.

## Strategic posture

We do not compete on site count. The edge is intelligence quality:

- entity resolution rather than enumeration
- explainable per-edge confidence with cited evidence
- evidence-bounded AI that cannot invent facts
- a graph-first dossier rather than a list of hits
