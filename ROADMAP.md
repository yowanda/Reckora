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
| 7 | Evidence Chain | partial | Source URL + timestamp + SHA-256 of canonicalised payload, plus optional Wayback Machine `archive_url` per trace via `reckora investigate --archive`. Screenshot capture — Phase 2. |
| 8 | Graph | done (in-process) | NetworkX `MultiDiGraph[str]`. Neo4j adapter — Phase 2. |
| 9 | Reporting | partial | JSON + Markdown + self-contained HTML dossier + PDF dossier (`reckora investigate --format pdf`, `--output dossier.pdf`, API `?format=pdf`), persisted to SQLite via `reckora list` / `reckora show`. Timeline reconstruction — Phase 3. |
| 10 | Web UI | partial | FastAPI backend with JWT auth at `/api/v1/*` (`apps/api/reckora_api`, `reckora-api serve`) — landed. Dashboard / graph viewer / report viewer (`apps/web/`, Vite + React + TS, stack confirmed when work starts) — pending user instruction. |

## Phase plan

- **Phase 1 — MVP skeleton**: entity-first data model, evidence chain, three collectors, rule-based correlation engine, evidence-bounded AI reasoning, CLI dossier, CI matrix on Python 3.11 + 3.12.
- **Phase 2 — Persistence & UI**: SQLite storage behind a repository seam (`reckora.persistence.SubjectRepository`, `reckora investigate --save`, `reckora list / show / delete`) — landed; self-contained HTML dossier (`--format html`, `.html` output) — landed; archive.org / Wayback snapshot per evidence URL (`reckora investigate --archive`, `Evidence.archive_url`) — landed; **FastAPI backend with JWT auth** (`apps/api/reckora_api`, `reckora-api serve`) — landed; **PDF dossier** (`reckora investigate --format pdf`, `--output dossier.pdf`, `GET /api/v1/subjects/{id}/dossier?format=pdf`) — landed; forensic screenshot capture — pending; optional Neo4j adapter — pending; **web frontend** (`apps/web/`, Vite + React + TS, graph viewer) — pending user instruction.
- **Phase 3 — Sensor expansion**: phone collector, crypto wallet collector (Etherscan / Blockstream), `sentence-transformers` bio embeddings, anomaly detector, breach lookup behind a feature flag.
- **Phase 4 — Autonomous agents**: hypothesis-driven recursive identifier expansion gated by confidence floors, AI-proposed collector plans verified by rule-based engines.
- **Phase 5 — Collaborative platform**: multi-user investigations, shared evidence library, role-based reporting.

## Frontend / backend split

Layout (the source of truth — agreed with the user):

```
Reckora/
├── src/reckora/        # engine — collectors, correlation, persistence, reports, CLI
└── apps/
    ├── api/reckora_api/   # FastAPI backend (Phase 2, landed)
    └── web/               # Vite + React + TS frontend (Phase 2, pending user instruction)
```

Backend is a thin FastAPI shell on top of the engine (`Orchestrator` +
`SubjectRepository` + `reckora.reports`) — same wheel, two top-level
packages (`reckora`, `reckora_api`). Run it with `reckora-api serve`; OpenAPI
is at `/openapi.json` and Swagger UI at `/docs`. Auth model is JWT bearer
(login user) issued by `POST /api/v1/auth/token`.

The frontend will live at `apps/web/` as a Vite + React + TypeScript SPA
when the user gives the go-ahead, and will consume the published OpenAPI
schema for fully typed client generation. Stack is locked in here so it is
not re-litigated when frontend work begins.

## Strategic posture

We deliberately do **not** compete on site count. Reckora's edge is intelligence quality:

- entity resolution rather than enumeration
- explainable per-edge confidence with cited evidence
- evidence-bounded AI that cannot invent facts
- a graph-first dossier rather than a list of hits
