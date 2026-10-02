# WxBCH Community App Store

Community App Store for **BCH Solo Pool** on umbrelOS.

## Add this store to umbrelOS

Use this repository URL when adding a Community App Store:

```
https://github.com/Wx256-MIN/WxBCH-SOLO
```

Store ID:

```
wxbch
```

App ID:

```
wxbch-solo-pool
```

## BCH Solo Pool

BCH Solo Pool provides Stratum V1 solo mining for Bitcoin Cash using BCHN.

### Miner configuration

```
URL: stratum+tcp://YOUR_UMBREL_IP:3336
Username: bitcoincash:YOUR_BCH_ADDRESS.worker1
Password: x
```

The BCH address before the first `.` is used as the payout address.

### Features

- Stratum V1
- BCHN getblocktemplate
- Direct block submission
- BCH CashAddr and legacy-address validation
- Version rolling / ASICBoost-compatible Stratum negotiation
- Worker statistics
- Accepted/rejected share tracking
- Web dashboard
- Non-root Docker container

## Repository layout

```
umbrel-app-store.yml
wxbch-solo-pool/
├── umbrel-app.yml
├── docker-compose.yml
├── Dockerfile
├── exports.sh
├── package.json
└── src/
    └── main.js
```

## License

GPL-3.0-or-later.
