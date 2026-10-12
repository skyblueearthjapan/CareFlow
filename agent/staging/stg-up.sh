#!/usr/bin/env bash
# らく助 使い捨て試験環境を VPS 上に立てる（本番 DB・カイポケのロボットにはつながない）。
#
# 使い方（VPS の上で・root）:
#   bash stg-up.sh                       # /opt/carelink/backups の最新 *.sql.gz を流し込む
#   bash stg-up.sh /opt/carelink/backups/daily-20261011-1730.sql.gz
#
# 実行にはディレクターの了承が要る（README.md）。終わったら必ず stg-down.sh。
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.staging.yml"
ENV_FILE="${HERE}/.env.staging"
PROJECT="carelink-stg"
PROD_ENV="${PROD_ENV:-/opt/carelink/.env}"
BACKUP_DIR="${BACKUP_DIR:-/opt/carelink/backups}"
PROD_BACKEND_CONTAINER="carelink-backend"
STG_PORT=18101
MIN_AVAIL_MB=1536
MIN_DISK_MB=3072

log()  { printf '[stg-up] %s\n' "$*"; }
die()  { printf '[stg-up] 中止: %s\n' "$*" >&2; exit 1; }

compose() { docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"; }

# 起動を始めた後に失敗したら（die を含む）、自動で stg-down.sh を流して何も残さない。
STARTED=0
on_exit() {
  local rc=$?
  if [[ "${rc}" != "0" && "${STARTED}" == "1" ]]; then
    printf '[stg-up] 途中で失敗しました（rc=%s）。片付けます: stg-down.sh\n' "${rc}" >&2
    bash "${HERE}/stg-down.sh" || printf '[stg-up] 片付けにも失敗。手で stg-down.sh を実行してください\n' >&2
  fi
  exit "${rc}"
}
trap on_exit EXIT

# ---------------------------------------------------------------- 0. 前提
command -v docker >/dev/null || die "docker がありません"
docker compose version >/dev/null 2>&1 || die "docker compose (v2) がありません"
[[ -f "${COMPOSE_FILE}" ]] || die "${COMPOSE_FILE} がありません"
[[ -r "${PROD_ENV}" ]] || die "${PROD_ENV} が読めません（値は写さず、無害な設定だけ使います）"

# ---------------------------------------------------------------- 1. もう動いていないか
for c in stg-postgres stg-backend; do
  if docker ps -a --format '{{.Names}}' | grep -qx "${c}"; then
    die "コンテナ ${c} が既にあります。先に stg-down.sh を実行してください"
  fi
done
docker volume inspect carelink-stg-pg >/dev/null 2>&1 && die "volume carelink-stg-pg が残っています。stg-down.sh を実行してください"
docker network inspect carelink-stg >/dev/null 2>&1 && die "network carelink-stg が残っています。stg-down.sh を実行してください"
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${STG_PORT}\$"; then
  die "ポート ${STG_PORT} が使われています"
fi

# ---------------------------------------------------------------- 2. メモリ・ディスク
avail_mb=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 ))
log "空きメモリ (MemAvailable) = ${avail_mb} MB（下限 ${MIN_AVAIL_MB} MB）"
(( avail_mb >= MIN_AVAIL_MB )) || die "空きメモリが ${MIN_AVAIL_MB} MB 未満です（本番を守るため立てません）"
disk_mb=$(df -Pm /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}')
disk_mb="${disk_mb:-0}"
log "空きディスク = ${disk_mb} MB（下限 ${MIN_DISK_MB} MB）"
(( disk_mb >= MIN_DISK_MB )) || die "空きディスクが ${MIN_DISK_MB} MB 未満です"

# ---------------------------------------------------------------- 3. 流し込むバックアップ
DUMP="${1:-}"
if [[ -z "${DUMP}" ]]; then
  # 日次（本番の全体の写し）を優先。rk backup の agent-*（試験環境の写しのことがある）は選ばない
  DUMP="$(ls -1t "${BACKUP_DIR}"/daily-*.sql.gz 2>/dev/null | head -n 1 || true)"
  [[ -n "${DUMP}" ]] || DUMP="$(ls -1t "${BACKUP_DIR}"/*.sql.gz 2>/dev/null | grep -v '/agent-' | head -n 1 || true)"
  [[ -n "${DUMP}" ]] || die "${BACKUP_DIR} に *.sql.gz がありません"
