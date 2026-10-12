# らく助エージェントの片づけ（ホストの root が動かす）の共通部分。source して使う。
# 既定は表示だけ（dry-run）。消すのは --apply のときだけ。時刻はすべて UTC。
# お客様（アプリの画面・エージェント）には一切出さない: 記録はこのログと作業の記録（journal）だけ。

REPO="${CARELINK_REPO:-/opt/carelink}"
RETENTION="${REPO}/agent/relay/retention.py"
LOG_DIR="/var/log/carelink"
PY="${PYTHON:-python3}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"
mkdir -p "${LOG_DIR}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "${LOG_FILE}"; }

# 作業の記録（journal）の置き場所: 中継の data volume の journal/work-journal.jsonl。
# volume がまだ無い（中継を立てる前）は /var/lib/carelink-agent/journal/ に書く（立てた後に足し合わせる・README §10）。
journal_path() {
  local prefix="$1" mp
  mp=$(docker volume inspect -f '{{.Mountpoint}}' "${prefix}-agent-relay-data" 2>/dev/null || true)
  if [ -n "${mp}" ] && [ -d "${mp}" ]; then
    install -d -o 10001 -g 10001 -m 0750 "${mp}/journal"
    echo "${mp}/journal/work-journal.jsonl"
  else
    install -d -m 0750 /var/lib/carelink-agent/journal
    echo "/var/lib/carelink-agent/journal/work-journal.jsonl"
  fi
}

# 書いた後に持ち主を中継（uid 10001）に戻す（中継も同じファイルに足すため）
fix_journal_owner() {
  case "$1" in /var/lib/docker/*) chown 10001:10001 "$1" 2>/dev/null || true ;; esac
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

free_is_low() {  # 終了コード 0 = 空きが MIN_FREE_GB 未満
  "${PY}" -I "${RETENTION}" disk --path "${REPO}" --min-free-gb "${MIN_FREE_GB}" > /dev/null 2>&1
  [ $? -eq 2 ]
}
