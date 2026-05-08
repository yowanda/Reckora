# Reckora Roadmap

The 10 layers of the Reckora vision and where each one lives in the codebase.

| # | Layer | Status | Notes |
|---|---|---|---|
| 1 | Input | partial | `IdentifierType` covers username, email, domain, url, phone, wallet, avatar. Active collectors: username, domain, url, phone, wallet (Bitcoin mainnet). Avatar input + non-BTC wallet networks — Phase 3. |
| 2 | Collection | partial | GitHub API, WHOIS / RDAP, web profile (OG / `<title>`), offline phone normalisation (`PhoneCollector`, `phonenumbers`), opt-in HIBP breach lookup (`BreachCollector`, `--breach` / `breach: true`, gated by `HIBP_API_KEY`), Bitcoin chain stats (`BitcoinChainCollector`, public Blockstream Esplora, no key). Ethereum / EVM (Etherscan) and other non-BTC chains — Phase 3. |
| 3 | Normalization | done | Each collector emits a uniform `Trace.fields` schema. Evidence is canonicalised (sorted keys, no whitespace, UTF-8) before hashing. |
| 4 | Correlation | done (rule-based) | Rules: `username_mutation`, `avatar_phash`, `timezone_overlap`, `bio_similarity`. Probabilistic-OR fusion. Embedding-based bio similarity (`sentence-transformers`) — Phase 3. |
| 5 | AI Reasoning | done | Async OpenAI client with `summarize` and `hypothesize`. Prompt forbids invented facts and requires `ev:<8-hex>` citations. Rule-based anomaly detector (`reckora.anomaly.detect_anomalies`) — landed. |
| 6 | Confidence Scoring | done | Per-edge `confidence: float [0, 1]`, `reasons[]`, `supporting_evidence[]` (payload SHAs). |
| 7 | Evidence Chain | partial | Source URL + timestamp + SHA-256 of canonicalised payload, optional Wayback Machine `archive_url` per trace (`reckora investigate --archive`), and optional forensic PNG `screenshot_path` per evidence URL (`reckora investigate --screenshot`, `[screenshots]` extra). Cross-trace Merkle / blockchain timestamping — Phase 3+. |
| 8 | Graph | done (in-process) | NetworkX `MultiDiGraph[str]` for live correlation; optional `Neo4jSubjectRepository` (`reckora.persistence.Neo4jSubjectRepository`, `[neo4j]` extra) for durable cross-subject identifier sharing. |
| 9 | Reporting | partial | JSON + Markdown + self-contained HTML dossier + PDF dossier (`reckora investigate --format pdf`, `--output dossier.pdf`, API `?format=pdf`), persisted to SQLite via `reckora list` / `reckora show`. Chronological timeline reconstruction (`reckora.reports.build_timeline`, `## Timeline` section in every dossier renderer + `timeline[]` in JSON / API payload) — landed. |
| 10 | Web UI | partial | FastAPI backend with JWT auth at `/api/v1/*` (`apps/api/reckora_api`, `reckora-api serve`) — landed. Dashboard / graph viewer / report viewer (`apps/web/`, Vite + React + TS, stack confirmed when work starts) — pending user instruction. |

## Phase plan

- **Phase 1 — MVP skeleton**: entity-first data model, evidence chain, three collectors, rule-based correlation engine, evidence-bounded AI reasoning, CLI dossier, CI matrix on Python 3.11 + 3.12.
- **Phase 2 — Persistence & UI**: SQLite storage behind a repository seam (`reckora.persistence.SubjectRepository`, `reckora investigate --save`, `reckora list / show / delete`) — landed; self-contained HTML dossier (`--format html`, `.html` output) — landed; archive.org / Wayback snapshot per evidence URL (`reckora investigate --archive`, `Evidence.archive_url`) — landed; **FastAPI backend with JWT auth** (`apps/api/reckora_api`, `reckora-api serve`) — landed; **PDF dossier** (`reckora investigate --format pdf`, `--output dossier.pdf`, `GET /api/v1/subjects/{id}/dossier?format=pdf`) — landed; **forensic screenshot capture** (`reckora investigate --screenshot --screenshots-dir DIR`, `Evidence.screenshot_path`, API `screenshot: true` + `/screenshots/*` static mount, optional `[screenshots]` extra → Playwright headless Chromium) — landed; **optional Neo4j adapter** (`reckora.persistence.Neo4jSubjectRepository`, optional `[neo4j]` extra, shared `Identifier` nodes across subjects for cross-dossier graph queries) — landed; **web frontend** (`apps/web/`, Vite + React + TS, graph viewer) — pending user instruction.
- **Phase 3 — Sensor expansion**: offline phone collector (`PhoneCollector` via `phonenumbers`, `--kind phone`) — landed; **timeline reconstruction in dossier** (`reckora.reports.build_timeline`; chronological `## Timeline` section in every renderer; `timeline[]` exposed in JSON export and `GET /api/v1/subjects/{id}` payload) — landed; **HIBP breach lookup behind a feature flag** (`reckora.collectors.breach.BreachCollector`, opt-in via CLI `--breach` / API `breach: true`, gated by `HIBP_API_KEY`; emits a `breach_hibp` Trace with normalised summary + per-breach metadata, raw HIBP payload dropped from evidence) — landed; **rule-based anomaly detector** (`reckora.anomaly.detect_anomalies`; future-evidence, temporal inconsistency, expired-domain, invalid-phone and display-name divergence rules; `## Anomalies` section in every dossier renderer; `anomalies[]` in JSON / API payload) — landed; **Bitcoin wallet collector** (`reckora.collectors.wallet_btc.BitcoinChainCollector`, public Blockstream Esplora — no API key; emits a `wallet_blockstream` Trace with normalised stats: `address_format` (`p2pkh` / `p2sh` / `bech32` / `bech32m`), `confirmed_tx_count` / `mempool_tx_count`, `total_received_satoshi` / `total_spent_satoshi`, `current_balance_satoshi` / `current_balance_btc` string, `is_active`; raw envelope dropped from evidence) — landed; Ethereum / EVM wallet collector (Etherscan) — pending; `sentence-transformers` bio embeddings — pending.
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