fi
[[ -f "${DUMP}" ]] || die "バックアップ ${DUMP} がありません"
gzip -t "${DUMP}" || die "バックアップ ${DUMP} が壊れています (gzip -t)"
bad_lines=$(gunzip -c "${DUMP}" | grep -cE '^\\connect|^CREATE DATABASE|^DROP DATABASE' || true)
if [[ "${bad_lines}" != "0" ]]; then
  die "バックアップに \\connect / CREATE DATABASE が含まれます（1 DB の plain dump だけ受け付けます）"
fi
log "バックアップ = ${DUMP} ($(du -h "${DUMP}" | cut -f1), $(date -r "${DUMP}" '+%Y-%m-%d %H:%M %Z'))"

# ---------------------------------------------------------------- 4. backend の image（今の本番と同じもの）
STG_BACKEND_IMAGE="$(docker inspect "${PROD_BACKEND_CONTAINER}" --format '{{.Image}}' 2>/dev/null || true)"
if [[ -z "${STG_BACKEND_IMAGE}" ]]; then
  STG_BACKEND_IMAGE="$(docker image inspect carelink-backend:latest --format '{{.Id}}')" \
    || die "carelink-backend の image が見つかりません"
fi
export STG_BACKEND_IMAGE
log "backend image = ${STG_BACKEND_IMAGE}"

# ---------------------------------------------------------------- 5. .env.staging を作る
# 本番 .env からは「秘密でも外部接続でもない」設定だけを写す。値は画面に出さない。
prod_val() {
  local key="$1" v
  v="$(grep -E "^${key}=" "${PROD_ENV}" | tail -n 1 | cut -d= -f2- || true)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "${v}"
}
rand() { python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])//2))" "$1"; }

PG_USER="$(prod_val POSTGRES_USER)"; PG_USER="${PG_USER:-carelink}"
PG_DB="$(prod_val POSTGRES_DB)";     PG_DB="${PG_DB:-carelink}"
PG_PASS="stg$(rand 32)"
STG_JWT="stg$(rand 64)"

COPY_KEYS=(JWT_ALGORITHM JWT_ACCESS_TTL_SECONDS JWT_REFRESH_TTL_SECONDS
           LOGIN_LOCKOUT_THRESHOLD LOGIN_LOCKOUT_DURATION_SECONDS
           KAIPOKE_EXPORT_TTL_SECONDS VISIT_AUDIO_MAX_BYTES VISIT_AUDIO_RETENTION_DAYS
           VOICE_AI_TIMEOUT_SECONDS VOICE_JOB_STALE_MINUTES)

{
  echo "# 使い捨て試験環境用（stg-up.sh が $(date -u '+%Y-%m-%dT%H:%MZ') に生成）。stg-down.sh で消える。"
  echo "# 本番の秘密は入れていない。外部接続（カイポケ・Google・Vertex）は無効化。"
  echo "POSTGRES_USER=${PG_USER}"
  echo "POSTGRES_DB=${PG_DB}"
  echo "POSTGRES_PASSWORD=${PG_PASS}"
  echo "DATABASE_URL=postgresql+asyncpg://${PG_USER}:${PG_PASS}@stg-postgres:5432/${PG_DB}"
  echo "APP_ENV=staging"
  echo "APP_NAME=CareLink Backend (STAGING)"
  echo "LOG_LEVEL=INFO"
  echo "JWT_SECRET=${STG_JWT}"
  echo "CORS_ORIGINS=http://127.0.0.1:18101"
  # --- カイポケ RPA: 向き先を塞ぐ・鍵なし・復号用の鍵も本番と別（DB の暗号化済み資格情報は復号できない）
  echo "KAIPOKE_API_BASE_URL=http://127.0.0.1:9/kaipoke-disabled-in-staging"
  echo "KAIPOKE_API_TOKEN="
  echo "KAIPOKE_CRED_SECRET="
  echo "KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=false"
  echo "KAIPOKE_EXPORT_DIR=/tmp/stg/kaipoke_exports"
  echo "KAIPOKE_NOVNC_URL=http://127.0.0.1:9/novnc-disabled-in-staging"
  echo "INTEGRATION_BASE_URL=http://127.0.0.1:9/integrations-disabled-in-staging"
  # --- Google（地図）・Vertex（音声）: 無効
  echo "GOOGLE_MAPS_API_KEY="
  echo "VOICE_AI_PROVIDER=none"
  echo "VERTEX_PROJECT_ID=staging-disabled"
  echo "GOOGLE_APPLICATION_CREDENTIALS=/nonexistent/staging-no-credentials.json"
  # --- 本番のファイル置き場は使わない（mount もしない）
  echo "VISIT_PHOTOS_DIR=/tmp/stg/visit_photos"
  echo "VISIT_AUDIO_DIR=/tmp/stg/visit_audio"
  echo "VISIT_SIGNATURES_DIR=/tmp/stg/visit_signatures"
  for k in "${COPY_KEYS[@]}"; do
    v="$(prod_val "${k}")"
    if [[ -n "${v}" ]]; then echo "${k}=${v}"; fi
  done
  # compose の interpolation 用
  echo "STG_BACKEND_IMAGE=${STG_BACKEND_IMAGE}"
} > "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
log ".env.staging を作成（値は表示しません）: ${ENV_FILE}"

