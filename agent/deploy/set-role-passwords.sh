#!/usr/bin/env bash
# DB の専用ロール（agent_ro・agent_dump・agent_relay）にパスワードを付け、中継の秘密ファイルを作る。
#
# 使い方（VPS の上で・root・roles.sql の後）:
#   bash set-role-passwords.sh prod              # 何をするかを表示するだけ（既定）
#   bash set-role-passwords.sh prod --apply      # 実行（PO の了承の後）
#   bash set-role-passwords.sh staging --apply   # 試験環境（stg-postgres）に
#
# - パスワードは新しく作る（毎回作り直す＝流すたびに鍵が替わる。中継の再起動が要る）。
# - パスワードは画面にもコマンドの引数にも出さない（psql へは標準入力で渡す・秘密ファイルは 0400）。
# - 秘密ファイルには中継が使う値だけ。本番の .env の値は写さない。
# - 既にある秘密ファイルのうち、ここで作らない値（AGENT_BACKEND_* など）は引き継ぐ。
set -euo pipefail
umask 077

ENV_NAME="${1:-}"
APPLY=0
[[ "${2:-}" == "--apply" ]] && APPLY=1

case "${ENV_NAME}" in
  prod)
    PG_CONTAINER=carelink-postgres
    SECRETS_FILE=/opt/carelink/secrets/agent-relay.env
    ;;
  staging)
    PG_CONTAINER=stg-postgres
    SECRETS_FILE=/opt/carelink/agent-staging/agent-relay.env
    ;;
  *)
    echo "使い方: bash set-role-passwords.sh prod|staging [--apply]" >&2
    exit 2
    ;;
esac
PG_USER="${PG_USER:-carelink}"
PG_DB="${PG_DB:-carelink}"

log() { printf '[set-role-passwords] %s\n' "$*"; }
die() { printf '[set-role-passwords] 中止: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker がありません"
docker inspect "${PG_CONTAINER}" >/dev/null 2>&1 || die "${PG_CONTAINER} が動いていません"
for r in agent_ro agent_dump agent_relay; do
  n=$(docker exec "${PG_CONTAINER}" psql -X -At -U "${PG_USER}" -d "${PG_DB}" -c "select count(*) from pg_roles where rolname='${r}'")
  [[ "${n}" == "1" ]] || die "ロール ${r} がありません。先に sql/roles.sql を流してください"
done

log "対象: ${ENV_NAME}（${PG_CONTAINER}・DB ${PG_DB}）"
log "秘密ファイル: ${SECRETS_FILE}（0400・root）"
if [[ "${APPLY}" != "1" ]]; then
  log "表示だけです。実行するには --apply を付けてください（PO の了承の後）"
  log "  1) agent_ro / agent_dump / agent_relay に新しいパスワード（48 文字）を付ける"
  log "  2) ${SECRETS_FILE} に AGENT_RO_DSN / AGENT_DUMP_DSN / AGENT_RELAY_DSN / AGENT_URL_SIGNING_KEY を書く"
  log "  3) 中継が動いていれば、再起動が要る（README §4）"
  exit 0
fi

rand() { python3 -c "import secrets; print(secrets.token_urlsafe(36))"; }
PW_RO="$(rand)"; PW_DUMP="$(rand)"; PW_RELAY="$(rand)"
SIGNING_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))")"

# 標準入力で渡す（ps・シェル履歴に出ない）。password_encryption は scram-sha-256（PG16 の既定）
docker exec -i "${PG_CONTAINER}" psql -X -q -v ON_ERROR_STOP=1 -U "${PG_USER}" -d "${PG_DB}" <<SQL
SET password_encryption = 'scram-sha-256';
ALTER ROLE agent_ro    PASSWORD '${PW_RO}';
ALTER ROLE agent_dump  PASSWORD '${PW_DUMP}';
ALTER ROLE agent_relay PASSWORD '${PW_RELAY}';
SQL
log "パスワードを付けました（表示しません）"

mkdir -p "$(dirname "${SECRETS_FILE}")"
chmod 700 "$(dirname "${SECRETS_FILE}")" 2>/dev/null || true
TMP="$(mktemp "${SECRETS_FILE}.XXXXXX")"
{
  echo "# らく助エージェントの中継の秘密（set-role-passwords.sh が $(date -u '+%Y-%m-%dT%H:%MZ') に生成・UTC）"
  echo "# git に入れない・写さない。中継（carelink-agent）だけが env_file で読む。箱には渡さない。"
  echo "AGENT_RO_DSN=postgresql://agent_ro:${PW_RO}@${PG_CONTAINER}:5432/${PG_DB}"
  echo "AGENT_DUMP_DSN=postgresql://agent_dump:${PW_DUMP}@${PG_CONTAINER}:5432/${PG_DB}"
  echo "AGENT_RELAY_DSN=postgresql://agent_relay:${PW_RELAY}@${PG_CONTAINER}:5432/${PG_DB}"
  echo "AGENT_URL_SIGNING_KEY=${SIGNING_KEY}"
  # ここで作らない値（エージェント専用の管理者ユーザーの資格情報・試験中の許可ユーザー）は前のファイルから引き継ぐ
  if [[ -f "${SECRETS_FILE}" ]]; then
    grep -E '^(AGENT_BACKEND_IDENTIFIER|AGENT_BACKEND_PASSWORD|AGENT_ALLOWED_USER_IDS)=' "${SECRETS_FILE}" || true
  fi
} > "${TMP}"
chmod 400 "${TMP}"
mv -f "${TMP}" "${SECRETS_FILE}"
log "秘密ファイルを書きました: ${SECRETS_FILE}"
grep -qE '^AGENT_BACKEND_IDENTIFIER=' "${SECRETS_FILE}" \
  || log "注意: AGENT_BACKEND_IDENTIFIER / AGENT_BACKEND_PASSWORD（エージェント専用の管理者）がまだありません。README §3-3"
