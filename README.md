# BCH Solo Pool for UmbrelOS

A self-hosted Bitcoin Cash (BCH) Solo Stratum V1 pool for BCHN and SHA-256 ASIC miners such as Avalon Nano 3S and Bitaxe.

> **Solo mining:** shares are used for proof-of-work accounting and miner health. A payout happens only if this pool finds a valid BCH block and BCHN successfully submits it to the network.

## Quick start

1. Install **BCHN** on Umbrel and wait for it to synchronize.
2. Install **BCH Solo Pool** from the Community App Store.
3. Open the pool and configure BCHN RPC, ZMQ, and your BCH payout CashAddr.
4. Confirm the dashboard shows BCHN as **online**.
5. Point your ASIC miners at `stratum+tcp://UMBREL-IP:3334`.
6. Give each miner a unique worker suffix and password `x`.

Example for Umbrel `192.168.50.100`:
```text
Dashboard: http://192.168.50.100:3567
Stratum:   stratum+tcp://192.168.50.100:3334
```

## Install on UmbrelOS

Open **Umbrel → App Store → Community App Stores**, add:

`https://github.com/Wx256-MIN/WxBCH-SOLO`

Refresh the store and install **BCH Solo Pool**.

CLI alternative:
```bash
sudo ~/umbrel/scripts/repo add https://github.com/Wx256-MIN/WxBCH-SOLO
sudo ~/umbrel/scripts/repo update
sudo ~/umbrel/scripts/app install bch-solo-pool
```

### GHCR image visibility

The app uses:
```text
ghcr.io/wx256-min/bch-solo-pool:latest
```

If Umbrel cannot pull the image, check **GitHub → Packages → bch-solo-pool → Package settings → Visibility**. A normal public Community App Store installation requires the image to be publicly pullable.

## BCHN configuration

The pool needs BCHN JSON-RPC and the ZMQ `hashblock` endpoint.

For the official Umbrel BCHN app, the pool is configured to use:
```text
RPC:      http://bitcoind:8332/
ZMQ:      tcp://bitcoind:28332
RPC user: umbrel
```

The RPC account must be able to call `getblocktemplate`, `getblockchaininfo`, `getnetworkinfo`, and `submitblock`.

**Never expose BCHN RPC port 8332 or ZMQ port 28332 directly to the public Internet.**

## First-run setup

Open **BCH Solo Pool** in Umbrel and enter:

| Setting | Example |
|---|---|
| BCHN RPC URL | `http://bitcoind:8332/` |
| RPC username | `umbrel` |
| RPC password | Your BCHN RPC password |
| ZMQ hashblock URL | `tcp://bitcoind:28332` |
| BCH payout address | `bitcoincash:q...` |

Click **Save / Connect** and confirm BCHN is online.

### Payout address

Use a BCH CashAddr that you control. The pool puts the address into the solo block coinbase; it does not need your private key.

**Never enter a private key or seed phrase.**

## Configure ASIC miners

All miners use Stratum V1 on TCP port `3334`.

Example worker:
```text
Pool URL: stratum+tcp://192.168.50.100:3334
Worker:   bitcoincash:qYOURADDRESS.bitaxe602
Password: x
```

For the Nano 3S:
```text
Pool URL: stratum+tcp://192.168.50.100:3334
Worker:   bitcoincash:qYOURADDRESS.nano3s
Password: x
```

Use a different suffix for every ASIC, such as `.nano3s` and `.bitaxe602`.

The dashboard shows worker suffixes rather than exposing the complete payout address.

## ASICBoost / version rolling

The pool supports **overt ASICBoost through Stratum V1 BIP310 version rolling**.

A compatible miner negotiates `version-rolling` with `mining.configure`. The miner can then submit a sixth `version_bits` value. The pool applies only the negotiated mask to the block-header version and protects all other version bits.

Important: pool support does not guarantee that an ASIC will actually use ASICBoost. The miner firmware and ASIC must support version rolling and perform it.

The implementation intentionally uses **overt version rolling**. It does not modify transactions or the coinbase for covert ASICBoost.

## Difficulty and VarDiff

Default settings:
```text
Start difficulty:       1000
VarDiff:                enabled
Target share time:      30 seconds
Minimum difficulty:     0.001
Maximum difficulty:     65536
```

Configure these from **Dashboard → Settings → Mining difficulty**.

For mixed hardware, VarDiff can remain enabled. For fixed difficulty, disable VarDiff and choose the starting difficulty.

A share is not a payout. It proves work and is used for health/hashrate accounting. Only a valid BCH network block earns the solo block reward.

