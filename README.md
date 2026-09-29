# BCH Solo Pool for UmbrelOS

A self-hosted Bitcoin Cash (BCH) Solo Stratum V1 pool designed for BCHN and SHA-256 ASIC miners.

## What this repository contains

- `bch-solo-pool/` — the Umbrel Community App Store package.
- `.github/workflows/docker-publish.yml` — builds multi-architecture Docker images and publishes them to GHCR.
- `bch-solo-pool/tests/` — core unit tests.

The package uses BCHN JSON-RPC (`getblocktemplate`, `submitblock`) and optional ZMQ `hashblock` notifications. It does **not** store private keys; the configured BCH payout address receives the coinbase reward when a valid block is found.

## Install on UmbrelOS

This repository is structured as an Umbrel Community App Store. Umbrel's community-store format requires a root `umbrel-app-store.yml` and an app directory whose ID begins with the store ID. See the official Umbrel template for the current format: https://github.com/getumbrel/umbrel-community-app-store

### 1. Fork this repository

Create your own GitHub fork. In `bch-solo-pool/docker-compose.yml`, replace:

```text
ghcr.io/YOUR_GITHUB_USERNAME/bch-solo-pool:v1.0.0
```

with your GitHub username/organization.

### 2. Enable GitHub Actions

Push the fork to GitHub. The workflow builds `linux/amd64` and `linux/arm64` images and publishes the image to GitHub Container Registry (GHCR).

Make the published GHCR package **Public**, because an Umbrel device must be able to pull it anonymously.

### 3. Pin the image digest

For a personal/community store, the image tag is convenient for testing. For a production/community-store release, pin the GHCR image to its immutable `sha256` digest in `bch-solo-pool/docker-compose.yml`.

### 4. Add the repository to Umbrel

In umbrelOS, open **App Store → Community App Stores** and add your GitHub repository URL.

Then install **BCH Solo Pool**.

### 5. Configure BCHN

The app needs access to your synchronized BCHN node. Edit the app's compose environment if your node is not reachable at the defaults:

```text
BCH_RPC_URL=http://host.docker.internal:8332/
BCH_RPC_USER=poolrpc
BCH_RPC_PASSWORD=YOUR_RPC_PASSWORD
BCH_ZMQ_URL=tcp://host.docker.internal:28332
BCH_PAYOUT_ADDRESS=bitcoincash:qYOUR_ADDRESS
```

Never expose BCHN RPC or ZMQ ports to the Internet.

## Local Docker development

From `bch-solo-pool/`:

```bash
cp .env.example .env
# edit .env

docker compose -f docker-compose.local.yml up -d --build
```

Dashboard: `http://UMBREL-IP:8080`

Stratum: `stratum+tcp://UMBREL-IP:3334`

Example worker:

```text
URL:      stratum+tcp://192.168.1.50:3334
USER:     bitcoincash:qYOURADDRESS.nano3s
PASSWORD: x
```

## Architecture

```text
SHA-256 ASIC miners
        │
        │ Stratum V1 :3334
        ▼
┌───────────────────────┐
│     BCH Solo Pool     │
│ Stratum / GBT / DB    │
│ Dashboard / Vardiff   │
└───────────┬───────────┘
            │ JSON-RPC
            ▼
       BCHN Node
       :8332 RPC
       :28332 ZMQ
```

## Important

This is **solo mining**, not PPS/PPLNS. Shares are accounting/health information; a payout occurs only when a submitted block is accepted by the BCH network.

The included implementation is intended for trusted LAN use. It does not provide TLS, public-pool authentication, DDoS protection, or a hosted payout system.

## Development status

The repository is packaged for Umbrel Community App Store use, but a real Umbrel install should still be tested against the exact BCHN/AxeBCH networking setup on the target machine before relying on it for mining.
