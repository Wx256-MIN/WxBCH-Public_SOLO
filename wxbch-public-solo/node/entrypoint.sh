#!/bin/sh
set -eu
DATA_DIR=/root/.bitcoin
SHARED=/run/wxbch
mkdir -p "$DATA_DIR" "$SHARED"
if [ ! -f "$SHARED/rpc.env" ]; then RPC_PASSWORD="$(cat /proc/sys/kernel/random/uuid | tr -d '-')$(cat /proc/sys/kernel/random/uuid | tr -d '-')"; umask 077; printf '%s\n' "BCH_RPC_USER=wxbchpool" "BCH_RPC_PASSWORD=$RPC_PASSWORD" > "$SHARED/rpc.env"; fi
. "$SHARED/rpc.env"
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
EOF
exec bitcoind -datadir="$DATA_DIR" -printtoconsole
