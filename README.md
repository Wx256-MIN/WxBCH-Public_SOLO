# WxBCH Public Pool

**Bitcoin Cash (BCH) solo mining pool based on the architecture of Benjamin Wilson's Public Pool, converted specifically for Bitcoin Cash Node (BCHN).**

Upstream reference: https://github.com/benjamin-wilson/public-pool

## Architecture

The Umbrel package is self-contained:

- **BCHN node:** runs inside the WxBCH app stack.
- **Pool:** connects to that BCHN node over the private Docker network.
- **Stratum V1:** exposed on port `41837`.
- **Dashboard/API:** exposed on port `41838` and through the Umbrel app proxy.
- **ZMQ:** BCHN `hashblock` notifications are used for fast template refresh.
- **No external mining-pool backend or third-party BCH node is required.**

The current BCHN release is 29.1.0, and the BCHN project recommends upgrading older 28.x and earlier nodes. citeturn10search0turn10search2

## What was changed for BCHN

- Uses BCHN `getblocktemplate`, `getmininginfo`, `submitblock`.
- Does **not** request Bitcoin SegWit rules.
- Builds a BCH non-witness coinbase.
- Includes BCHN `coinbaseaux` data.
- Accepts Bitcoin Cash CashAddr and legacy Base58 addresses.
- Keeps BCHN transaction data as raw hex instead of parsing arbitrary BCH transactions with a Bitcoin-only transaction parser.
- Builds merkle branches directly from BCHN TXIDs.
- Reconstructs and submits the complete BCH block through `submitblock`.
- Supports Stratum V1, variable difficulty and BIP310 version rolling / ASICBoost.
- Tracks worker shares, rejected shares and share difficulty in SQLite.

## Miner connection

Use:

```
stratum+tcp://YOUR_UMBREL_IP:41837
```

Username is your BCH payout address, optionally followed by a worker name:

```
bitcoincash:qxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.worker1
```

Password can be `x`.

## UmbrelOS

Install **WxBCH Public Pool** from the WxBCH community app store.

The app starts two containers:

1. **BCHN** — synchronizes the Bitcoin Cash mainnet and provides RPC/ZMQ privately to the pool.
2. **Web/Stratum** — runs the pool server and dashboard.

### Ports

- Dashboard/API: `41838`
- Stratum V1: `41837`
- BCHN RPC: internal only
- BCHN ZMQ: internal only

The BCHN RPC is intentionally not published to the LAN. Only the pool container can access it.

### First startup

A fresh BCHN node must synchronize the Bitcoin Cash blockchain before the pool can build live templates. During initial synchronization the dashboard may show:

```
height: null
networkDifficulty: null
miners: 0
```

Once BCHN is synchronized and the RPC becomes ready, the pool will automatically start refreshing templates.

## Run with Docker

For standalone Docker, the root `docker-compose.yml` can connect the pool to an existing BCHN RPC:

```bash
cp .env.example .env
# edit BCHN RPC settings
docker compose up -d --build
```

Standalone Docker uses:

Dashboard/API:
```
http://YOUR_POOL_IP:3334/
http://YOUR_POOL_IP:3334/api/pool
```

Stratum:
```
stratum+tcp://YOUR_POOL_IP:3333
```

## Important testing requirement

Before using mainnet hashpower, run this pool against BCHN regtest and verify an actual solved block is accepted by `submitblock`. A successful JavaScript build alone is not proof of consensus correctness.

## Node storage

Bitcoin Cash Node stores the blockchain and chainstate locally. BCHN documentation notes that the full history requires a few hundred gigabytes and that initial synchronization can take hours or longer depending on hardware and network speed. citeturn0search2

Make sure the Umbrel storage location has enough free space for a full BCH mainnet node.

## License

GPL-3.0-only. This project is an adaptation of the GPL-licensed Public Pool architecture.
