#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose up -d mysql
bash scripts/migrate-phase2.sh
docker compose --profile phase2 build reasoner
docker compose --profile phase2 run --rm reasoner
