#!/usr/bin/env bash
# らく助エージェントの保存期間の片づけ（PO 決定 2026-10-12・agent/relay/retention.py の決まり）。
#   反映前の pg_dump（agent-*.sql.gz）14 日・新しい 5 個は残す／CLI の会話の写し・作業フォルダー 7 日／
#   会話・イベント 90 日／案・実行の記録 1 年／作業の記録（journal）は消さない。
#
# 使い方（root・ホストで）:
#   bash agent-cleanup.sh                    # 表示だけ（何を消すか・大きさ）
#   bash agent-cleanup.sh --apply            # 消す（件数・バイト数を journal とログへ）
#   bash agent-cleanup.sh --if-low-disk      # 空きが 10GB 未満のときだけ、早めに --apply で動かす（毎時のタイマー）
#   --env stg                                # staging（コンテナ stg-agent*・/opt/carelink/agent-staging/backups）
# ログ: /var/log/carelink/agent-cleanup.log
set -u
set -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/var/log/carelink/agent-cleanup.log"
# shellcheck source=common.sh
. "${HERE}/common.sh"

APPLY=""; LOWDISK=0; ENV=prod
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY="--apply" ;;
    --if-low-disk) LOWDISK=1 ;;
    --env) ENV="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
if [ "${ENV}" = "stg" ]; then
  PREFIX=stg; AGENT_BACKUP_DIRS=("/opt/carelink/agent-staging/backups")
else
  PREFIX=carelink; AGENT_BACKUP_DIRS=("/opt/carelink/backups/agent" "/opt/carelink/backups")
fi

exec 9>"/run/lock/carelink-agent-cleanup-${ENV}.lock"
flock -n 9 || { echo "another cleanup is running" >&2; exit 0; }

TRIGGER=scheduled
if [ "${LOWDISK}" = 1 ]; then
  free_is_low || exit 0          # 空きが足りていれば何もしない（ログも書かない）
  APPLY="--apply"; TRIGGER=low-disk
  log "LOW DISK (< ${MIN_FREE_GB} GB free) -> running cleanup early"
fi

J="$(journal_path "${PREFIX}")"
COMMON=(--journal "${J}" --trigger "${TRIGGER}")
[ -n "${APPLY}" ] && COMMON+=(--apply)
log "START agent cleanup env=${ENV} mode=${APPLY:-dry-run} journal=${J}"
rc=0

# 1) 反映前の pg_dump（置き場所ごとに新しい 5 個は残す）
dirs=(); for d in "${AGENT_BACKUP_DIRS[@]}"; do [ -d "$d" ] && dirs+=(--dir "$d"); done
if [ ${#dirs[@]} -gt 0 ]; then
  "${PY}" -I "${RETENTION}" "${COMMON[@]}" agent-backups "${dirs[@]}" | tee -a "${LOG_FILE}" || rc=1
fi

# 2) 中継: 案・実行の記録（1 年）・帳票と作業フォルダー（7 日）・会話の記録 DB（90 日／1 年）
if running "${PREFIX}-agent"; then
  RELAY_ARGS=(--journal /data/journal/work-journal.jsonl --trigger "${TRIGGER}"); [ -n "${APPLY}" ] && RELAY_ARGS+=(--apply)
  docker exec "${PREFIX}-agent" python -m relay.retention "${RELAY_ARGS[@]}" relay \
    | tee -a "${LOG_FILE}" || rc=1
else
  log "SKIP relay (${PREFIX}-agent is not running)"
fi

# 3) 箱: CLI の会話の写し（~/.claude/projects・~/.codex/sessions・log）と作業フォルダー（/data/sessions）7 日
if running "${PREFIX}-agent-sandbox"; then
  SB_ARGS=(--json --trigger "${TRIGGER}"); [ -n "${APPLY}" ] && SB_ARGS+=(--apply)
  out=$(docker exec "${PREFIX}-agent-sandbox" python3 -I /srv/agent/relay/retention.py "${SB_ARGS[@]}" \
        sandbox --home /home/agent --scratch /data/sessions) || rc=1
  printf '%s\n' "${out}" | "${PY}" -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print("[sandbox]", "dry-run" if d.get("dry_run") else "deleted", json.dumps(d.get("deleted")), "\n" + "\n".join("  %s\t%s\t%s" % (i["size"], i["ts"], i["path"]) for i in d.get("items", [])))' \
    | tee -a "${LOG_FILE}"
  printf '%s\n' "${out}" | "${PY}" -I "${RETENTION}" --journal "${J}" --trigger "${TRIGGER}" journal-append || rc=1
else
  log "SKIP sandbox (${PREFIX}-agent-sandbox is not running)"
fi

fix_journal_owner "${J}"
log "END agent cleanup rc=${rc}"
exit "${rc}"
