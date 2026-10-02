# WxBCH Public Pool — BCH Community Store Integration

This Umbrel package integrates WxBCH Public Pool with the existing
`sslabs-bitcoin-cash-node` application from `danhaus93-ops/umbrel-bch-apps`.

It deliberately does **not** start a second BCHN node.

## Connection to the store BCHN app

- Dependency: `sslabs-bitcoin-cash-node`
- RPC: `sslabs-bitcoin-cash-node_bitcoind_1:8332`
- ZMQ hashblock: `sslabs-bitcoin-cash-node_bitcoind_1:28332`
- RPC user: `bchn`
- RPC password: the password configured by the store's BCHN app

## Ports

- Dashboard/API: 41838 through Umbrel's proxy
- Stratum V1: 41837 directly on the Umbrel host

Example:

`stratum+tcp://<UMBREL-IP>:41837`

Install and synchronize the BCHN app before running the pool.
