# Arc Testnet Automation Web App

Static web toolkit for validated Arc testnet tasks.

## Security model

- No private key is embedded in the source or APK.
- Use a burner key only; never paste a seed phrase or main-wallet key.
- Transactions are simulated before sending.
- USDC approvals are exact task amounts, not unlimited.

## Run locally

```bash
cd /home/ubuntu/arc-testnet/automation-web
python3 -m http.server 8080
```

Open `http://localhost:8080`.

## Included tasks

- Arc GM Portal
- zkCodex GM / Counter / ScoreMint
- OnChainGM
- ArcWorld Key / Passport / quick transfer
- SurfLayer Daily GM / Deploy
- Balance checker
- Manual wrapper links for Micro3 and Arkada claims

Micro3 and Arkada platform claims require sign-in / wallet verification and are intentionally not bypassed.
