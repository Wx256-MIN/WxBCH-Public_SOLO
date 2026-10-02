# WxBCH Public SOLO

Bitcoin Cash full node + solo mining application for Umbrel.

## What it provides

- Bitcoin Cash Node 29.2.0
- Persistent BCH blockchain data
- Internal RPC and ZMQ connection between the node and miner
- BCH-native CKPool solo Stratum server
- Native CashAddr usernames
- ASIC-oriented starting difficulty
- Web dashboard with node sync and mining statistics
- Stratum V1 endpoint on TCP/3333
- BCH P2P endpoint on TCP/8333

The mining component is based on the BCH-focused CKPool fork at https://github.com/skaisser/ckpool and is pinned to commit 0479f860e439d835d56779d2881312668994750c.

## Miner configuration

After the node is synchronized:

- Pool URL: `stratum+tcp://UMBREL_IP:3333`
- Username: your BCH address, optionally `.worker`
- Password: `x`

Example:

`bitcoincash:qq...worker01`

The BCH CKPool fork supports CashAddr and legacy BCH addresses for solo mining.

## Security model

The BCH RPC service is not published to the Umbrel host. It is reachable only through the internal Docker network. Only BCH P2P and Stratum are published by the application.

RPC credentials are generated at first start and stored in an application-owned Docker volume.

## Storage

A BCH full node requires substantial disk space. Keep the blockchain data on reliable storage and allow additional room for growth.

## Development

The app is structured as:

- `node/` - BCHN runtime image and configuration
- `ckpool/` - BCH CKPool runtime
- `web/` - lightweight dashboard/API
- `docker-compose.yml` - Umbrel service topology
- `umbrel-app.yml` - Umbrel app manifest

## Licensing

This repository's application glue and dashboard code are MIT licensed.

The packaged CKPool component is GPLv3 software from its upstream BCH-focused project. See the upstream repository and its COPYING file for the applicable terms.

Bitcoin Cash Node is distributed under the MIT license by its upstream project.

This project is an independent Umbrel integration and is not an official Bitcoin Cash Node or Umbrel project.
