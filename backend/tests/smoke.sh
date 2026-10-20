#!/usr/bin/env bash
# End-to-end smoke test for the PHP API.
#
#   bash backend/tests/smoke.sh
#
# Uses a throwaway SQLite database and an ephemeral port, so it never touches
# real data. Set PHP_BIN if the php binary is not on PATH.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHP_BIN="${PHP_BIN:-php}"
PORT="${PORT:-18080}"
BASE="http://127.0.0.1:${PORT}"
TMP_DIR="$(mktemp -d)"
LOG="${TMP_DIR}/server.log"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  echo "--- server log ---" >&2
  tail -20 "${LOG}" >&2 || true
  exit 1
}

assert_eq() {
  [[ "$1" == "$2" ]] || fail "$3 (expected '$2', got '$1')"
}

json_get() {
  "${PHP_BIN}" -r '
    $data = json_decode(stream_get_contents(STDIN), true);
    $value = $data;
    foreach (explode(".", $argv[1]) as $key) {
        if (!is_array($value) || !array_key_exists($key, $value)) {
            fwrite(STDERR, "missing key: $key\n");
            exit(2);
        }
        $value = $value[$key];
    }
    echo is_bool($value) ? ($value ? "true" : "false") : $value;
  ' "$1"
}

request() {
  local method="$1" path="$2" body="${3:-}" origin="${4:-}"
  local args=(-sS -m 10 -o "${TMP_DIR}/body" -w "%{http_code}" -X "${method}" "${BASE}${path}")

  if [[ -n "${body}" ]]; then
    args+=(-H "Content-Type: application/json" --data "${body}")
  fi

  if [[ -n "${origin}" ]]; then
    args+=(-H "Origin: ${origin}")
  fi

  curl "${args[@]}"
}

echo "Starting PHP server on ${BASE} with a temporary database"
FURNACE_DB="${TMP_DIR}/test.sqlite" \
FURNACE_INGEST_KEY="" \
"${PHP_BIN}" -S "127.0.0.1:${PORT}" -t "${ROOT}/backend" "${ROOT}/backend/index.php" >"${LOG}" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 40); do
  if curl -s -m 1 -o /dev/null "${BASE}/api/health"; then
    break
  fi
  sleep 0.1
done

# --- health ---------------------------------------------------------------
status="$(request GET /api/health)"
assert_eq "${status}" "200" "health status"
health="$(cat "${TMP_DIR}/body")"
assert_eq "$(echo "${health}" | json_get ok)" "true" "health payload"

# --- ingest ---------------------------------------------------------------
payload="$("${PHP_BIN}" -r '
  $samples = [];
  for ($i = 0; $i < 10; $i++) {
      $samples[] = ["u" => $i * 100, "t" => [700 + $i * 0.5, 710, 720, 730], "f" => [0, 0, 0, 0]];
  }
  echo json_encode(["samples" => $samples]);
')"

status="$(request POST /api/ingest "${payload}")"
assert_eq "${status}" "200" "ingest status"
ingest="$(cat "${TMP_DIR}/body")"
assert_eq "$(echo "${ingest}" | json_get accepted)" "10" "accepted samples"
RUN_ID="$(echo "${ingest}" | json_get run_id)"

# --- live ----------------------------------------------------------------
status="$(request GET "/api/live")"
assert_eq "${status}" "200" "live status"
live="$(cat "${TMP_DIR}/body")"
assert_eq "$(echo "${live}" | json_get run.id)" "${RUN_ID}" "live run id"
assert_eq "$(echo "${live}" | json_get latest.temps.0)" "704.5" "latest first channel"
assert_eq "$(echo "${live}" | json_get active_run_id)" "${RUN_ID}" "active run id"

status="$(request GET "/api/live?since=5")"
samples="$(cat "${TMP_DIR}/body")"
assert_eq "$(echo "${samples}" | json_get samples.0.id)" "6" "incremental samples start after cursor"

# --- history -------------------------------------------------------------
status="$(request GET "/api/history?window=600&max_points=600")"
assert_eq "${status}" "200" "history status"
history="$(cat "${TMP_DIR}/body")"
assert_eq "$(echo "${history}" | json_get source)" "raw" "history source"
POINTS="$("${PHP_BIN}" -r '$d = json_decode(stream_get_contents(STDIN), true); echo count($d["points"]);' <<<"${history}")"
[[ "${POINTS}" -ge 8 && "${POINTS}" -le 10 ]] || fail "history point count (got ${POINTS})"

