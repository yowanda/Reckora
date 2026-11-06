# Changelog

All notable changes to Reckora are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — 0.1.0 (Alpha)

The first public shape of Reckora: an AI-native OSINT investigation system.

### Added

- CLI (`reckora`) investigating usernames, emails, domains, URLs, phone
  numbers, and BTC/ETH/SOL wallets, with auto-detection of the seed kind.
- Dossier export in Markdown, HTML, JSON, NDJSON, and PDF, with Timeline,
  Anomalies, and Cross-trace anchor sections.
- Evidence graph with entity resolution, anomaly detection, and
  cross-subject references.
- Forensic capture: Wayback snapshots (`--archive`), full-page PNG
  screenshots (`--screenshot`), Merkle-root anchoring with OpenTimestamps
  (`--anchor`, `verify-anchor`).
- Opt-in collectors: HIBP breach lookup, doc-leak / paste search, avatar
  perceptual hash, semantic bio similarity (embeddings extra).
- HTTP API (`reckora-api`, FastAPI): JWT auth, RBAC, investigations,
  dossiers, sharing, comments + threading, reactions, mentions, labels,
  status, watchers, pins, private notes, visits/unread, TODOs, activity feed.
- Web frontend (`apps/web`) for the API.
- AI reasoning via OpenAI API key or ChatGPT OAuth login (PKCE).
- SQLite persistence shared by CLI and API; Docker + docker-compose deployment.
- Test suite: 1,000+ tests; CI (ruff, format check, mypy strict, pytest) on
  Python 3.11 and 3.12.
