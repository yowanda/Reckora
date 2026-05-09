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
# Run an investigation; print the dossier (markdown by default).
reckora investigate octocat --kind username
reckora investigate example.com --kind domain --ai

# Pick the format / write to file (extension is enough — md / json / html / pdf).
reckora investigate octocat --kind username --output dossier.html
reckora investigate octocat --kind username --format pdf --output dossier.pdf

# Persist to the SQLite store and reopen.
reckora investigate octocat --kind username --save
reckora list
reckora show subj-...

# Mint a Wayback snapshot per evidence URL.
reckora investigate octocat --kind username --archive

# Capture forensic full-page PNGs (needs the screenshots extra).
reckora investigate octocat --kind username --screenshot \
    --screenshots-dir ./screenshots

# Anchor evidence into a Merkle root + OpenTimestamps stamp.
reckora investigate octocat --kind username --anchor --save
reckora verify-anchor subj-...

# AI reasoning: either set OPENAI_API_KEY, or log in with ChatGPT.
reckora auth login    # OAuth + PKCE; runs on your ChatGPT subscription
reckora auth status
reckora auth refresh
reckora auth logout

# Opt-in HIBP breach lookup (requires HIBP_API_KEY).
reckora investigate alice@example.com --kind email --breach
```

Supported `--kind` values: `username`, `email`, `domain`, `url`, `phone`, `wallet` (BTC / ETH / SOL), `avatar` (image URL).

Active collectors (default orchestrator): GitHub, Hacker News, Keybase, Gravatar, WHOIS / RDAP, web profile, phone (offline `phonenumbers`), wallet (Blockstream Esplora / Etherscan / Solana mainnet-beta JSON-RPC), avatar perceptual hash, opt-in HIBP breach.

Every dossier — markdown / HTML / JSON / PDF — carries `## Timeline`, `## Anomalies`, an optional `## Cross-trace anchor`, and clickable evidence + Wayback / screenshot links. The same projection is exposed at `GET /api/v1/subjects/{id}` so the frontend can reuse it without re-deriving.

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
| `RECKORA_OPENAI_OAUTH_MODEL` | `gpt-5.1-codex-mini` | model used over ChatGPT OAuth |

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
