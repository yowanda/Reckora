# Reckora Arc Testnet Automation

Web-based burner-wallet automation toolkit for Arc testnet activity.

## Run

Open `arc-automation-web/index.html` from a static server, or deploy the folder as a static site. The app never stores or embeds private keys; paste a burner key only at runtime.

## Features

- Validated Arc campaign actions split by frequency: `daily`, `loopable`, `one-time`, `optional`, and `utility`.
- Daily GM/check-in tasks are locally guarded to run once per wallet per UTC day.
- Activity Mode excludes daily tasks and only loops repeatable transfer-self, Curve swap, and ScoreMint update actions.
- Safety controls: max transactions, delay, native gas budget, exact approvals, simulation warnings, and stop-on-error.

Use testnet burner wallets only.
