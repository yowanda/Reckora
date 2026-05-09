# Reckora

AI-native OSINT investigation system. Entity resolution, evidence-graph reasoning, explainable intelligence.

## Install

```bash
uv sync --extra dev
```

Optional extras (each is opt-in):

- `--extra screenshots` — Playwright + Chromium for forensic PNG capture.
- `--extra neo4j` — Neo4j adapter for cross-subject graph queries.
- `--extra embeddings` — `sentence-transformers` for semantic bio similarity.

## CLI

```bash
# --kind is auto-detected from the value when omitted.
reckora investigate octocat                                    # → username
reckora investigate alice@example.com                          # → email
reckora investigate https://github.com/octocat                 # → url
reckora investigate 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEAd # → wallet (ETH)
reckora investigate +628123456789                              # → phone
reckora investigate example.com --ai                           # → domain

# Pass --kind explicitly to override auto-detection.
reckora investigate user.name --kind username   # otherwise treated as a domain

# Pick the format / write to file (extension is enough — md / json / ndjson / html / pdf).
reckora investigate octocat --output dossier.html
reckora investigate octocat --format pdf --output dossier.pdf

# NDJSON: one JSON object per line, one line per logical record. Streams
# cleanly into jq / log shippers / dataframes:
reckora investigate octocat --format ndjson | jq -c 'select(.record=="edge") | .edge'

# Persist to the SQLite store and reopen.
reckora investigate octocat --save
reckora list
reckora show subj-...

# Mint a Wayback snapshot per evidence URL.
reckora investigate octocat --archive

# Capture forensic full-page PNGs (needs the screenshots extra).
reckora investigate octocat --screenshot --screenshots-dir ./screenshots

# Anchor evidence into a Merkle root + OpenTimestamps stamp.
reckora investigate octocat --anchor --save
reckora verify-anchor subj-...

# AI reasoning: either set OPENAI_API_KEY, or log in with ChatGPT.
reckora auth login    # OAuth + PKCE; runs on your ChatGPT subscription
reckora auth status
reckora auth refresh
reckora auth logout

# Opt-in HIBP breach lookup (requires HIBP_API_KEY).
reckora investigate alice@example.com --breach
```

Supported `--kind` values: `username`, `email`, `domain`, `url`, `phone`, `wallet` (BTC / ETH / SOL), `avatar` (image URL). Auto-detection covers all of them; pass `--kind` only when the heuristic guesses wrong (e.g. a dotted handle that looks like a domain).

Active collectors (default orchestrator): GitHub, Hacker News, Keybase, Gravatar, Reddit, X / Twitter (via the public `syndication.twitter.com` profile widget), TikTok (via the public `__UNIVERSAL_DATA_FOR_REHYDRATION__` blob), social presence probe (best-effort URL probes for Instagram / Threads / LinkedIn / Facebook — auth-walled platforms where presence can only be partially verified without a logged-in session), WHOIS / RDAP, DNS records (NS / MX / TXT / SPF / DMARC / DNSSEC via `dnspython`), web profile, phone (offline `phonenumbers`), email, wallet (Blockstream Esplora / Etherscan / Solana mainnet-beta JSON-RPC), avatar perceptual hash, opt-in HIBP breach.

Every dossier — markdown / HTML / JSON / NDJSON / PDF — carries `## Timeline`, `## Anomalies`, an optional `## Cross-trace anchor`, and clickable evidence + Wayback / screenshot links. The same projection is exposed at `GET /api/v1/subjects/{id}` so the frontend can reuse it without re-deriving. NDJSON envelopes each record under a top-level `record` discriminator so a downstream `jq -c 'select(.record=="trace") | .trace'` projects records as standalone documents without re-parsing the whole dossier.

## HTTP API

The FastAPI backend (`apps/api/reckora_api`) wraps the same engine. JWT bearer auth; OpenAPI at `/openapi.json`, Swagger at `/docs`, health at `/healthz`.

