#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/bch-solo-pool"
[ -f .env ] || cp .env.example .env
echo "Edit bch-solo-pool/.env and set BCH_RPC_URL, BCH_RPC_USER, BCH_RPC_PASSWORD and BCH_PAYOUT_ADDRESS."
echo "Then run: docker compose -f docker-compose.local.yml up -d --build"
