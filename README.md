# WxBCH Public Pool

**Bitcoin Cash (BCH) solo mining pool based on the architecture of Benjamin Wilson's Public Pool, converted specifically for Bitcoin Cash Node (BCHN).**

Upstream reference: https://github.com/benjamin-wilson/public-pool

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

BCHN's current documentation describes `getblocktemplate` as the RPC that returns the data required to construct a block, including transactions, `coinbaseaux`, `coinbasevalue`, target, bits and height. BCHN also exposes `submitblock` and ZMQ block notifications.

## Miner connection

Use:

```
stratum+tcp://YOUR_POOL_IP:41837
```

Username is your BCH payout address, optionally followed by a worker name:

```
bitcoincash:qxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.worker1
```

Password can be `x`.

## BCHN configuration

Example:

```ini
server=1
rpcbind=0.0.0.0
rpcallowip=172.16.0.0/12
rpcuser=pooluser
rpcpassword=CHANGE_THIS_PASSWORD
zmqpubhashblock=tcp://0.0.0.0:28332
```

Use a firewall and a narrower RPC allow-list in production.

## Run with Docker

```bash
cp .env.example .env
# edit BCHN RPC settings
docker compose up -d --build
```

Standalone Docker uses the default application ports:

Dashboard/API:
```
http://YOUR_POOL_IP:3334/
http://YOUR_POOL_IP:3334/api/pool
```

Stratum:
```
stratum+tcp://YOUR_POOL_IP:3333
```

### UmbrelOS

The Umbrel package uses dedicated host ports so it does not collide with other mining apps:

- Dashboard/proxy: `41838`
- Stratum: `41837`
- Backend target: `41839` (internal proxy target; normally do not open this in a browser)

Miner:
```
stratum+tcp://YOUR_UMBREL_IP:41837
```

Dashboard:
```
http://YOUR_UMBREL_IP:41838/
```

The Umbrel package uses host networking and connects to BCHN through `127.0.0.1`, which is required for reliable umbrelOS 2.x operation.

## Important testing requirement

Before using mainnet hashpower, run this pool against BCHN regtest and verify an actual solved block is accepted by `submitblock`. A successful JavaScript build alone is not proof of consensus correctness.

## License

GPL-3.0-only. This project is an adaptation of the GPL-licensed Public Pool architecture.
