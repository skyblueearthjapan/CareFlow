#!/bin/bash
# 箱の起動（root・必要な capability は SETUID/SETGID/SETPCAP だけ。compose で cap_drop ALL のうえ足す）。
# やること: 3 つの係を **別々の uid・capability なし・no-new-privs** で起こし、どれかが落ちたら全部止める。
#   uid 10002 agent  … runner（CLI を起こす）と CLI。ログイン情報（/home/agent）を持つ
#   uid 10004 agentx … execd（claude のコマンド）と codex exec-server（codex のコマンド）。ログイン情報は読めない
# この root のプロセスは何も待ち受けず、ファイルも触らない（子の親として生きているだけ）。
set -u
A_UID=10002; A_GID=10000; B_UID=10004; B_GID=10005
DROP=(--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs)
as_a() { setpriv --reuid=$A_UID --regid=$A_GID --groups=$B_GID "${DROP[@]}" "$@"; }
as_b() { setpriv --reuid=$B_UID --regid=$B_GID --clear-groups "${DROP[@]}" "$@"; }

# 会話のフォルダー: /data/sessions と各会話 d は agent 持ち 0750（グループ 10005 は入るだけ・消す／付け替えるは不可）。
# 実行係が書けるのは各 d/work の中だけ（2770）。--settings は /run/agent（実行係は入れない）に別途書く。
as_a sh -c "chgrp $B_GID /data/sessions; chmod 0750 /data/sessions;   for d in /data/sessions/*/; do [ -d \"\$d\" ] || continue; chgrp $B_GID \"\$d\"; chmod 0750 \"\$d\";     if [ -d \"\${d}work\" ] && [ ! -L \"\${d}work\" ]; then chgrp -R $B_GID \"\${d}work\"; chmod -R g+rwX,o-rwx \"\${d}work\"; fi; done; true" 2>/dev/null

as_b mkdir -p /home/agentx/.codex
pids=()
as_b env HOME=/home/agentx python3 /opt/runner/execd.py & pids+=($!)
# codex の実行係。app-server（agent）は CODEX_EXEC_SERVER_URL でここにだけ頼む（自分では走らせない）
as_b env HOME=/home/agentx CODEX_HOME=/home/agentx/.codex PATH=/srv/agent/bin:/usr/local/bin:/usr/bin:/bin \
  codex exec-server --listen ws://127.0.0.1:9911 & pids+=($!)
# 実行係の準備ができてから runner（healthcheck も同じ印を見る）
for _ in $(seq 1 50); do [ -S /run/x/exec.sock ] && break; sleep 0.1; done
as_a python3 /opt/runner/agent_runner.py & pids+=($!)

trap 'kill "${pids[@]}" 2>/dev/null; exit 0' TERM INT
wait -n
kill "${pids[@]}" 2>/dev/null
exit 1