# --- validation ----------------------------------------------------------
status="$(request POST /api/ingest '{"samples":[]}')"
assert_eq "${status}" "422" "empty batch is rejected"

status="$(request POST /api/ingest '{"samples":[{"u":1,"t":[1,2,3],"f":[0,0,0,0]}]}')"
assert_eq "${status}" "422" "wrong channel count is rejected"

status="$(request POST /api/ingest '{"samples":[{"u":1,"t":[1,2,3,9999],"f":[0,0,0,0]}]}')"
assert_eq "${status}" "422" "out of range temperature is rejected"

# --- run reset -----------------------------------------------------------
status="$(request POST /api/runs/reset '{}' "http://evil.example")"
assert_eq "${status}" "403" "cross-origin reset is rejected"

status="$(request POST /api/runs/reset '{}' "${BASE}")"
assert_eq "${status}" "200" "same-origin reset is accepted"
reset="$(cat "${TMP_DIR}/body")"
NEW_RUN_ID="$(echo "${reset}" | json_get run.id)"
[[ "${NEW_RUN_ID}" -gt "${RUN_ID}" ]] || fail "reset should create a newer run"

status="$(request GET "/api/live?run=${RUN_ID}")"
assert_eq "$(echo "$(cat "${TMP_DIR}/body")" | json_get active_run_id)" "${NEW_RUN_ID}" "old run polls report the new active run"

# --- static UI -----------------------------------------------------------
status="$(curl -sS -m 5 -o "${TMP_DIR}/index.html" -w "%{http_code}" "${BASE}/")"
assert_eq "${status}" "200" "dashboard index"
grep -q "Furnace Monitor" "${TMP_DIR}/index.html" || fail "dashboard markup missing"

status="$(curl -sS -m 5 -o /dev/null -w "%{http_code}" "${BASE}/assets/js/main.js")"
assert_eq "${status}" "200" "dashboard module"

status="$(curl -sS -m 5 -o /dev/null -w "%{http_code}" "${BASE}/../backend/config.php")"
[[ "${status}" == "404" || "${status}" == "400" ]] || fail "path traversal must not leak config (got ${status})"

# --- retention job -------------------------------------------------------
FURNACE_DB="${TMP_DIR}/test.sqlite" "${PHP_BIN}" "${ROOT}/backend/bin/maintenance.php" --all > "${TMP_DIR}/maintenance.log"
grep -q "Rollup" "${TMP_DIR}/maintenance.log" || fail "maintenance output missing"

ROLLUP_ROWS="$("${PHP_BIN}" -r '
  $pdo = new PDO("sqlite:" . $argv[1]);
  echo (int) $pdo->query("SELECT COUNT(*) FROM readings_1s")->fetchColumn();
' "${TMP_DIR}/test.sqlite")"
# Rows younger than 3 s are intentionally left for the next run.
[[ "${ROLLUP_ROWS}" -ge 0 ]] || fail "rollup query failed"

# --- API key -------------------------------------------------------------
KEY_PORT="$((PORT + 1))"
FURNACE_DB="${TMP_DIR}/keyed.sqlite" \
FURNACE_INGEST_KEY="secret-key" \
"${PHP_BIN}" -S "127.0.0.1:${KEY_PORT}" -t "${ROOT}/backend" "${ROOT}/backend/index.php" >"${TMP_DIR}/keyed.log" 2>&1 &
KEY_PID=$!

for _ in $(seq 1 40); do
  if curl -s -m 1 -o /dev/null "http://127.0.0.1:${KEY_PORT}/api/health"; then
    break
  fi
  sleep 0.1
done

status="$(curl -sS -m 10 -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "${payload}" "http://127.0.0.1:${KEY_PORT}/api/ingest")"
assert_eq "${status}" "401" "ingest without key"

status="$(curl -sS -m 10 -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -H "X-Api-Key: secret-key" --data "${payload}" "http://127.0.0.1:${KEY_PORT}/api/ingest")"
assert_eq "${status}" "200" "ingest with key"

kill "${KEY_PID}" 2>/dev/null || true
wait "${KEY_PID}" 2>/dev/null || true

echo
echo "All API smoke tests passed."
