#!/bin/sh
set -eu
. /run/wxbch/rpc.env
mkdir -p /etc/ckpool /var/lib/ckpool /run/ckpool /var/lib/ckpool/logs
cat > /etc/ckpool/ckpool.conf <<EOF
{
  "btcd": [{"url":"$BCH_RPC_HOST:$BCH_RPC_PORT","auth":"$BCH_RPC_USER","pass":"$BCH_RPC_PASSWORD","notify":true,"zmqnotify":"$BCH_ZMQ_URL"}],
  "poolfee": 0.0,
  "btcsig": "/WxBCH Public SOLO/",
  "blockpoll": 50,
  "update_interval": 15,
  "serverurl": ["0.0.0.0:$STRATUM_PORT"],
  "mindiff": 500000,
  "startdiff": 500000,
  "maxdiff": 0,
  "asicboost": true,
  "logdir": "/var/lib/ckpool/logs",
  "sockdir": "/run/ckpool"
}
EOF
exec ckpool -B -c /etc/ckpool/ckpool.conf
