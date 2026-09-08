#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v curl >/dev/null || { echo '需要 WSL 的 curl。' >&2; exit 1; }
command -v python3 >/dev/null || { echo '需要 Python 3 标准库解析 JSON（无需 pip）。' >&2; exit 1; }
mkdir -p .artifacts
endpoint="${ONTOP_ENDPOINT:-http://localhost:${ONTOP_PORT:-8080}/sparql}"

# Wait for the container's official healthcheck, with a bounded timeout.
ready=false
for ((attempt = 0; attempt < 90; attempt++)); do
    container_id=$(docker compose ps -aq ontop)
    if [[ -n "$container_id" ]]; then
        state=$(docker inspect --format '{{.State.Status}}' "$container_id")
        if [[ "$state" == exited || "$state" == dead ]]; then break; fi
        health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id")
        if [[ "$health" == healthy ]]; then ready=true; break; fi
        if [[ "$health" == unhealthy ]]; then break; fi
    fi
    sleep 2
done
if [[ "$ready" != true ]]; then
    echo 'Ontop 未就绪；最近容器日志：' >&2
    docker compose logs --tail=80 ontop mysql >&2
    exit 1
fi

curl --fail-with-body --silent --show-error --connect-timeout 5 --max-time 60 \
    -H 'Accept: application/sparql-results+json' \
    --data-urlencode 'query@queries/risky-orders.rq' \
    "$endpoint" > .artifacts/sparql.json

# Re-run SQL now, so comparison never uses an old result file.
bash scripts/test-sql.sh
python3 scripts/verify-results.py
