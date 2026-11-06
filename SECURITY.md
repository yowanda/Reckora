# Security Policy

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability. Instead, open a
[private security advisory](https://github.com/yowanda/Reckora/security/advisories/new)
on this repository. Include:

- What the issue is and where it is (file / endpoint / command).
- Steps to reproduce, if you have them.
- The impact as you understand it.

We will acknowledge the report, investigate, and coordinate a fix and disclosure
timeline with you before anything is published.

## Scope notes

Reckora is an OSINT investigation tool. It queries public sources and, with
opt-in flags, third-party services (e.g. HIBP breach lookup, Etherscan,
OpenAI/ChatGPT). Please:

- Only investigate subjects you are authorized to investigate, and respect the
  terms of service of the sources you query.
- Never commit API keys, tokens, or personal data to the repository. Use
  environment variables (see `README.md` for the full list).
- Treat dossiers as sensitive: they aggregate personal data. Store and share
  them only with people who are authorized to see them.
