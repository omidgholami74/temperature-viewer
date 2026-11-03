#!/usr/bin/env bash
# Smoke test for the combined single-origin deployment.
#
#   bash fullstack/tests/smoke.sh
#
# Uses a temporary database and port. No real data is touched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHP_BIN="${PHP_BIN:-php}"
PORT="${PORT:-18090}"
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
  tail -30 "${LOG}" >&2 || true
  exit 1
}

if ! command -v "${PHP_BIN}" >/dev/null 2>&1; then
  echo "SKIP: ${PHP_BIN} is not installed; cannot run the fullstack smoke test." >&2
  exit 2
fi

echo "Preparing a temporary database"
FURNACE_DB="${TMP_DIR}/test.sqlite" "${PHP_BIN}" "${ROOT}/backend/bin/install.php" >/dev/null

echo "Starting the combined server on ${BASE}"
FURNACE_DB="${TMP_DIR}/test.sqlite" \
FURNACE_INGEST_KEY="" \
FURNACE_ALLOWED_ORIGINS="${BASE}" \
"${PHP_BIN}" -S "127.0.0.1:${PORT}" -t "${ROOT}/fullstack" "${ROOT}/fullstack/index.php" >"${LOG}" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if curl -fsS -m 1 -o /dev/null "${BASE}/api/health" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

curl -fsS -m 5 "${BASE}/api/health" | grep -q '"ok":true' \
  || fail "health endpoint did not return ok"

echo "Checking dashboard and same-origin configuration"
DASHBOARD="$(curl -fsS -m 5 "${BASE}/")"
grep -q "Furnace Monitor" <<<"${DASHBOARD}" || fail "dashboard HTML is missing"
grep -q "apiBaseUrl: ''" "${ROOT}/fullstack/config.js" \
  || fail "combined config is not same-origin"

CONFIG="$(curl -fsS -m 5 "${BASE}/config.js")"
grep -q "apiBaseUrl: ''" <<<"${CONFIG}" \
  || fail "served config.js is not same-origin"

echo "Checking static assets and cache headers"
ASSET_HEADERS="$(curl -fsS -m 5 -D - -o /dev/null "${BASE}/assets/js/main.js")"
grep -qi '^Content-Type: text/javascript' <<<"${ASSET_HEADERS}" \
  || fail "main.js is not served as JavaScript"
grep -qi '^Content-Security-Policy:' <<<"${ASSET_HEADERS}" \
  || fail "static response is missing CSP"

echo "Checking ingest, live and history on one origin"
PAYLOAD="$("${PHP_BIN}" -r '
  $samples = [];
  for ($i = 0; $i < 10; $i++) {
      $samples[] = ["u" => $i * 100, "t" => [700 + $i * 0.5, 710, 720, 730], "f" => [0, 0, 0, 0]];
  }
  echo json_encode(["samples" => $samples]);
')"

INGEST="$(curl -fsS -m 5 -X POST \
  -H "Content-Type: application/json" \
  --data "${PAYLOAD}" \
  "${BASE}/api/ingest")"
grep -q '"accepted":10' <<<"${INGEST}" || fail "ingest did not accept 10 samples"

LIVE="$(curl -fsS -m 5 "${BASE}/api/live")"
grep -q '"latest"' <<<"${LIVE}" || fail "live response has no latest sample"
grep -q '"id":10' <<<"${LIVE}" || fail "live response did not include the ingested samples"

HISTORY="$(curl -fsS -m 5 "${BASE}/api/history?window=600&max_points=600")"
grep -q '"points"' <<<"${HISTORY}" || fail "history response has no points"

echo "Checking same-origin writes"
RESET="$(curl -fsS -m 5 -X POST \
  -H "Origin: ${BASE}" \
  -H "Content-Type: application/json" \
  --data '{}' \
  "${BASE}/api/runs/reset")"
grep -q '"ok":true' <<<"${RESET}" \
  || fail "same-origin run reset was rejected"

echo "Checking path traversal is refused"
TRAVERSAL_STATUS="$(curl -sS --path-as-is -m 5 -o /dev/null -w '%{http_code}' \
  "${BASE}/assets/%2e%2e/%2e%2e/backend/config.php")"
[[ "${TRAVERSAL_STATUS}" == "404" ]] \
  || fail "path traversal should return 404, got ${TRAVERSAL_STATUS}"

echo "OK: fullstack smoke test passed"
