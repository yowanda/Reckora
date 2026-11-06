# Contributing to Reckora

Thanks for helping make Reckora better. This guide covers the dev setup and the checks every pull request must pass.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
```

Optional extras (opt-in):

- `--extra screenshots` — Playwright + Chromium for forensic PNG capture.
- `--extra neo4j` — Neo4j adapter for cross-subject graph queries.
- `--extra embeddings` — `sentence-transformers` for semantic bio similarity.

## Checks (must all pass before you open a PR)

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # type checking (strict)
uv run pytest -q             # tests (1,000+)
```

CI runs the same four checks on Python 3.11 and 3.12 for every push and pull request.

## Pull requests

- Keep PRs focused: one change, one purpose. Stacked PRs are fine — CI runs on every PR regardless of base branch.
- Add or update tests for behavior changes. New collectors, API endpoints, and dossier fields need coverage.
- Update `README.md` / `ROADMAP.md` / `CHANGELOG.md` when a change is user-visible.
- Write clear commit messages: what changed and why, not just what.
- Be respectful in reviews and issues. See the MIT license and `SECURITY.md` for the security disclosure process.

## Reporting bugs

Open an issue with: the command you ran (redact API keys and personal data), what you expected, what happened, and your Python version + how you installed Reckora (`uv`, `pip`, Docker).
