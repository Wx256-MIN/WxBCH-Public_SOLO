# WxBCH Public Pool

**Bitcoin Cash (BCH) solo mining pool based on the architecture of Benjamin Wilson's Public Pool, converted specifically for Bitcoin Cash Node (BCHN).**

Upstream reference: https://github.com/benjamin-wilson/public-pool

## Umbrel community-store integration

WxBCH is designed to run with the **existing BCHN node supplied by the community BCH store**:

- BCHN app: `sslabs-bitcoin-cash-node`
- BCHN RPC: `sslabs-bitcoin-cash-node_bitcoind_1:8332`
- BCHN ZMQ hashblock: `sslabs-bitcoin-cash-node_bitcoind_1:28332`
- No second BCHN container is started by WxBCH.
- The BCHN RPC and ZMQ endpoints remain private to the Umbrel app network.

The pool image is published for Linux amd64 and arm64.

## Features

- BCHN `getblocktemplate`, `getmininginfo`, `submitblock`
- BCH-native non-witness coinbase
- BCHN `coinbaseaux`
- CashAddr and legacy Base58 payout addresses
- Stratum V1
- Variable difficulty
- BIP310 version rolling / ASICBoost
- Worker statistics
- Accepted and rejected shares
- Share difficulty tracking
- Best-share difficulty
- SQLite persistence
- ZMQ block notifications
- Full BCH block reconstruction and `submitblock`

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

## UmbrelOS installation

1. Install **Bitcoin Cash Node** (`sslabs-bitcoin-cash-node`) from the community store.
2. Allow BCHN to synchronize.
3. Install **WxBCH Public Pool**.
4. Open the WxBCH dashboard.
5. Point your SHA-256 ASIC miners at:

```
stratum+tcp://YOUR_UMBREL_IP:41837
```

### Ports

| Service | Port |
|---|---:|
| Stratum V1 | 41837 |
| Dashboard/API | 41838 |
| BCHN RPC | Private |
| BCHN ZMQ | Private |

During initial BCHN synchronization, the dashboard can temporarily show:

```
height: null
networkDifficulty: null
miners: 0
```

After BCHN is synchronized and RPC is ready, the pool will automatically refresh its template.

## Standalone Docker

The root `docker-compose.yml` can still connect to an externally running BCHN node:

```bash
cp .env.example .env
# edit BCHN RPC settings
docker compose up -d --build
```

Standalone ports are 3333 (Stratum) and 3334 (dashboard/API).

## Important

Before directing meaningful mainnet hashpower at a new pool release, validate block construction and `submitblock` acceptance against BCHN regtest. A successful JavaScript build alone is not proof of consensus correctness.

## License

GPL-3.0-only.
