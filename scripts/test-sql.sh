#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p .artifacts

docker compose exec -T mysql sh -c '
  MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 \
    -u "$MYSQL_USER" "$MYSQL_DATABASE" --default-character-set=utf8mb4 \
    --batch --raw --skip-column-names
' < queries/risky-orders.sql > .artifacts/sql.tsv

expected=$'101\t华北钢材\t50000.00'
actual=$(cat .artifacts/sql.tsv)
if [[ "$actual" != "$expected" ]]; then
    printf 'SQL FAIL：预期只有 101 / 华北钢材 / 50000.00，实际结果：\n' >&2
    cat .artifacts/sql.tsv >&2
    exit 1
fi
printf 'order_id\tsupplier_name\tamount\n%s\nSQL PASS（唯一结果：订单 101）\n' "$actual"
