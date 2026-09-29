#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/bch-solo-pool"
[ -f .env ] || cp .env.example .env
echo "Local Docker setup:"
echo "  1. Edit .env with your BCHN/AxeBCH RPC and payout address."
echo "  2. Run: docker compose -f docker-compose.local.yml up -d --build"
echo "Umbrel users should add this repository as a Community App Store and configure the pool from its web setup page."
