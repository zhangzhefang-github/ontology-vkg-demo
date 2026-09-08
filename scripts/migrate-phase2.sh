#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose exec -T mysql sh -c '
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 \
    -u root "$MYSQL_DATABASE" --default-character-set=utf8mb4
' < sql/migrate-phase2.sql

docker compose up -d --force-recreate ontop >/dev/null
echo "Phase 2 migration applied; Ontop recreated to reload database metadata."
