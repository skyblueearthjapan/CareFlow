#!/usr/bin/env bash
# らく助 使い捨て試験環境を消す（コンテナ・網・volume・.env.staging）。本番には触らない。
#
# 使い方（VPS の上で・root）:  bash stg-down.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.staging.yml"
ENV_FILE="${HERE}/.env.staging"
PROJECT="carelink-stg"

# 消してよい名前はこれだけ（本番の carelink-postgres / carelink-backend / carelink-pg-data には触らない）
STG_CONTAINERS=(stg-backend stg-postgres)
STG_VOLUME="carelink-stg-pg"
STG_NETWORK="carelink-stg"

log() { printf '[stg-down] %s\n' "$*"; }

# 1. compose で落とす（.env.staging が無くても動くように image はダミーを渡す）
if [[ -f "${COMPOSE_FILE}" ]]; then
  env_args=()
  [[ -f "${ENV_FILE}" ]] && env_args=(--env-file "${ENV_FILE}")
  # env_file: ./.env.staging が無いと compose が止まるので、空のファイルを一時的に置く
  created_env=0
  if [[ ! -f "${ENV_FILE}" ]]; then : > "${ENV_FILE}"; chmod 600 "${ENV_FILE}"; created_env=1; fi
  STG_BACKEND_IMAGE="${STG_BACKEND_IMAGE:-carelink-backend:latest}" \
    docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" "${env_args[@]}" down -v --remove-orphans --timeout 20 \
    || log "compose down が失敗（下で個別に消します）"
  [[ "${created_env}" == "1" ]] && rm -f "${ENV_FILE}"
fi

# 2. 取りこぼしを名前で個別に消す
for c in "${STG_CONTAINERS[@]}"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "${c}"; then
    log "コンテナ ${c} を削除"
    docker rm -f -v "${c}" >/dev/null || true
  fi
done
if docker network inspect "${STG_NETWORK}" >/dev/null 2>&1; then
  log "network ${STG_NETWORK} を削除"
  docker network rm "${STG_NETWORK}" >/dev/null || true
fi
if docker volume inspect "${STG_VOLUME}" >/dev/null 2>&1; then
  log "volume ${STG_VOLUME} を削除"
  docker volume rm "${STG_VOLUME}" >/dev/null || true
fi

# 3. 試験用の秘密（試験 DB のパスワード・試験用 JWT 鍵）を消す
if [[ -f "${ENV_FILE}" ]]; then
  shred -u "${ENV_FILE}" 2>/dev/null || rm -f "${ENV_FILE}"
  log ".env.staging を削除"
fi
# 流し込みのログ（エラー時に利用者のデータの行が混じることがある）も消す
rm -f "${HERE}"/restore-*.log
# rk（RK_ENV=staging）のバックアップ（試験 DB の写し＝利用者のデータ入り）も消す
if [[ -d "${HERE}/backups" ]]; then
  rm -rf "${HERE}/backups"
  log "試験環境のバックアップ（${HERE}/backups）を削除"
fi

# 4. 消えたことを確かめる
fail=0
for c in "${STG_CONTAINERS[@]}"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "${c}"; then log "残っています: コンテナ ${c}"; fail=1; fi
done
docker network inspect "${STG_NETWORK}" >/dev/null 2>&1 && { log "残っています: network ${STG_NETWORK}"; fail=1; }
docker volume inspect "${STG_VOLUME}" >/dev/null 2>&1 && { log "残っています: volume ${STG_VOLUME}"; fail=1; }
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE '[:.]18101$'; then log "ポート 18101 がまだ使われています"; fail=1; fi

# 本番が無事かも見る（読むだけ）
for c in carelink-postgres carelink-backend; do
  st=$(docker inspect "${c}" --format '{{.State.Status}}' 2>/dev/null || echo "なし")
  log "本番 ${c}: ${st}"
done

if [[ "${fail}" == "0" ]]; then
  log "完了: 試験環境はすべて消えました"
else
  log "一部が残っています。上の表示を見て手で消してください"
  exit 1
fi
