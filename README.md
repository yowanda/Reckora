# Reckora

AI-native OSINT investigation system. Entity resolution, evidence-graph reasoning,
explainable intelligence.

## Install

```bash
uv sync --extra dev
```

## Usage

Run an investigation and print the dossier:

```bash
reckora investigate octocat --kind username
reckora investigate example.com --kind domain --ai
```

Pick the dossier format with `--format md|json|html`, or write straight to a
file (the format is inferred from the extension `.md` / `.json` / `.html`):

```bash
reckora investigate https://github.com/octocat --kind url --output dossier.md
reckora investigate octocat --kind username --output dossier.html
reckora investigate octocat --kind username --format html > dossier.html
```

The HTML dossier is fully self-contained (inline CSS, no external assets) so it
opens straight from disk and supports light / dark mode.

Persist a dossier to the SQLite store and reopen it later:

```bash
reckora investigate octocat --kind username --save
reckora list
reckora show subj-...      # md (default), --format json|html supported
reckora delete subj-...
```

The store lives at `./reckora.db` by default; override with `--db PATH` or the
`RECKORA_DB_PATH` environment variable.

Mint a Wayback Machine snapshot for every evidence URL so the chain stays
auditable even if the live page disappears (best-effort; off by default
because each save round-trips to web.archive.org):

```bash
reckora investigate octocat --kind username --archive
```

Each `Trace.evidence.archive_url` then points at the durable snapshot, and
the dossier renderers (markdown, JSON, HTML) include it next to the live
source URL.

Set `OPENAI_API_KEY` to enable `--ai` (LLM-generated summary + hypotheses,
evidence-bounded with `ev:<8-hex>` citations).

## HTTP API

Reckora ships a FastAPI backend (`apps/api/reckora_api`) that wraps the same
engine the CLI uses. The web frontend (Vite + React + TS, see
[ROADMAP](./ROADMAP.md)) consumes this API as its sole backend.

Generate a JWT secret and bootstrap the first user:

```bash
export RECKORA_API_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
reckora-api create-user alice --password 'supersecret123'
reckora-api serve --host 127.0.0.1 --port 8000
```

OpenAPI is at <http://127.0.0.1:8000/openapi.json>; Swagger UI at
<http://127.0.0.1:8000/docs>; health probe at `/healthz`.

Authenticate and run an investigation end-to-end with `curl`:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
  -H 'content-type: application/x-www-form-urlencoded' \
  -d 'username=alice&password=supersecret123' | jq -r .access_token)

curl -s -X POST http://127.0.0.1:8000/api/v1/investigations \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"seed": {"kind": "username", "value": "octocat"}}'

curl -s -H "authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/subjects
```

Endpoints (all under `/api/v1`, all require `Authorization: Bearer <token>`
except `/auth/register` and `/auth/token`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | create a user (username + password ≥ 8 chars) |
| POST | `/auth/token` | OAuth2-form login → JWT access token |
| GET  | `/auth/me` | current user identity |
| POST | `/investigations` | run orchestrator + persist (`archive`, `ai` flags) |
| GET  | `/subjects` | list saved dossiers (`?limit=`) |
| GET  | `/subjects/{id}` | full saved dossier as JSON |
| GET  | `/subjects/{id}/dossier?format=html\|json\|md` | render dossier |
| DELETE | `/subjects/{id}` | drop a saved dossier |

Configuration (env vars, all optional except the secret):

| Variable | Default | Notes |
|---|---|---|
| `RECKORA_API_JWT_SECRET` | _(required)_ | HMAC signing key (≥ 32 bytes recommended) |
| `RECKORA_API_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `RECKORA_API_JWT_TTL_SECONDS` | `3600` | access token lifetime |
| `RECKORA_API_CORS_ORIGINS` | `http://localhost:5173` | comma-separated allow-list |
| `RECKORA_API_DOCS_ENABLED` | `true` | toggle `/docs` and `/openapi.json` |
| `RECKORA_DB_PATH` | `./reckora.db` | shared SQLite file (CLI + API) |

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
