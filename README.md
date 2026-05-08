# Reckora

AI-native OSINT investigation system. Entity resolution, evidence-graph reasoning,
explainable intelligence.

## Install

```bash
uv sync --extra dev
```

## Usage

```bash
reckora investigate octocat --kind username
reckora investigate example.com --kind domain --ai
reckora investigate https://github.com/octocat --kind url --output dossier.md
```

Set `OPENAI_API_KEY` to enable `--ai`.

## License

[MIT](./LICENSE)
