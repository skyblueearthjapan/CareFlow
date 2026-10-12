#!/usr/bin/env bash
# アプリの既存バックアップ（/opt/carelink/backups）の片づけ（PO 了承 2026-10-12）。
#   daily-*.sql.gz             30 日＋各月の最初の 1 個を 12 か月（今月を含む）
#   pre-deploy-*・pre-*（.sql/.sql.gz）  60 日
#   種類ごと（daily・pre-deploy・pre）に新しい 3 個は必ず残す
#   agent-* は触らない（agent-cleanup.sh の決まり: 14 日・新しい 5 個）
#   それ以外（pre_*・predeploy_*・carelink_pre_*・env-*・フォルダー visit_photos/visit_signatures など）は触らない
#
# 使い方（root・ホストで）:
#   bash prune-app-backups.sh            # 表示だけ（消す候補・大きさ・理由）
#   bash prune-app-backups.sh --apply    # 消す（件数・バイト数を journal とログへ）
# ログ: /var/log/carelink/app-backup-prune.log（お客様の画面・エージェントには出さない）
#
# 注意: 毎日のバックアップ（docs/deployment/scripts/backup-carelink-db.sh）が daily-* を 7 日で消している
#       （RETENTION_DAYS=7）。30 日＋月初を残すには、そちらの 7 日の削除を止める必要がある（README §10）。
set -u
set -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/var/log/carelink/app-backup-prune.log"
# shellcheck source=common.sh
. "${HERE}/common.sh"

APPLY=""; TRIGGER=scheduled; DIR="/opt/carelink/backups"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY="--apply" ;;
    --trigger) TRIGGER="$2"; shift ;;
    --dir) DIR="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
[ -d "${DIR}" ] || { echo "no such dir: ${DIR}" >&2; exit 1; }

exec 9>/run/lock/carelink-app-backup-prune.lock
flock -n 9 || { echo "another prune is running" >&2; exit 0; }

J="$(journal_path carelink)"
ARGS=(--journal "${J}" --trigger "${TRIGGER}"); [ -n "${APPLY}" ] && ARGS+=(--apply)
log "START app backup prune dir=${DIR} mode=${APPLY:-dry-run}"
"${PY}" -I "${RETENTION}" "${ARGS[@]}" app-backups --dir "${DIR}" | tee -a "${LOG_FILE}"
rc=$?
fix_journal_owner "${J}"
log "END app backup prune rc=${rc}"
exit "${rc}"
