# Reckora

> **AI-Native OSINT Investigation System**

Reckora is an investigative operating system for digital identity. It is **not** a username
checker or a site enumerator. It is built around a single question:

> *Are these traces from the same identity?*

Where traditional OSINT tools optimize for breadth ("found in 500 sites"), Reckora optimizes
for **explainable intelligence**: entity resolution, evidence-graph reasoning, and AI-assisted
hypothesis generation grounded in verifiable evidence.

## Core principles

- **Entity-first data model** — Subjects, Identifiers, Traces, Evidence, Edges as primitives.
- **Evidence chain** — every claim links back to a content-hashed source, timestamp, and raw
  payload. No hallucinations, no unsourced assertions.
- **Confidence as a first-class type** — every relationship carries a 0.0–1.0 score plus a
  human-readable reason array.
- **Explainable AI reasoning** — the LLM operates over verified evidence, not raw web scraping.
  It summarizes, hypothesizes, and flags anomalies; it never invents data.
- **Strategic advantage is intelligence quality, not site count.**

## Status

This repository is the v2 rewrite. The legacy enumeration-first codebase lives at
[yowanda/Reckora-legacy](https://github.com/yowanda/Reckora-legacy) and is preserved for
reference and future opt-in collector adapters.

Phase 1 MVP — entity model, evidence chain, three collectors (GitHub API, WHOIS/RDAP, web
profile probe), correlation engine (username mutation, avatar perceptual hash, timezone
overlap, bio similarity), AI reasoning (summarize + hypothesize), CLI dossier output — is in
flight. See `pyproject.toml` for the full dependency surface and the open Phase 1 PR for the
implementation.

## License

[MIT](./LICENSE)
