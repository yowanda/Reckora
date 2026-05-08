# Reckora Roadmap

The 10 layers of the Reckora vision and where each one lives in the codebase.

| # | Layer | Status | Notes |
|---|---|---|---|
| 1 | Input | partial | `IdentifierType` covers username, email, domain, url, phone, wallet, avatar. Active collectors: username (GitHub, Hacker News, Keybase), email (Gravatar), domain, url, phone, wallet (Bitcoin mainnet, Ethereum / EVM mainnet, Solana mainnet), avatar (HTTP image URLs). |
| 2 | Collection | partial | GitHub API, Hacker News API (`HackerNewsCollector`, public Firebase endpoint at `hacker-news.firebaseio.com`, no key), Keybase API (`KeybaseCollector`, public user-lookup endpoint at `keybase.io/_/api/1.0/user/lookup.json`, no key; surfaces a structured `proofs[]` array of currently-live cross-platform identity proofs — Twitter / GitHub / Reddit / DNS / domain / website — plus PGP key fingerprint), Gravatar API (`GravatarCollector`, public profile JSON endpoint at `www.gravatar.com/{md5_hash}.json`, no key; takes an `email` identifier, hashes it locally per Gravatar's trim-lowercase-MD5 rules so the plaintext never leaves the process, surfaces the linked-`accounts[]` array — Twitter / GitHub / LinkedIn / … — for cross-platform pivoting plus the canonical `profile_photo_url` the avatar perceptual-hash collector can re-ingest), WHOIS / RDAP, web profile (OG / `<title>`), offline phone normalisation (`PhoneCollector`, `phonenumbers`), opt-in HIBP breach lookup (`BreachCollector`, `--breach` / `breach: true`, gated by `HIBP_API_KEY`), Bitcoin chain stats (`BitcoinChainCollector`, public Blockstream Esplora, no key), Ethereum chain stats (`EthereumChainCollector`, public Etherscan API, optional `ETHERSCAN_API_KEY`), Solana chain stats (`SolanaChainCollector`, public Solana JSON-RPC, no key), avatar perceptual-hashing (`AvatarCollector`, fetches an HTTP image URL, computes dHash / pHash / aHash, emits the `avatar_phash` field consumed by the existing correlation rule). |
| 3 | Normalization | done | Each collector emits a uniform `Trace.fields` schema. Evidence is canonicalised (sorted keys, no whitespace, UTF-8) before hashing. |
| 4 | Correlation | done | Rules: `username_mutation`, `avatar_phash`, `timezone_overlap`, `bio_similarity`. Probabilistic-OR fusion. Embedding-based bio similarity via `sentence-transformers` (`reckora.correlation.embeddings.SentenceTransformerEmbedder`, optional `[embeddings]` extra; default `sentence-transformers/all-MiniLM-L6-v2`; the `BioEmbedder` Protocol lets callers swap in alternative providers; `correlate(traces, bio_embedder=...)` plumbs it through to the bio-similarity rule which falls back to the lexical baseline if the embedder declines a string) — landed. |
| 5 | AI Reasoning | done | Async OpenAI client with `summarize` and `hypothesize`. Prompt forbids invented facts and requires `ev:<8-hex>` citations. Rule-based anomaly detector (`reckora.anomaly.detect_anomalies`) — landed. ChatGPT OAuth (PKCE) login path (`reckora auth login`, `reckora.auth`) so the reasoning layer can run on a ChatGPT Plus / Pro subscription — landed. |
| 6 | Confidence Scoring | done | Per-edge `confidence: float [0, 1]`, `reasons[]`, `supporting_evidence[]` (payload SHAs). |
| 7 | Evidence Chain | done | Source URL + timestamp + SHA-256 of canonicalised payload, optional Wayback Machine `archive_url` per trace (`reckora investigate --archive`), and optional forensic PNG `screenshot_path` per evidence URL (`reckora investigate --screenshot`, `[screenshots]` extra). Cross-trace Merkle root over per-trace evidence SHA-256 leaves (`reckora.evidence.merkle`, sorted leaves, even-duplication, all-SHA-256 nodes — interchangeable with the Bitcoin-style scheme `ots` uses) plus opt-in OpenTimestamps Calendar HTTP submission to the public fleet (`a.pool.opentimestamps.org`, `b.pool.opentimestamps.org`, `alice.btc.calendar.opentimestamps.org`, no key) for cross-trace tamper-evidence; persisted in SQLite + Neo4j and rendered into every dossier format (`reckora investigate --anchor`, API `anchor: true`, `reckora verify-anchor <subject-id>`) — landed. |
| 8 | Graph | done (in-process) | NetworkX `MultiDiGraph[str]` for live correlation; optional `Neo4jSubjectRepository` (`reckora.persistence.Neo4jSubjectRepository`, `[neo4j]` extra) for durable cross-subject identifier sharing. |
| 9 | Reporting | partial | JSON + Markdown + self-contained HTML dossier + PDF dossier (`reckora investigate --format pdf`, `--output dossier.pdf`, API `?format=pdf`), persisted to SQLite via `reckora list` / `reckora show`. Chronological timeline reconstruction (`reckora.reports.build_timeline`, `## Timeline` section in every dossier renderer + `timeline[]` in JSON / API payload) — landed. |
| 10 | Web UI | partial | FastAPI backend with JWT auth at `/api/v1/*` (`apps/api/reckora_api`, `reckora-api serve`), two-tier RBAC (`admin` / `viewer`) with per-dossier ownership and explicit read-only sharing (`apps/api/reckora_api/access/`, `subject_owners` + `subject_shares` tables, `POST /api/v1/subjects/{id}/share`, `GET /api/v1/users`, `PATCH /api/v1/users/{id}/role`) — landed. Dashboard / graph viewer / report viewer (`apps/web/`, Vite + React + TS, stack confirmed when work starts) — pending user instruction. |

## Phase plan

- **Phase 1 — MVP skeleton**: entity-first data model, evidence chain, three collectors, rule-based correlation engine, evidence-bounded AI reasoning, CLI dossier, CI matrix on Python 3.11 + 3.12.
- **Phase 2 — Persistence & UI**: SQLite storage behind a repository seam (`reckora.persistence.SubjectRepository`, `reckora investigate --save`, `reckora list / show / delete`) — landed; self-contained HTML dossier (`--format html`, `.html` output) — landed; archive.org / Wayback snapshot per evidence URL (`reckora investigate --archive`, `Evidence.archive_url`) — landed; **FastAPI backend with JWT auth** (`apps/api/reckora_api`, `reckora-api serve`) — landed; **PDF dossier** (`reckora investigate --format pdf`, `--output dossier.pdf`, `GET /api/v1/subjects/{id}/dossier?format=pdf`) — landed; **forensic screenshot capture** (`reckora investigate --screenshot --screenshots-dir DIR`, `Evidence.screenshot_path`, API `screenshot: true` + `/screenshots/*` static mount, optional `[screenshots]` extra → Playwright headless Chromium) — landed; **optional Neo4j adapter** (`reckora.persistence.Neo4jSubjectRepository`, optional `[neo4j]` extra, shared `Identifier` nodes across subjects for cross-dossier graph queries) — landed; **web frontend** (`apps/web/`, Vite + React + TS, graph viewer) — pending user instruction.
- **Phase 4 — Autonomous agents**: **AgentLoop** (`reckora.agent.AgentLoop` + `reckora.agent.Verifier`) — hypothesis-driven recursive identifier expansion gated by two rule-based gates: a `Verifier` that rejects malformed / unknown-kind / un-evidenced / unsupported AI-proposed identifiers before any collector runs, and a confidence-floor gate that drops verified candidates the rule-based correlation engine can't link back to the existing graph; full per-iteration transcript exposed on `AgentLoopResult.transcript`; pluggable into any orchestrator + reasoning client — landed. **ChatGPT OAuth (PKCE) login** (`reckora auth login` / `status` / `logout` / `refresh`; `reckora.auth` package — PKCE generator, local callback server on `127.0.0.1:1455`, OpenAI Codex CLI's public `client_id` and whitelisted redirect URI, on-disk credentials at `~/.config/reckora/auth.json` with `0600` mode and `XDG_CONFIG_HOME` honoured, eager + 401-driven refresh, atomic writes; `ReasoningClient` dispatches between API-key and OAuth modes lazily — API key still wins when both are present so existing deploys are unaffected; OAuth mode talks to `chatgpt.com/backend-api/codex/responses` via SSE streaming so usage counts against the user's ChatGPT subscription instead of a Platform billing tier; CLI surfaces a clear `--ai` pre-flight error when neither auth path is configured) — landed.
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
