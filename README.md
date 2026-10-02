# BCH Solo Pool

Clean Bitcoin Cash solo mining for BCHN on Umbrel, following the simple Public Pool architecture.

## Miner
URL: stratum+tcp://YOUR_UMBREL_IP:3336
Username: bitcoincash:YOUR_BCH_ADDRESS.worker1
Password: x

The address before the first dot receives the solo block reward.

## Components
- Stratum V1
- BCHN getblocktemplate
- Direct submitblock
- CashAddr and BCH legacy payout-address validation
- Worker dashboard/API
- Non-root Docker container

## License
GPL-3.0-or-later.
