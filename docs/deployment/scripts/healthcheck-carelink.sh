#!/usr/bin/env bash
# CareLink production healthcheck (Wave 5-B).
#
# Runs from systemd cron every 5 minutes; pings backend + frontend healthz
# endpoints and verifies that all 3 carelink-* docker containers are reported
# as "Up" with healthy state. Failures append to /var/log/carelink/healthcheck.log
# and (after 3 consecutive failures) trigger notify-failure.sh, which dispatches
# a webhook alert if NOTIFY_WEBHOOK_URL is configured.
#
# Cron registration (manual, see docs/deployment/runbook.md Phase 5):
#   /etc/cron.d/carelink-healthcheck
#   */5 * * * * root /opt/carelink/docs/deployment/scripts/healthcheck-carelink.sh
#
# Exit codes:
#   0 - all checks passed
#   1 - at least one check failed (still records to log; notify-failure.sh
#       handles the consecutive-failure threshold via state file)
#
# Conventions:
#   - POSIX-ish bash; passes shellcheck with default settings.
#   - All paths absolute; never relies on cwd (cron has no working dir).
#   - stdout/stderr is suppressed by cron, so we always log to file.

set -u
set -o pipefail

# --- Configuration ---------------------------------------------------------
LOG_DIR="/var/log/carelink"
LOG_FILE="${LOG_DIR}/healthcheck.log"
STATE_DIR="/var/lib/carelink"
STATE_FILE="${STATE_DIR}/healthcheck.failcount"
NOTIFY_THRESHOLD=3   # consecutive failures before alert fires
NOTIFY_SCRIPT="/opt/carelink/docs/deployment/scripts/notify-failure.sh"
CURL_TIMEOUT=5

BACKEND_HEALTHZ="http://127.0.0.1:18001/api/v1/healthz"
FRONTEND_HEALTHZ="http://127.0.0.1:18000/api/healthz"
EXPECTED_CONTAINERS=(carelink-postgres carelink-backend carelink-frontend)

# --- Bootstrap log/state dirs (idempotent) ---------------------------------
mkdir -p "${LOG_DIR}" "${STATE_DIR}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(ts)" "$1" >> "${LOG_FILE}"; }

failures=()

# --- 1) Backend healthz ----------------------------------------------------
backend_code=$(curl -sS --max-time "${CURL_TIMEOUT}" -o /dev/null -w '%{http_code}' "${BACKEND_HEALTHZ}" 2>/dev/null || echo "000")
if [ "${backend_code}" != "200" ]; then
  failures+=("backend healthz endpoint=${BACKEND_HEALTHZ} code=${backend_code}")
fi

# --- 2) Frontend healthz ---------------------------------------------------
frontend_code=$(curl -sS --max-time "${CURL_TIMEOUT}" -o /dev/null -w '%{http_code}' "${FRONTEND_HEALTHZ}" 2>/dev/null || echo "000")
if [ "${frontend_code}" != "200" ]; then
  failures+=("frontend healthz endpoint=${FRONTEND_HEALTHZ} code=${frontend_code}")
fi

# --- 3) Container status (must be Up + healthy) ----------------------------
# `docker ps --filter name=carelink-` returns lines like
# "carelink-backend|Up 2 hours (healthy)"
# We check each expected container individually so that a missing one is
# detected (filter alone would silently omit it).
ps_output=$(docker ps --filter "name=carelink-" --format '{{.Names}}|{{.Status}}' 2>/dev/null || true)
for c in "${EXPECTED_CONTAINERS[@]}"; do
  line=$(printf '%s\n' "${ps_output}" | awk -F'|' -v n="${c}" '$1==n {print $0; exit}')
  if [ -z "${line}" ]; then
    failures+=("container ${c} not running")
    continue
  fi
  status="${line#*|}"
  case "${status}" in
    Up*\(healthy\)*) ;;  # OK
    Up*\(starting\)*) failures+=("container ${c} still starting: ${status}") ;;
    Up*) failures+=("container ${c} up but missing healthy state: ${status}") ;;
    *)   failures+=("container ${c} not Up: ${status}") ;;
  esac
done

# --- Result ----------------------------------------------------------------
if [ "${#failures[@]}" -eq 0 ]; then
  # Reset failcount on success.
  : > "${STATE_FILE}"
  log "OK backend=${backend_code} frontend=${frontend_code} containers=3/3 healthy"
  exit 0
fi

# Failure path: append all failure lines, bump fail counter, alert on threshold.
fc=0
if [ -s "${STATE_FILE}" ]; then
  fc=$(cat "${STATE_FILE}" 2>/dev/null || echo 0)
fi
fc=$((fc + 1))
printf '%d\n' "${fc}" > "${STATE_FILE}"

for f in "${failures[@]}"; do
  log "FAIL count=${fc} ${f}"
done

if [ "${fc}" -ge "${NOTIFY_THRESHOLD}" ] && [ -x "${NOTIFY_SCRIPT}" ]; then
  summary=$(printf '%s; ' "${failures[@]}")
  "${NOTIFY_SCRIPT}" "carelink healthcheck failed ${fc} consecutive runs: ${summary%; }" \
    >> "${LOG_FILE}" 2>&1 || log "WARN notify-failure.sh exited non-zero"
fi

exit 1
