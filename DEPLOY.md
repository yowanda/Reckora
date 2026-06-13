# Deploying Reckora

This guide walks a single-host Docker Compose deploy of Reckora —
FastAPI backend + React/TS SPA + Caddy auto-HTTPS — onto any modern
Linux box with a public IP and a registered domain.

The walk-through assumes:

* **Host**: any x86_64 Linux distribution with `systemd` and a 2 vCPU /
  4 GB RAM minimum. Tested on Ubuntu 24.04 LTS (Canonical).
* **Domain**: a hostname you control (any TLD). The reference flow
  uses Cloudflare-hosted DNS, but a registrar's native DNS panel works
  equally well — Caddy only needs the A record to resolve.
* **TLS**: a public-IPv4 / IPv6-reachable host so Let's Encrypt can
  perform the HTTP-01 challenge on port 80.

There is no magic in this layout — everything is plain Docker Compose
+ a Caddyfile. If you want to swap Caddy for nginx + certbot, or move
the API behind a separate cloud load balancer, the engine itself does
not care.

## 1. Prepare the host

```bash
# 1a. Update the OS.
sudo apt-get update && sudo apt-get upgrade -y

# 1b. Install Docker Engine + the compose plugin from the official repo.
#     (Ubuntu's distro package is too old; the official channel is one-line.)
curl -fsSL https://get.docker.com | sudo sh

# 1c. Run docker without sudo for the deploy user.
sudo usermod -aG docker "$USER"
newgrp docker

# 1d. Enable Docker on boot.
sudo systemctl enable --now docker
```

### Firewall

Open the three ports Caddy and SSH need; deny everything else.

```bash
sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # HTTP (ACME challenge + redirect)
sudo ufw allow 443/tcp    # HTTPS
sudo ufw --force enable
```

If the host is behind a cloud-provider firewall (e.g. an OCI
"Security List" or AWS "Security Group"), open the same three TCP
ports there as well. Without that, Let's Encrypt's ACME challenge
will time out and Caddy will refuse to issue a cert.

## 2. Clone the repo

```bash
git clone https://github.com/yowanda/Reckora.git
cd Reckora
```

If the repo is private, use a deploy key or a fine-scoped PAT:

```bash
GITHUB_TOKEN=ghp_... git clone \
    https://x-access-token:$GITHUB_TOKEN@github.com/yowanda/Reckora.git
```

## 3. Configure environment

Copy the template and fill in the two required fields:

```bash
cp deploy/.env.example .env

# generate a high-entropy JWT secret
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Edit `.env`:

```ini
DOMAIN=reckora.example.com
RECKORA_API_JWT_SECRET=<paste the token above>
RECKORA_API_CORS_ORIGINS=https://reckora.example.com
```

Optional integrations (`OPENAI_API_KEY`, `HIBP_API_KEY`,
`ETHERSCAN_API_KEY`, `GITHUB_TOKEN`) can stay blank — Reckora will
silently skip the corresponding collectors / reasoning paths.

## 4. Point DNS at the host

Add an `A` record:

```
reckora.example.com.  300  IN  A  <public-ip-of-this-host>
```

If you use Cloudflare, leave the proxy **off** for the first deploy
so Let's Encrypt can talk to Caddy directly. Once the cert is issued
you can flip the orange cloud back on and switch SSL/TLS mode to
`Full (strict)`.

Verify with `dig`:

```bash
dig +short reckora.example.com
# expected: <your public ip>
```

Wait for resolution to actually return the right A record before
moving on — bringing the stack up against a stale DNS view will
trigger Let's Encrypt rate limits.

## 5. Bring the stack up

```bash
docker compose build
docker compose up -d
docker compose ps
```

Tail Caddy's log to watch the cert get issued:

```bash
docker compose logs -f web
```

You should see one of:

* `certificate obtained successfully` — done.
* `acme: domain ... no name found` — DNS not propagated yet; wait.
* `port 80 already in use` — another web server is bound; stop it
  (`sudo systemctl disable --now nginx apache2` etc.) and retry.

Smoke test:

```bash
curl https://reckora.example.com/healthz
# {"status":"ok"}
```

## 6. Create the first admin

The API ships with no users by default; the first one has to be
created from inside the container:

```bash
docker compose exec api reckora-api create-user admin --password 'change-this-password'
# created user admin (id=..., role=admin)
```

If you omit `--password`, the CLI prompts for it interactively. The
command creates an admin by default; pass `--viewer` only when creating
a restricted viewer account.

Log in via the SPA at `https://reckora.example.com/login` and run a
sample investigation to confirm the engine + reasoning + reporting
paths are all wired up.

## 7. Day-2 ops

### Logs

```bash
docker compose logs -f api          # FastAPI / engine logs
docker compose logs -f web          # Caddy access + cert renewal
```

### Pull a new release

```bash
git pull --ff-only
docker compose build
docker compose up -d                # zero-downtime restart of changed services
docker image prune -f
```

### Backups

The only stateful directory inside the API container is `/data`,
mounted from the named volume `reckora_data`. Snapshot it however
your platform wants — the simplest approach:

```bash
docker run --rm \
    -v reckora_data:/from \
    -v "$PWD":/to \
    alpine \
    tar czf "/to/reckora-data-$(date +%F).tgz" -C /from .
```

The Caddy volumes (`caddy_data`, `caddy_config`) hold the issued
certs; backing them up is optional — Let's Encrypt will reissue on
first launch if they're empty.

### Rotating the JWT secret

Edit `.env`, then `docker compose up -d api`. All in-flight tokens
become invalid immediately, which is the desired behaviour.

### Scaling out

For a larger deployment, switch the `api` service to point at a
managed Postgres + an external object store for screenshots, then
front the Caddy stack with a cloud LB and run multiple `api`
replicas. The engine itself is stateless aside from `/data`, so
horizontal scaling is straightforward — but a single host comfortably
serves a small team's investigation workload.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `502 Bad Gateway` from Caddy | `api` container crashed; check `docker compose logs api`. Most common cause: missing `RECKORA_API_JWT_SECRET` in `.env`. |
| `dial tcp 0.0.0.0:80: bind: address already in use` | Another web server is bound on port 80. Stop it before bringing the stack up. |
| Cert issuance loops | DNS A record points at the wrong IP, or the cloud-provider firewall is blocking 80/443. Verify with `dig` + `nc -zv <ip> 80`. |
| Permission errors on `/data` | The volume was created with the wrong UID. Clear it (`docker volume rm reckora_data`) and let compose recreate it. |
| `reckora-api create-user` refuses to run | Your `.env` lacks `RECKORA_API_JWT_SECRET` or it's shorter than 32 bytes. Generate a new one and `docker compose up -d api`. |
