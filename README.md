# BCH Solo Pool for UmbrelOS

A self-hosted Bitcoin Cash (BCH) Solo Stratum V1 pool for BCHN/AxeBCH and SHA-256 ASIC miners.

## What was fixed in this polished build

- Fixed the GitHub Actions workflow filename (`.yml`, not `.ylm`).
- Uses a public GHCR image name that matches the repository owner.
- Adds a first-run browser setup page, so you do **not** need to edit the Umbrel compose file to enter RPC credentials or your BCH payout address.
- Persists configuration under `/data/config.json`.
- Starts safely in setup mode when required BCH configuration is missing.
- Adds BCHN RPC connectivity validation before saving setup.
- Adds SQLite WAL/busy-timeout settings and indexes.
- Adds Docker healthcheck and `unless-stopped` restart policy.
- Removes committed Python `__pycache__` artifacts from the intended source tree.
- Keeps the Stratum endpoint on TCP `3334` and the dashboard on `8080`.
- Adds stronger Stratum input validation and correct internal transaction-hash byte order for merkle construction.
- Adds tests for CashAddr, compact targets, coinbase construction, and Stratum job serialization.

## UmbrelOS

This repository is structured as an Umbrel Community App Store. Umbrel community stores use a root store manifest and an app directory whose ID starts with the store ID.

1. Add this GitHub repository as a Community App Store in umbrelOS.
2. Install **BCH Solo Pool**.
3. Open the app. On first launch it will show the setup screen.
4. Enter:
   - BCHN/AxeBCH RPC URL, normally `http://host.docker.internal:8332/`
   - RPC username/password
   - Optional ZMQ URL, normally `tcp://host.docker.internal:28332`
   - BCH payout CashAddr
5. Save configuration. The app validates RPC connectivity, stores the configuration, and restarts itself.
6. Connect ASIC miners to:
   `stratum+tcp://UMBREL-IP:3334`
7. Use a worker name such as:
   `bitcoincash:qYOURADDRESS.worker1`
   with password `x`.

The dashboard is available through the Umbrel app proxy. The Stratum port is separately exposed on TCP 3334.

## BCHN/AxeBCH networking

The pool container needs access to the node RPC port and, if ZMQ is enabled, the ZMQ hashblock port. The default configuration uses Docker's `host-gateway` mapping for `host.docker.internal`.

Never expose BCHN RPC or ZMQ ports directly to the public Internet.

## Solo-mining behavior

This is solo mining, not PPS/PPLNS. Shares are proof-of-work accounting and health information. A miner is paid only when a valid block candidate is accepted by the BCH network. The pool does not hold private keys.

## Local Docker development

```bash
cp bch-solo-pool/.env.example bch-solo-pool/.env
# edit .env

docker compose -f bch-solo-pool/docker-compose.local.yml up -d --build
```

Dashboard: `http://127.0.0.1:8080`

Stratum: `stratum+tcp://127.0.0.1:3334`

## Architecture

```text
SHA-256 ASIC miners
        │
        │ Stratum V1 :3334
        ▼
┌────────────────────────┐
│      BCH Solo Pool     │
│ Stratum / GBT / SQLite │
│ Setup / Dashboard      │
└────────────┬───────────┘
             │ JSON-RPC / ZMQ
             ▼
        BCHN / AxeBCH
         RPC :8332
         ZMQ :28332
```

## Security

The included service is intended for a trusted LAN. It does not provide TLS, public-pool authentication, DDoS protection, or a hosted payout service. Keep RPC credentials private and restrict the Stratum port to miners you trust.

## Build

GitHub Actions builds `linux/amd64` and `linux/arm64` images and publishes them to GHCR. For a production release, pin the image to an immutable multi-architecture digest in `docker-compose.yml`.