```bash
export RECKORA_API_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
reckora-api create-user root  --password 'supersecret123'             # admin
reckora-api create-user alice --password 'alicepassword1' --viewer    # viewer
reckora-api serve --host 127.0.0.1 --port 8000
```

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
  -H 'content-type: application/x-www-form-urlencoded' \
  -d 'username=alice&password=alicepassword1' | jq -r .access_token)

curl -s -X POST http://127.0.0.1:8000/api/v1/investigations \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"seed": {"kind": "username", "value": "octocat"}}'

curl -s -H "authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/subjects/<subject-id>/dossier?format=pdf" \
  -o dossier.pdf
```

### Endpoints

All under `/api/v1`; all require `Authorization: Bearer <token>` except `/auth/register` and `/auth/token`.

| Group | Endpoints |
|---|---|
| Auth & users | `POST /auth/register`, `POST /auth/token`, `GET /auth/me`, `GET /users`, `PATCH /users/{id}/role` |
| Investigations | `POST /investigations` (flags: `archive`, `screenshot`, `ai`, `breach`, `anchor`) |
| Dossiers | `GET /subjects` · `GET /subjects/{id}` · `GET /subjects/{id}/dossier?format=html\|json\|md\|pdf` · `DELETE /subjects/{id}` |
| Sharing | `POST /subjects/{id}/share`, `DELETE /subjects/{id}/share/{user_id}` |
| Cross-references | `GET /subjects/{id}/cross-references` |
| Activity feed | `GET /subjects/{id}/activity` |
| Comments + threading | `GET/POST /subjects/{id}/comments` · `PATCH/DELETE /subjects/{id}/comments/{cid}` · `GET /subjects/{id}/comments/{cid}/replies` |
| Reactions | `GET /subjects/{id}/comments/{cid}/reactions` · `PUT/DELETE /subjects/{id}/comments/{cid}/reactions/{key}` |
| Mentions feed | `GET /me/mentions` |
| Assignees | `GET/POST /subjects/{id}/assignees` · `DELETE /subjects/{id}/assignees/{uid}` |
| Labels | `GET /subjects/{id}/labels` · `PUT/DELETE /subjects/{id}/labels/{label}` · `GET /labels` |
| Status | `GET/PUT /subjects/{id}/status` · `GET /status` |
| Watchers / following | `GET /subjects/{id}/watchers` · `PUT/DELETE /subjects/{id}/watchers/me` · `GET /me/watching` |
| Pinned dossiers | `GET/POST /me/pins` · `DELETE /me/pins/{subject_id}` |
| Private notes | `GET/PUT/DELETE /subjects/{id}/notes/me` |
| Visits + unread | `POST/GET /subjects/{id}/visits/me` · `GET /subjects/{id}/unread` |
| TODO checklist | `GET/POST /subjects/{id}/todos/me` · `PATCH/DELETE /subjects/{id}/todos/me/{todo_id}` |

### Configuration (env vars)

| Variable | Default | Notes |
|---|---|---|
| `RECKORA_API_JWT_SECRET` | _(required)_ | HMAC signing key (≥ 32 bytes recommended) |
| `RECKORA_API_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `RECKORA_API_JWT_TTL_SECONDS` | `3600` | access token lifetime |
| `RECKORA_API_CORS_ORIGINS` | `http://localhost:5173` | comma-separated allow-list |
| `RECKORA_API_DOCS_ENABLED` | `true` | toggle `/docs` and `/openapi.json` |
| `RECKORA_DB_PATH` | `./reckora.db` | shared SQLite file (CLI + API) |
| `RECKORA_API_SCREENSHOTS_DIR` | `screenshots` | dir for captured PNGs |
| `RECKORA_API_SCREENSHOTS_URL_PREFIX` | `/screenshots` | URL prefix for served PNGs |
| `HIBP_API_KEY` | _(unset)_ | enables `--breach` / `breach: true` |
| `ETHERSCAN_API_KEY` | _(unset)_ | lifts the anonymous tier rate limit |
| `RECKORA_OPENAI_OAUTH_MODEL` | `gpt-5.5` | model used over ChatGPT OAuth |

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
