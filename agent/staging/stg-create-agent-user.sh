#!/usr/bin/env bash
# 試験環境にだけ、エージェント専用の管理者ユーザーを作る（本番には作らない）。
#
# 使い方（VPS の上で・root・stg-up.sh の後）:
#   bash stg-create-agent-user.sh [email]        # 既定 rakusuke-agent@example.com
#
# パスワードは標準出力に 1 回だけ出る。PC の ~/.rakusuke/credentials.json（または
# RK_IDENTIFIER / RK_PASSWORD）に写す。試験環境を消せばユーザーも消える。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMAIL="${1:-rakusuke-agent@example.com}"
C="stg-backend"

die() { printf '[stg-user] 中止: %s\n' "$*" >&2; exit 1; }

# 本番コンテナには絶対に入れない
[[ "${C}" == "stg-backend" ]] || die "対象は stg-backend だけです"
docker inspect "${C}" >/dev/null 2>&1 || die "${C} が動いていません。先に stg-up.sh"
[[ "$(docker inspect "${C}" --format '{{.State.Health.Status}}')" == "healthy" ]] || die "${C} が healthy ではありません"
nets=$(docker inspect "${C}" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}')
[[ "${nets}" == "carelink-stg " ]] || die "${C} が carelink-stg 以外の網に入っています (${nets})"

docker cp "${HERE}/stg_create_agent_user.py" "${C}:/tmp/stg_create_agent_user.py"
trap 'docker exec "${C}" rm -f /tmp/stg_create_agent_user.py >/dev/null 2>&1 || true' EXIT

echo "[stg-user] 試験環境に ${EMAIL}（admin）を作ります"
docker exec -w /app -e PYTHONPATH=/app "${C}" python /tmp/stg_create_agent_user.py "${EMAIL}"
echo "[stg-user] 上の password は 1 回だけ表示しています。rk 用に控えてください（試験環境専用）"
