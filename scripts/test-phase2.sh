#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p .artifacts/phase2

restore_event() {
  docker compose exec -T mysql sh -c '
    MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 \
      -u root "$MYSQL_DATABASE" -Nse \
      "UPDATE supply_disruption_event SET is_current = TRUE WHERE event_id = '\''E01'\''"
  ' >/dev/null 2>&1 || true
}
trap restore_event EXIT INT TERM

sql_query() {
  docker compose exec -T mysql sh -c '
    MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 \
      -u "$MYSQL_USER" "$MYSQL_DATABASE" --default-character-set=utf8mb4 \
      --batch --raw --skip-column-names
  ' < queries/needs-review-orders.sql
}

set_event_current() {
  local value="$1"
  docker compose exec -T mysql sh -c "
    MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" mysql --protocol=TCP -h 127.0.0.1 \
      -u root \"\$MYSQL_DATABASE\" -Nse \
      \"UPDATE supply_disruption_event SET is_current = ${value} WHERE event_id = 'E01'\"
  "
}

run_reasoner() {
  docker compose --profile phase2 run --rm reasoner "$@"
}

docker compose up -d mysql
bash scripts/migrate-phase2.sh
docker compose --profile phase2 build reasoner
restore_event

echo "[1/4] 正常推理"
run_reasoner --expect one --expected-order 101 | tee .artifacts/phase2/normal.txt
grep -Fq '<https://example.org/event/E01> rdf:type proc:DeliverySuspensionEvent' .artifacts/phase2/normal.txt
grep -Fq '<https://example.org/event/E01> rdf:type proc:SupplyDisruptionEvent' .artifacts/phase2/normal.txt
grep -Fq 'R-01 命中: order=101, event=E01' .artifacts/phase2/normal.txt
normal_sql=$(sql_query)
[[ "$normal_sql" == $'101\t华北钢材\tE01' ]] || {
  echo "SQL normal mismatch: ${normal_sql:-<empty>}" >&2; exit 10;
}
echo "SQL 正常场景 PASS: ${normal_sql}"

echo "[2/4] 移除继承公理（基础事实与规则不变）"
run_reasoner --without-inheritance --expect none | tee .artifacts/phase2/no-inheritance.txt
grep -Fq '加载继承公理: False' .artifacts/phase2/no-inheritance.txt
grep -Fq '<https://example.org/event/E01> rdf:type proc:DeliverySuspensionEvent' .artifacts/phase2/no-inheritance.txt
grep -Fq 'R-01 未命中' .artifacts/phase2/no-inheritance.txt
echo "本体依赖实验 PASS"

echo "[3/4] 事件失效后全量重算"
set_event_current FALSE
supplier_status=$(docker compose exec -T mysql sh -c '
  MYSQL_PWD="$MYSQL_PASSWORD" mysql --protocol=TCP -h 127.0.0.1 \
    -u "$MYSQL_USER" "$MYSQL_DATABASE" -Nse \
    "SELECT supplier_status FROM supplier WHERE supplier_id = 1"
')
[[ "$supplier_status" == "DISABLED" ]] || {
  echo "Supplier status unexpectedly changed: ${supplier_status:-<empty>}" >&2; exit 13;
}
invalid_sql=$(sql_query)
[[ -z "$invalid_sql" ]] || { echo "SQL invalid-event expected empty: $invalid_sql" >&2; exit 11; }
run_reasoner --expect none | tee .artifacts/phase2/inactive-event.txt
echo "事件失效场景 SQL + 推理 PASS"

echo "[4/4] 恢复事件后全量重算"
set_event_current TRUE
restored_sql=$(sql_query)
[[ "$restored_sql" == $'101\t华北钢材\tE01' ]] || {
  echo "SQL restored mismatch: ${restored_sql:-<empty>}" >&2; exit 12;
}
run_reasoner --expect one --expected-order 101 | tee .artifacts/phase2/restored.txt
echo "Phase 2 ALL PASS"
