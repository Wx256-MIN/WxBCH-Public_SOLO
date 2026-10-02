#!/bin/sh
set -eu
DATA_DIR=/root/.bitcoin
SHARED=/run/wxbch
SETTINGS="$SHARED/node-settings.conf"
mkdir -p "$DATA_DIR" "$SHARED"

if [ ! -f "$SHARED/rpc.env" ]; then
  RPC_PASSWORD="$(cat /proc/sys/kernel/random/uuid | tr -d '-')$(cat /proc/sys/kernel/random/uuid | tr -d '-')"
  umask 077
  printf '%s\n' "BCH_RPC_USER=wxbchpool" "BCH_RPC_PASSWORD=$RPC_PASSWORD" > "$SHARED/rpc.env"
fi
. "$SHARED/rpc.env"

if [ ! -f "$SETTINGS" ]; then
  umask 077
  printf '%s\n' "PRUNE_MB=0" > "$SETTINGS"
fi
. "$SETTINGS"

case "${PRUNE_MB:-0}" in
  0) PRUNE_LINE="prune=0" ;;
  550|[1-9][0-9][0-9][0-9]*)
    if [ "$PRUNE_MB" -lt 550 ]; then
      PRUNE_LINE="prune=0"
    else
      PRUNE_LINE="prune=$PRUNE_MB"
    fi
    ;;
  *) PRUNE_LINE="prune=0" ;;
esac

cat > "$DATA_DIR/bitcoin.conf" <<EOF
server=1
daemon=0
listen=1
bind=0.0.0.0
port=8333
rpcbind=0.0.0.0
rpcport=8332
rpcuser=$BCH_RPC_USER
rpcpassword=$BCH_RPC_PASSWORD
rpcallowip=0.0.0.0/0
zmqpubhashblock=tcp://0.0.0.0:28332
dbcache=512
maxconnections=64
$PRUNE_LINE
EOF

exec bitcoind -datadir="$DATA_DIR" -printtoconsole
