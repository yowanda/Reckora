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

Pick the dossier format with `--format md|json|html|pdf`, or write straight to
a file (the format is inferred from the extension `.md` / `.json` / `.html` /
`.pdf`):

```bash
reckora investigate https://github.com/octocat --kind url --output dossier.md
reckora investigate octocat --kind username --output dossier.html
reckora investigate octocat --kind username --output dossier.pdf
reckora investigate octocat --kind username --format html > dossier.html
reckora investigate octocat --kind username --format pdf  > dossier.pdf
```

The HTML dossier is fully self-contained (inline CSS, no external assets) so it
opens straight from disk and supports light / dark mode. The PDF dossier is
generated with reportlab (pure Python, no system libs) and mirrors the same
structure: header → identifiers → traces → **anomalies** → correlation edges →
optional AI summary / hypotheses, with clickable source / archive links.

Every dossier (markdown / HTML / PDF) carries an `## Anomalies` section
populated by a rule-based detector
(`reckora.anomaly.detect_anomalies(traces)`). It currently surfaces
future-dated evidence, internal timestamp inconsistencies (`created_at`
postdating `updated_at` or `Evidence.fetched_at`), expired domains
(WHOIS / RDAP `expires_at` < observation), invalid phone numbers
(`is_valid=False`), and display-name divergence across collectors.
Findings are sorted high → low severity and cite the supporting payload
SHAs so every claim stays auditable. JSON export and
`GET /api/v1/subjects/{id}` surface the same data as a top-level
`anomalies[]` array.

Persist a dossier to the SQLite store and reopen it later:

```bash
reckora investigate octocat --kind username --save
reckora list
reckora show subj-...                                # md (default)
reckora show subj-... --format pdf -o dossier.pdf    # md|json|html|pdf
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
the dossier renderers (markdown, JSON, HTML, PDF) include it next to the
live source URL.

Capture a forensic full-page PNG of every evidence URL via headless
Chromium so the dossier can be reviewed offline (best-effort; off by
default because it pulls a browser binary):

```bash
uv sync --extra screenshots
uv run playwright install chromium
reckora investigate octocat --kind username --screenshot \
    --screenshots-dir ./screenshots
```

Each `Trace.evidence.screenshot_path` then points at the captured PNG, and
all four dossier renderers (markdown, JSON, HTML, PDF) include the path /
link next to the live source URL.

Set `OPENAI_API_KEY` to enable `--ai` (LLM-generated summary + hypotheses,
evidence-bounded with `ev:<8-hex>` citations).

## Breach lookup (Have I Been Pwned)

Reckora ships an opt-in `BreachCollector` that resolves an `email`
identifier against the [Have I Been Pwned v3 API](https://haveibeenpwned.com/API/v3).
It is **off by default** (no network calls, no PII leaving the host)
and only fires when you both pass `--breach` AND set `HIBP_API_KEY` —
either of those missing degrades silently to an empty trace list so
investigations stay deterministic on hosts without a key.

```bash
export HIBP_API_KEY="hibp_..."
reckora investigate alice@example.com --kind email --breach
```

The emitted trace normalises to a flat schema the dossier renderers can
read without parsing nested arrays at render time:
`email`, `breach_count`, `first_breach_date`, `latest_breach_date`,
`data_classes` (sorted union across all breaches), `has_sensitive_breach`,
and `breaches[]` (per-breach summary with `name`, `domain`, `breach_date`,
`pwn_count`, `data_classes`, `is_verified`, `is_sensitive`, ...).

The raw HIBP response is **never** inlined into the evidence row
(`keep_raw=False`) — only the SHA-256 of the canonicalised payload is
preserved, so the chain stays auditable without spilling per-breach PII
into the saved dossier. The HTTP API exposes the same toggle via the
`breach: true` field on `POST /api/v1/investigations`.

## Phone identifiers

Phone numbers are first-class identifiers. The bundled `PhoneCollector` is
fully offline — it relies on `phonenumbers` (libphonenumber's Python port,
which ships its own metadata database) so investigations stay deterministic
and don't leak the number to any third party.

```bash
# International form (any locale):
reckora investigate "+12025550123" --kind phone

# National form needs a default region; pass it via the orchestrator if
# you script Reckora as a library (the CLI defaults to "US").
```

The emitted trace normalises to: `e164`, `country_code`, `country_iso`,
`country_name`, `region`, `carrier_name`, `line_type`
(`mobile` / `fixed_line` / `voip` / `toll_free` / ...), `is_valid`,
`is_possible`. Numbers that fail to parse never abort the investigation —
the collector returns no traces and the orchestrator logs the miss.

## Optional Neo4j backend

`SQLiteSubjectRepository` is the default store. For environments that want
graph-native cross-subject queries (e.g. "every dossier that ever touched
this email"), Reckora ships an optional Neo4j adapter behind the same
`SubjectRepository` seam:

```bash
uv sync --extra neo4j
```

```python
from neo4j import GraphDatabase
from reckora.persistence import Neo4jSubjectRepository

driver = GraphDatabase.driver(
    "bolt://localhost:7687",
    auth=("neo4j", "secret"),
)
repo = Neo4jSubjectRepository(driver, database="reckora")
# same surface as SQLiteSubjectRepository:
#   repo.save(subject=..., traces=..., edges=...)
#   repo.get(subject_id)
#   repo.list_recent(limit=20)
#   repo.delete(subject_id)
```

The graph maps `(:Subject)-[:HAS_IDENTIFIER]->(:Identifier)` with
`Identifier` nodes shared across subjects, so a follow-up Cypher query like
`MATCH (i:Identifier {value: 'bob@example.com'})<-[:HAS_IDENTIFIER]-(s:Subject)
RETURN s` lists every saved investigation that touched that identifier —
something the relational backend cannot express. Trace and edge JSON is
stored verbatim on subject-owned `(:TraceNode)` / `(:EdgeNode)` children so
the round-trip preserves bit-for-bit Pydantic fidelity.

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

# Download a PDF dossier for a saved subject:
curl -s -H "authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/subjects/<subject-id>/dossier?format=pdf" \
  -o dossier.pdf
```

Endpoints (all under `/api/v1`, all require `Authorization: Bearer <token>`
except `/auth/register` and `/auth/token`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | create a user (username + password ≥ 8 chars) |
| POST | `/auth/token` | OAuth2-form login → JWT access token |
| GET  | `/auth/me` | current user identity |
| POST | `/investigations` | run orchestrator + persist (`archive`, `screenshot`, `ai`, `breach` flags) |
| GET  | `/subjects` | list saved dossiers (`?limit=`) |
| GET  | `/subjects/{id}` | full saved dossier as JSON |
| GET  | `/subjects/{id}/dossier?format=html\|json\|md\|pdf` | render dossier |
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
| `RECKORA_API_SCREENSHOTS_DIR` | `screenshots` | filesystem dir for captured PNGs |
| `RECKORA_API_SCREENSHOTS_URL_PREFIX` | `/screenshots` | URL prefix at which the API serves PNGs |
| `HIBP_API_KEY` | _(unset)_ | Have I Been Pwned API key (enables `--breach` / `breach: true`) |

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