# ---------------------------------------------------------------- 6. Postgres を立てて流し込む
STARTED=1
log "stg-postgres を起動"
compose up -d --wait stg-postgres

log "バックアップを流し込み中（数十秒）"
RESTORE_LOG="${HERE}/restore-$(date -u +%Y%m%d-%H%M).log"
gunzip -c "${DUMP}" \
  | docker exec -i stg-postgres psql -X -q -v ON_ERROR_STOP=1 -U "${PG_USER}" -d "${PG_DB}" \
  > "${RESTORE_LOG}" 2>&1 || { tail -n 20 "${RESTORE_LOG}" >&2; die "流し込みに失敗（${RESTORE_LOG}）"; }
tables=$(docker exec stg-postgres psql -X -At -U "${PG_USER}" -d "${PG_DB}" \
  -c "select count(*) from information_schema.tables where table_schema='public'")
dump_rev=$(docker exec stg-postgres psql -X -At -U "${PG_USER}" -d "${PG_DB}" \
  -c "select version_num from alembic_version" | tr '\n' ' ')
log "流し込み完了: テーブル ${tables} 個・alembic_version = ${dump_rev}"

# ---------------------------------------------------------------- 7. backend を立てる
log "stg-backend を起動（127.0.0.1:${STG_PORT}）"
compose up -d --wait stg-backend

# 安全確認: DB の向き先・網・鍵
db_host=$(docker exec stg-backend python -c "from app.core.config import get_settings as g; from urllib.parse import urlsplit; print(urlsplit(g().database_url.replace('+asyncpg','')).hostname)")
[[ "${db_host}" == "stg-postgres" ]] || die "stg-backend の DB の向き先が stg-postgres ではありません (${db_host})"
nets=$(docker inspect stg-backend --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}')
[[ "${nets}" == "carelink-stg " ]] || die "stg-backend が carelink-stg 以外の網に入っています (${nets})"
tok=$(docker exec stg-backend sh -c 'printf %s "${KAIPOKE_API_TOKEN:-}"')
[[ -z "${tok}" ]] || die "stg-backend に KAIPOKE_API_TOKEN が入っています"
log "安全確認 OK: DB=stg-postgres・網=carelink-stg のみ・カイポケの鍵なし"

# ---------------------------------------------------------------- 8. migration（image の方が新しいときだけ・試験 DB にだけ）
head_rev=$(docker exec stg-backend alembic heads 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
log "image の alembic head = ${head_rev}"
if [[ "$(echo "${head_rev}" | xargs)" != "$(echo "${dump_rev}" | xargs)" ]]; then
  log "バックアップの版と image の版が違うので、試験 DB にだけ alembic upgrade head を流します"
  docker exec stg-backend alembic upgrade head
fi

# ---------------------------------------------------------------- 9. 疎通
curl -fsS "http://127.0.0.1:${STG_PORT}/api/v1/healthz" >/dev/null || die "healthz が 200 になりません"
log "healthz OK: http://127.0.0.1:${STG_PORT}/api/v1/healthz"

echo
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' stg-postgres stg-backend
echo
free -m
echo
STARTED=0
log "準備完了。rk は RK_API_PORT=${STG_PORT} RK_PG_CONTAINER=stg-postgres で使う。終わったら: bash ${HERE}/stg-down.sh"
