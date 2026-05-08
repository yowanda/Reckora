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

Set `OPENAI_API_KEY` to enable `--ai` (LLM-generated summary + hypotheses,
evidence-bounded with `ev:<8-hex>` citations).

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

## License

[MIT](./LICENSE)
