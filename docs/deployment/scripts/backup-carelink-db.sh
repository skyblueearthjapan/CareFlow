#!/usr/bin/env bash
# CareLink daily PostgreSQL backup (Wave 5-B).
#
# Cron registration (manual, see docs/deployment/runbook.md Phase 5):
#   /etc/cron.d/carelink-backup
#   30 2 * * * root /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
#
# Behaviour:
#   - pg_dump --format=plain via `docker exec`, gzip-compressed.
#   - Output: /opt/carelink/backups/daily-YYYYMMDD-HHMM.sql.gz
#   - Sanity check: dump must be >= 10 KB (smaller is treated as failure).
#   - Retention: deletes daily-*.sql.gz older than 7 days.
#   - VisitPhoto bind mount (/opt/carelink/data/visit_photos) is rsync'd into
#     a sibling 'visit_photos/' directory under BACKUP_DIR (size-bounded
#     incremental copy; large datasets should switch to a dedicated object
#     store later -- see runbook).
#   - All output appended to /var/log/carelink/backup.log.
#
# Exit codes:
#   0 - backup file written and >= MIN_SIZE_BYTES
#   1 - dump command failed or output too small

set -u
set -o pipefail

# --- Configuration ---------------------------------------------------------
BACKUP_DIR="/opt/carelink/backups"
LOG_DIR="/var/log/carelink"
LOG_FILE="${LOG_DIR}/backup.log"
DB_CONTAINER="carelink-postgres"
DB_USER="carelink"
DB_NAME="carelink"
MIN_SIZE_BYTES=10240   # 10 KB
RETENTION_DAYS=7
PHOTO_SRC="/opt/carelink/data/visit_photos"
PHOTO_DEST="${BACKUP_DIR}/visit_photos"
NOTIFY_SCRIPT="/opt/carelink/docs/deployment/scripts/notify-failure.sh"

mkdir -p "${BACKUP_DIR}" "${LOG_DIR}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(ts)" "$1" | tee -a "${LOG_FILE}" >/dev/null; }

stamp=$(date '+%Y%m%d-%H%M')
BACKUP_FILE="${BACKUP_DIR}/daily-${stamp}.sql.gz"

log "START daily backup -> ${BACKUP_FILE}"

# --- 1) Dump --------------------------------------------------------------
# Pipefail ensures docker/pg_dump failure surfaces even with `| gzip`.
if ! docker exec "${DB_CONTAINER}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" 2>>"${LOG_FILE}" \
     | gzip -9 > "${BACKUP_FILE}"; then
  log "FAIL pg_dump exited non-zero (file may be partial: ${BACKUP_FILE})"
  rm -f "${BACKUP_FILE}"
  if [ -x "${NOTIFY_SCRIPT}" ]; then
    "${NOTIFY_SCRIPT}" "carelink backup pg_dump failed at ${stamp}" >> "${LOG_FILE}" 2>&1 || true
  fi
  exit 1
fi

# --- 2) Sanity-check size --------------------------------------------------
size_bytes=$(stat -c '%s' "${BACKUP_FILE}" 2>/dev/null || echo 0)
if [ "${size_bytes}" -lt "${MIN_SIZE_BYTES}" ]; then
  log "FAIL backup too small: ${size_bytes} bytes (< ${MIN_SIZE_BYTES})"
  rm -f "${BACKUP_FILE}"
  if [ -x "${NOTIFY_SCRIPT}" ]; then
    "${NOTIFY_SCRIPT}" "carelink backup file undersized (${size_bytes} bytes) at ${stamp}" \
      >> "${LOG_FILE}" 2>&1 || true
  fi
  exit 1
fi
log "OK dump size=${size_bytes} bytes"

# --- 3) Photo bind-mount sync (best-effort) -------------------------------
if [ -d "${PHOTO_SRC}" ]; then
  mkdir -p "${PHOTO_DEST}"
  if command -v rsync >/dev/null 2>&1; then
    if rsync -a --delete "${PHOTO_SRC}/" "${PHOTO_DEST}/" 2>>"${LOG_FILE}"; then
      photo_size=$(du -sb "${PHOTO_DEST}" 2>/dev/null | awk '{print $1}')
      log "OK photo rsync size=${photo_size:-unknown} bytes"
    else
      log "WARN photo rsync exited non-zero (continuing; DB backup OK)"
    fi
  else
    log "WARN rsync not installed; skipping photo sync"
  fi
else
  log "INFO photo source ${PHOTO_SRC} not present; skipping"
fi

# --- 4) Retention ---------------------------------------------------------
deleted=$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'daily-*.sql.gz' \
            -mtime "+${RETENTION_DAYS}" -print -delete 2>/dev/null | wc -l)
log "OK retention deleted=${deleted} files older than ${RETENTION_DAYS}d"

log "END daily backup file=${BACKUP_FILE} size=${size_bytes}"
exit 0
