#!/bin/bash
# 認証情報の隔離の通し試験（docker が要る。pytest には入れない）。
#   bash agent/deploy/tests/isolation_e2e.sh [image]      image 既定: carelink-agent-sandbox:latest（事前に build）
# compose と同じ守り（read_only・cap_drop ALL＋SETUID/SETGID/SETPCAP・no-new-privileges・同じ tmpfs）で箱を起こし、
# CLI の uid で isolation_driver.py を動かす。モデルのコマンドは uid 10004、ログイン情報（ダミー）は読めないことを確かめる。
set -eu
export MSYS_NO_PATHCONV=1
IMG="${1:-carelink-agent-sandbox:latest}"
NAME="isot-$$"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOSTDIR="$HERE"; command -v cygpath >/dev/null && HOSTDIR="$(cygpath -w "$HERE")"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" --read-only --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add SETPCAP \
  --security-opt no-new-privileges:true --init -u 0:0 \
  -e RUNNER_EXEC_SOCKET=/run/x/exec.sock -e RUNNER_SOCKET=/run/agent/runner.sock \
  --tmpfs /tmp:size=64m,mode=1777 \
  --tmpfs /run/x:size=1m,mode=2770,uid=10004,gid=10005 --tmpfs /home/agentx:size=64m,mode=0700,uid=10004,gid=10005 \
  --tmpfs /run/agent:size=1m,mode=0750,uid=10002,gid=10000 --tmpfs /home/agent:size=16m,mode=0700,uid=10002,gid=10000 \
  --tmpfs /data/sessions:size=64m,mode=0750,uid=10002,gid=10005 \
  -v "$HOSTDIR:/t:ro" "$IMG" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$NAME" test -S /run/x/exec.sock 2>/dev/null && docker exec "$NAME" test -S /run/agent/runner.sock 2>/dev/null && break
  sleep 0.5
done
# entrypoint.sh が runner を起こすのと同じ形（uid 10002・グループ 10005・capability なし）で試験を動かす
docker exec -e HOME=/home/agent -e RK_MODE=relay "$NAME" setpriv --reuid=10002 --regid=10000 --groups=10005 \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs python3 /t/isolation_driver.py
