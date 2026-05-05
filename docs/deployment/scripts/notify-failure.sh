#!/usr/bin/env bash
# CareLink alert dispatcher (Wave 5-B).
#
# Called by healthcheck-carelink.sh / backup-carelink-db.sh on failure.
# If NOTIFY_WEBHOOK_URL is set in the environment (typically via /etc/cron.d/
# entry or /opt/carelink/.env exported by the calling script), POSTs a JSON
# payload compatible with both Slack and Discord generic webhooks.
#
# When the env var is unset, the script logs the message and exits 0 (noop)
# so callers do not have to special-case the unconfigured state.
#
# Usage:
#   notify-failure.sh "<message>"
#
# Email/sendmail integration is intentionally out of scope (no MTA on VPS);
# add a dedicated script if/when postfix is provisioned.

set -u
set -o pipefail

LOG_DIR="/var/log/carelink"
LOG_FILE="${LOG_DIR}/healthcheck.log"  # shared with healthcheck output
mkdir -p "${LOG_DIR}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s notify %s\n' "$(ts)" "$1" >> "${LOG_FILE}"; }

msg="${1:-carelink alert (no message provided)}"

# Try to source .env so cron-launched callers pick up NOTIFY_WEBHOOK_URL
# without having to maintain a duplicate copy in /etc/cron.d/. The .env is
# 600 root-only so this is safe.
if [ -z "${NOTIFY_WEBHOOK_URL:-}" ] && [ -r /opt/carelink/.env ]; then
  # shellcheck disable=SC1091
  set -a
  . /opt/carelink/.env
  set +a
fi

if [ -z "${NOTIFY_WEBHOOK_URL:-}" ]; then
  log "noop (NOTIFY_WEBHOOK_URL unset) msg=${msg}"
  exit 0
fi

# Both Slack incoming-webhook and Discord (with ?wait=true or generic JSON)
# accept {"text": "..."} as the payload. Generic relays (e.g. Mattermost,
# alertmanager-webhook-receiver) also tolerate it.
payload=$(printf '{"text":%s}' "$(printf '%s' "${msg}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "${msg//\"/\\\"}")")

http_code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
              -H 'Content-Type: application/json' \
              -d "${payload}" \
              "${NOTIFY_WEBHOOK_URL}" 2>>"${LOG_FILE}" || echo "000")

if [ "${http_code}" = "200" ] || [ "${http_code}" = "204" ]; then
  log "sent http=${http_code} msg=${msg}"
  exit 0
fi

log "WARN webhook POST failed http=${http_code} msg=${msg}"
exit 1
