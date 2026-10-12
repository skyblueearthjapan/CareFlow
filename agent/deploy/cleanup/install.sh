#!/usr/bin/env bash
# 片づけのタイマーを入れる（1 コマンド・root・ホストで）。既定ではタイマーを「有効にしない」:
#   1) systemd の unit を /etc/systemd/system に置き、daemon-reload
#   2) 両方の片づけを dry-run で 1 回動かし、消す候補の一覧を /var/log/carelink/*-dryrun-<UTC>.txt に保存
#   3) 一覧を director が見て了承したら、--enable で有効にする（入れ直しても安全・冪等）
#
#   bash /opt/carelink/agent/deploy/cleanup/install.sh            # 入れる＋dry-run の一覧（消さない）
#   bash /opt/carelink/agent/deploy/cleanup/install.sh --enable   # 了承の後: 3 つのタイマーを有効にする
#   bash /opt/carelink/agent/deploy/cleanup/install.sh --disable  # 止める（unit は残す）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TIMERS=(carelink-app-backup-prune.timer carelink-agent-cleanup.timer carelink-agent-diskguard.timer)
[ "$(id -u)" = 0 ] || { echo "root で動かしてください" >&2; exit 1; }

case "${1:-}" in
  --enable)
    systemctl enable --now "${TIMERS[@]}"
    systemctl list-timers --all | grep -E 'carelink-(app-backup|agent)' || true
    exit 0 ;;
  --disable)
    systemctl disable --now "${TIMERS[@]}" || true
    exit 0 ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 64 ;;
esac

install -m 0644 "${HERE}"/systemd/carelink-*.service "${HERE}"/systemd/carelink-*.timer /etc/systemd/system/
systemctl daemon-reload
mkdir -p /var/log/carelink
stamp=$(date -u +%Y%m%d-%H%M)
bash "${HERE}/prune-app-backups.sh" > "/var/log/carelink/app-backup-prune-dryrun-${stamp}.txt" 2>&1 || true
bash "${HERE}/agent-cleanup.sh"     > "/var/log/carelink/agent-cleanup-dryrun-${stamp}.txt" 2>&1 || true
echo "unit を置きました（タイマーはまだ無効）。dry-run の一覧:"
for f in "/var/log/carelink/app-backup-prune-dryrun-${stamp}.txt" "/var/log/carelink/agent-cleanup-dryrun-${stamp}.txt"; do
  echo "== ${f}"; tail -n 3 "${f}"
done
echo "一覧を見て了承したら: bash ${HERE}/install.sh --enable"
