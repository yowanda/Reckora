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
structure: header → identifiers → traces → **timeline** → **anomalies** →
correlation edges → optional AI summary / hypotheses, with clickable source /
archive links.

Every dossier (markdown / HTML / PDF) carries a chronological `## Timeline`
section reconstructed from `Evidence.fetched_at`, sorted ascending with ties
broken by `payload_sha256` so the order is deterministic. The JSON export
and `GET /api/v1/subjects/{id}` surface the same data as a top-level
`timeline[]` array so the frontend can reuse the projection without
re-deriving it.

Every dossier also carries an `## Anomalies` section populated by a
rule-based detector (`reckora.anomaly.detect_anomalies(traces)`). It
currently surfaces future-dated evidence, internal timestamp
inconsistencies (`created_at` postdating `updated_at` or
`Evidence.fetched_at`), expired domains (WHOIS / RDAP `expires_at` <
observation), invalid phone numbers (`is_valid=False`), and display-name
divergence across collectors. Findings are sorted high → low severity
and cite the supporting payload SHAs so every claim stays auditable.
JSON export and `GET /api/v1/subjects/{id}` surface the same data as a
top-level `anomalies[]` array.

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

## Bitcoin wallet identifiers

Reckora ships a `BitcoinChainCollector` that resolves a `wallet` identifier
against the public [Blockstream Esplora API](https://github.com/Blockstream/esplora/blob/master/API.md)
— no API key, no registration, just an HTTP gateway in front of an
Esplora-indexed Bitcoin node. The collector is wired into the default
orchestrator (CLI and HTTP API), so any seed of `--kind wallet` that
parses as a Bitcoin mainnet address triggers it automatically.

```bash
# Legacy (P2PKH)
reckora investigate "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" --kind wallet

# SegWit (P2WPKH / P2WSH)
reckora investigate "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" --kind wallet

# Taproot (P2TR)
reckora investigate "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr" --kind wallet
```

The emitted trace normalises to a flat schema the dossier renderers can
read without parsing the raw envelope at render time:
`address`, `chain` (`"bitcoin"`), `network` (`"mainnet"`), `address_format`
(`p2pkh` / `p2sh` / `bech32` / `bech32m`), `confirmed_tx_count`,
`mempool_tx_count`, `tx_count`, `total_received_satoshi`,
`total_spent_satoshi`, `current_balance_satoshi`, `current_balance_btc`
(string-formatted, no float drift), `mempool_balance_satoshi`,
`is_active`. A 404 from Blockstream — i.e. the address has never been
seen on chain — still emits a Trace with `is_active=False`, because the
absence of activity is itself an intelligence finding rather than a
collection failure.

The collector silently no-ops on `wallet` strings that are not Bitcoin
mainnet addresses (e.g. an Ethereum hex address) so future wallet
adapters that also support `IdentifierType.WALLET` can coexist in the
orchestrator.

The raw HTTP envelope is **never** inlined into the evidence row
(`keep_raw=False`) — only the SHA-256 of the canonicalised payload is
preserved, so the chain stays auditable without bloating the saved
dossier with on-chain detail. The HTTP API enables the same collector
automatically — any `POST /api/v1/investigations` with a `wallet`-kind
seed routes through it.

## Ethereum wallet identifiers

Alongside Bitcoin, Reckora ships an `EthereumChainCollector` that resolves
a `wallet` identifier against the public
[Etherscan API](https://docs.etherscan.io/). The collector works on the
anonymous tier (no key required) so it is wired into the default
orchestrator unconditionally — passing `ETHERSCAN_API_KEY` simply lifts
the rate limit, it is not a feature flag.

```bash
# Vitalik's well-known address (mainnet, EIP-55-checksummed):
reckora investigate "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045" --kind wallet

# Lowercase form is accepted too — checksum is purely a display convention.
reckora investigate "0xd8da6bf26964af9d7eed9e03e53415d37aa96045" --kind wallet
```

The emitted trace normalises to a flat schema the dossier renderers can
read without parsing the raw envelope at render time:
`address` (lower-cased so identifier joins are case-insensitive),
`address_input` (original casing, preserved verbatim for display so a
user-supplied EIP-55 checksum survives round-tripping), `chain`
(`"ethereum"`), `network` (`"mainnet"`), `address_format` (`"evm"` —
shared across every EVM-compatible chain so a future Polygon /
Arbitrum / Base adapter can reuse the schema unchanged), `balance_wei`,
`balance_eth` (string-formatted with full 18-decimal precision, no
float drift), `outgoing_tx_count` (account nonce — exactly the number
of external txs the EOA has originated), `is_active`.

The collector silently no-ops on `wallet` strings that are not
EVM-shaped (e.g. a Bitcoin address) so the BTC adapter and any future
Solana / Cosmos collector can coexist on `IdentifierType.WALLET`. Two
Etherscan endpoints (`account/balance` and
`proxy/eth_getTransactionCount`) are combined into a single Trace; the
raw HTTP envelopes are dropped from evidence (`keep_raw=False`) and the
SHA-256 of the canonicalised combined payload is preserved as the
audit anchor. Etherscan's "Invalid address format" responses are
treated as no-ops; quota / rate-limit responses are surfaced upstream
so the orchestrator's per-collector logger records them once and the
investigation continues without this collector's data.

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
| `ETHERSCAN_API_KEY` | _(unset)_ | Etherscan API key — optional; lifts the anonymous tier's rate limit for the Ethereum wallet collector |

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
