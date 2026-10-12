#!/usr/bin/env bash
# ディスクの見張り（毎時）: 空きが 10GB 未満なら片づけを早めに動かす。足りていれば何もしない。
# アプリのバックアップの片づけは、そのタイマーが有効（=最初の dry-run を見て了承済み）のときだけ早める。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/var/log/carelink/agent-cleanup.log"
# shellcheck source=common.sh
. "${HERE}/common.sh"
free_is_low || exit 0
log "DISKGUARD free < ${MIN_FREE_GB} GB"
bash "${HERE}/agent-cleanup.sh" --if-low-disk
if systemctl is-enabled --quiet carelink-app-backup-prune.timer 2>/dev/null; then
  bash "${HERE}/prune-app-backups.sh" --apply --trigger low-disk
fi
J="$(journal_path carelink)"
"${PY}" -I "${RETENTION}" disk --path "${REPO}" --min-free-gb "${MIN_FREE_GB}" --journal "${J}" --trigger after-cleanup \
  >> "${LOG_FILE}" 2>&1
fix_journal_owner "${J}"
exit 0
