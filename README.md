# BCH Solo Pool for UmbrelOS

A self-hosted Bitcoin Cash (BCH) Solo Stratum V1 pool for BCHN/AxeBCH and SHA-256 ASIC miners.

## UmbrelOS Community App Store

This repository is packaged as an Umbrel Community App Store:

- Store ID: `bch-solo`
- App ID: `bch-solo-pool`
- Web dashboard: port `8080`
- Stratum V1: port `3334`
- Supported container architectures: `linux/amd64` and `linux/arm64`

### Install from the Umbrel dashboard

1. Open the Umbrel App Store.
2. Open the **Community App Stores** menu.
3. Add:
   `https://github.com/Wx256-MIN/WxBCH-SOLO`
4. Refresh/update the store.
5. Install **BCH Solo Pool**.
6. The installed app appears as a normal app tile in the Umbrel dashboard. Open it to reach the first-run setup page.

### CLI alternative

From the Umbrel host:

```bash
sudo ~/umbrel/scripts/repo add https://github.com/Wx256-MIN/WxBCH-SOLO
sudo ~/umbrel/scripts/repo update
sudo ~/umbrel/scripts/app install bch-solo-pool
```

### Important: GHCR image visibility

Umbrel installs the prebuilt image referenced by `docker-compose.yml`:

```text
ghcr.io/wx256-min/bch-solo-pool:latest
```

The GitHub Actions build is already publishing the image to GHCR. The **Container Registry package must be public (or otherwise anonymously pullable)** for a normal Umbrel community-store installation.

To check this on GitHub:

1. Open your GitHub profile → **Packages** → **bch-solo-pool**.
2. Open **Package settings**.
3. Check **Visibility**.
4. Set it to **Public** when the package is private.

### First-run setup

Open **BCH Solo Pool** from the Umbrel dashboard.

Enter:

- BCHN/AxeBCH RPC URL, normally `http://host.docker.internal:8332/`
- RPC username
- RPC password
- Optional ZMQ hashblock URL, normally `tcp://host.docker.internal:28332`
- BCH payout CashAddr

The setup page validates the RPC connection before saving the configuration. Configuration is persisted in the app data directory at `/data/config.json`.

### Miner connection

Point SHA-256 ASIC miners to:

```text
stratum+tcp://UMBREL-IP:3334
```

Example worker:

```text
bitcoincash:qYOURADDRESS.worker1
```

Password:

```text
x
```

The payout address used by the miner is the address that the pool uses when constructing the solo block coinbase.

## BCHN/AxeBCH networking

The pool container reaches a BCHN/AxeBCH node through Docker's `host.docker.internal` host-gateway mapping.

Typical endpoints:

```text
RPC  http://host.docker.internal:8332/
ZMQ  tcp://host.docker.internal:28332
```

Never expose BCHN RPC or ZMQ ports directly to the public Internet.

## Solo-mining behavior

This is solo mining, not PPS/PPLNS. Shares are proof-of-work accounting and health information. A miner is paid only when a valid BCH block candidate is accepted by the network. The pool does not hold private keys.

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
        |
        | Stratum V1 :3334
        v
+------------------------+
|      BCH Solo Pool     |
| Stratum / GBT / SQLite |
| Setup / Dashboard      |
+------------+-----------+
             | JSON-RPC / ZMQ
             v
        BCHN / AxeBCH
         RPC :8332
         ZMQ :28332
```

## Build

GitHub Actions builds `linux/amd64` and `linux/arm64` images and publishes them to GHCR.

Current published tags include:

```text
ghcr.io/wx256-min/bch-solo-pool:latest
ghcr.io/wx256-min/bch-solo-pool:sha-<commit>
```

For long-term releases, pin the Umbrel compose file to a specific image digest.

## Packaging notes

- Root `umbrel-app-store.yml` defines the Community App Store.
- `bch-solo-pool/umbrel-app.yml` defines the app metadata.
- `bch-solo-pool/docker-compose.yml` defines the Umbrel services.
- `bch-solo-pool/icon.svg` is served through jsDelivr so the Community Store can load the app icon.