## Dashboard

Umbrel normally exposes the dashboard on port `3567`, while miners use port `3334`.

The dashboard includes BCHN state, block height, peers, connected miners, pool hashrate, best share difficulty, network difficulty/hashrate, accepted/rejected shares, current job, worker statistics, block submissions, events, difficulty settings, themes, and optional auto-refresh.

## Job handling

The pool retains **8 recent Stratum jobs**. New BCH chain tips use `clean_jobs=true`. Same-tip template refreshes use `clean_jobs=false`, allowing valid in-flight work to continue while the pool refreshes its template.

Per-job difficulty is retained so late shares are validated against the difficulty assigned when their job was issued.

## Reliability features

- bounded Stratum socket writes
- zombie-session/activity timeout
- per-worker submit-rate protection
- bounded ntime validation
- transient BCHN RPC retries
- retryable `submitblock`
- ZMQ block notifications
- fallback template refresh
- retained Stratum job history
- per-job difficulty pinning
- duplicate-share protection
- SQLite WAL and durable writes
- graceful shutdown/restart
- concurrent job broadcasting with per-miner timeout
- BIP310 version rolling / overt ASICBoost

## Local Docker development

```bash
cp bch-solo-pool/.env.example bch-solo-pool/.env
nano bch-solo-pool/.env
docker compose -f bch-solo-pool/docker-compose.local.yml up -d --build
```

Dashboard: `http://127.0.0.1:8080`
Stratum: `stratum+tcp://127.0.0.1:3334`

Stop with:
```bash
docker compose -f bch-solo-pool/docker-compose.local.yml down
```

## Environment configuration

For local Docker deployments, `.env.example` documents the settings. Important values are:
```dotenv
BCH_RPC_URL=http://host.docker.internal:8332/
BCH_RPC_USER=poolrpc
BCH_RPC_PASSWORD=
BCH_ZMQ_URL=tcp://host.docker.internal:28332
BCH_PAYOUT_ADDRESS=
STRATUM_HOST=0.0.0.0
STRATUM_PORT=3334
WEB_HOST=0.0.0.0
WEB_PORT=8080
START_DIFFICULTY=1000
VARDIFF_ENABLED=true
VARDIFF_TARGET_SECONDS=30
VARDIFF_MIN=0.001
VARDIFF_MAX=65536
```

On Umbrel, prefer the pool setup/settings UI instead of manually editing container environment variables.

## Troubleshooting

### BCHN is offline
Check that BCHN is running and synchronized, RPC credentials are correct, the RPC URL is reachable from the pool, and ZMQ uses port `28332`.

### Miner cannot connect
Check the Umbrel IP, LAN reachability, pool status, and TCP port `3334`. Do not use dashboard port `3567` as the Stratum port.

### Miner connects but has no accepted shares
Check miner hashrate, worker name, current share difficulty, rejected/stale shares, job age, and BCHN synchronization. Higher difficulty naturally produces fewer shares.

### Bitaxe negotiates ASICBoost but speed does not increase
The pool only provides correct Stratum negotiation and version-bit handling. Actual ASICBoost behavior depends on the Bitaxe/AxeOS/ASIC implementation. Compare stable hashrate and power rather than the negotiation message alone.

### Pool starts before BCHN
The pool is designed to recover when BCHN becomes available later. Once RPC/ZMQ becomes reachable, it obtains a template and refreshes Stratum jobs.

## Security

- Keep Stratum restricted to your LAN unless remote mining is intentional.
- Never expose BCHN RPC `8332` or ZMQ `28332` to the Internet.
- Never put a BCH private key or seed phrase into the pool.
- Do not publish `/data/config.json`; it can contain RPC credentials.
- Keep Umbrel and BCHN updated.

## Repository structure

```text
WxBCH-SOLO/
├── umbrel-app-store.yml
└── bch-solo-pool/
    ├── app/
    │   ├── config.py
    │   ├── db.py
    │   ├── main.py
    │   ├── node.py
    │   ├── stratum.py
    │   ├── web.py
    │   └── crypto.py
    ├── tests/
    ├── Dockerfile
    ├── docker-compose.yml
    ├── docker-compose.local.yml
    ├── .env.example
    └── umbrel-app.yml
```

## Build and release

GitHub Actions builds `linux/amd64` and `linux/arm64` images and publishes them to GHCR.

```text
ghcr.io/wx256-min/bch-solo-pool:latest
```

For reproducible deployments, pin a release/image digest rather than relying on `latest`.