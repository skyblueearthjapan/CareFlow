#!/usr/bin/env bash
# らく助エージェントを立てる前の確認（読むだけ・何も変えない）。
#
# 使い方（VPS の上で）:
#   bash preflight.sh prod
#   bash preflight.sh staging
#
# 見ること: メモリ（swap なし）・ディスク・ポート・網・秘密ファイル・DB のロール・本番の健康・
#           staging と本番の箱を同時に立てていないか。1 つでも NG なら終了コード 1。
set -uo pipefail

ENV_NAME="${1:-}"
case "${ENV_NAME}" in
  prod)
    PREFIX=carelink; PORT=18201; BACKEND_NET=deployment_default; PG=carelink-postgres
    SECRETS=/opt/carelink/secrets/agent-relay.env; BACKUP_DIR=/opt/carelink/backups/agent
    OTHER_SANDBOX=stg-agent-sandbox
    ;;
  staging)
    PREFIX=stg; PORT=18211; BACKEND_NET=carelink-stg; PG=stg-postgres
    SECRETS=/opt/carelink/agent-staging/agent-relay.env; BACKUP_DIR=/opt/carelink/agent-staging/backups
    OTHER_SANDBOX=carelink-agent-sandbox
    ;;
  *) echo "使い方: bash preflight.sh prod|staging" >&2; exit 2 ;;
esac

# 立てる分の上限の合計（compose の mem_limit: 中継 256m＋箱 1g＋出口 64m）＋ 本番を守る余裕 512MB
NEED_MB=$((256 + 1024 + 64 + 512))
MIN_DISK_MB=3072
fail=0
ok()  { printf '  OK  %s\n' "$*"; }
ng()  { printf '  NG  %s\n' "$*"; fail=1; }
inf() { printf '  ..  %s\n' "$*"; }

echo "[preflight] ${ENV_NAME}"

avail=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 ))
swap=$(( $(awk '/^SwapTotal:/ {print $2}' /proc/meminfo) / 1024 ))
if (( avail >= NEED_MB )); then ok "空きメモリ ${avail}MB ≥ ${NEED_MB}MB（swap ${swap}MB）"; else ng "空きメモリ ${avail}MB < ${NEED_MB}MB（swap ${swap}MB）。立てない"; fi

disk=$(df -Pm /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}')
if (( ${disk:-0} >= MIN_DISK_MB )); then ok "空きディスク ${disk}MB"; else ng "空きディスク ${disk:-?}MB < ${MIN_DISK_MB}MB"; fi

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}\$"; then
  if docker ps --format '{{.Names}}' | grep -qx "${PREFIX}-agent"; then inf "ポート ${PORT} は ${PREFIX}-agent が使用中（入れ替え）"; else ng "ポート ${PORT} がほかで使われています"; fi
else ok "ポート ${PORT} は空き"; fi

if docker network inspect "${BACKEND_NET}" >/dev/null 2>&1; then ok "網 ${BACKEND_NET} あり"; else ng "網 ${BACKEND_NET} がありません（backend が動いていない？）"; fi

if docker network ls --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}' >/dev/null 2>&1; then
  sub=$([[ "${ENV_NAME}" == prod ]] && echo 172.31.250.0/24 || echo 172.31.251.0/24)
  owner=$(docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | awk -v s="${sub}" '$0 ~ s {print $1}')
  if [[ -z "${owner}" || "${owner}" == "${PREFIX}-agent-sandbox-net" ]]; then ok "箱の網の範囲 ${sub} は空き（または自分）"; else ng "箱の網の範囲 ${sub} を ${owner} が使っています"; fi
fi

if [[ -f "${SECRETS}" ]]; then
  perm=$(stat -c '%a %U' "${SECRETS}")
  [[ "${perm}" == "400 root" ]] && ok "秘密ファイル ${SECRETS}（${perm}）" || ng "秘密ファイル ${SECRETS} の権限が ${perm}（400 root にする）"
  for k in AGENT_RO_DSN AGENT_DUMP_DSN AGENT_RELAY_DSN AGENT_URL_SIGNING_KEY AGENT_BACKEND_IDENTIFIER AGENT_BACKEND_PASSWORD; do
    grep -qE "^${k}=.+" "${SECRETS}" && ok "  ${k} あり（値は表示しない）" || ng "  ${k} がありません"
  done
  # 本番の .env の秘密が紛れていないか（名前だけ見る）
  if grep -qE '^(JWT_SECRET|NEXTAUTH_SECRET|POSTGRES_PASSWORD|KAIPOKE_API_TOKEN|KAIPOKE_CRED_SECRET)=' "${SECRETS}"; then
    ng "秘密ファイルに本番 .env の鍵が入っています。消してください"
  fi
else ng "秘密ファイル ${SECRETS} がありません（set-role-passwords.sh）"; fi

if [[ -d "${BACKUP_DIR}" ]]; then
  own=$(stat -c '%u:%g %a' "${BACKUP_DIR}")
  [[ "${own}" == 10001:10001* ]] && ok "バックアップ置き場 ${BACKUP_DIR}（${own}）" || ng "バックアップ置き場 ${BACKUP_DIR} の持ち主が ${own}（10001:10001 にする）"
else ng "バックアップ置き場 ${BACKUP_DIR} がありません（install -d -o 10001 -g 10001 -m 0700）"; fi

if docker inspect "${PG}" >/dev/null 2>&1; then
  n=$(docker exec "${PG}" psql -X -At -U carelink -d carelink -c "select count(*) from pg_roles where rolname in ('agent_ro','agent_dump','agent_relay')" 2>/dev/null)
  [[ "${n}" == "3" ]] && ok "DB のロール 3 つあり" || ng "DB のロールが ${n:-?}/3（sql/roles.sql）"
  s=$(docker exec "${PG}" psql -X -At -U carelink -d carelink -c "select count(*) from pg_namespace where nspname='agent'" 2>/dev/null)
  [[ "${s}" == "1" ]] && ok "スキーマ agent あり" || ng "スキーマ agent がありません（migration 0091 か roles.sql）"
else ng "${PG} が動いていません"; fi

if docker ps --format '{{.Names}}' | grep -qx "${OTHER_SANDBOX}"; then
  ng "${OTHER_SANDBOX} が動いています。staging と本番の箱は同時に立てない（メモリ・設計 §2-7）"
else ok "もう一方の箱（${OTHER_SANDBOX}）は止まっている"; fi

# 本番の健康（読むだけ）
if curl -fsS -m 5 http://127.0.0.1:18001/api/v1/healthz >/dev/null 2>&1; then ok "本番 backend healthz 200"; else ng "本番 backend healthz が 200 ではない"; fi

echo
if [[ "${fail}" == "0" ]]; then echo "[preflight] すべて OK"; else echo "[preflight] NG があります。立てないでください"; exit 1; fi
