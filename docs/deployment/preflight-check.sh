#!/usr/bin/env bash
# CareLink VPS pre-flight check.
# Run on the target VPS *before* `git clone` / `docker compose up`.
# Exits non-zero if any check fails so it can be wired into a deploy pipeline.

set -u
set -o pipefail

PASS=0
FAIL=0
WARN=0

ok()   { printf '  [ OK ] %s\n' "$1"; PASS=$((PASS + 1)); }
warn() { printf '  [WARN] %s\n' "$1"; WARN=$((WARN + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL + 1)); }
hdr()  { printf '\n== %s ==\n' "$1"; }

# 1. Disk space (>=5 GB free on /, MB-precise so ~4.6 GB does not pass)
hdr "disk"
disk_free_mb=$(df -BM --output=avail / | awk 'NR==2 {gsub(/[^0-9]/, "", $1); print $1}')
required_mb=5120
if [ "${disk_free_mb:-0}" -ge "${required_mb}" ]; then
  ok "free disk on / = ${disk_free_mb} MB (>= ${required_mb})"
else
  fail "free disk on / = ${disk_free_mb} MB (need >= ${required_mb})"
fi

# 2. Memory (>=1 GB available)
hdr "memory"
mem_avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "${mem_avail_mb:-0}" -ge 1024 ]; then
  ok "MemAvailable = ${mem_avail_mb} MB"
else
  fail "MemAvailable = ${mem_avail_mb} MB (need >= 1024)"
fi

# 3. Docker (>= 24.x major)
hdr "docker"
if command -v docker >/dev/null 2>&1; then
  docker_ver=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
  docker_server_ver=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "")
  docker_major=$(echo "${docker_server_ver:-${docker_ver}}" | awk -F. '{print $1}')
  if [ -n "${docker_major}" ] && [ "${docker_major}" -ge 24 ] 2>/dev/null; then
    ok "docker version = ${docker_ver} (server=${docker_server_ver:-unknown}, major>=24)"
  else
    fail "docker version = ${docker_ver} (server=${docker_server_ver:-unknown}, need major >= 24)"
  fi
else
  fail "docker not installed"
fi

# 4. docker compose v2
if docker compose version >/dev/null 2>&1; then
  compose_ver=$(docker compose version --short 2>/dev/null)
  ok "docker compose version = ${compose_ver}"
else
  fail "docker compose v2 not available"
fi

# 5. cloudflared (systemd native, NOT containerised)
hdr "cloudflared"
if systemctl is-active --quiet cloudflared 2>/dev/null; then
  ok "cloudflared service is active"
else
  fail "cloudflared service is NOT active"
fi
if [ -f /etc/cloudflared/config.yml ]; then
  ok "/etc/cloudflared/config.yml exists"
else
  fail "/etc/cloudflared/config.yml missing"
fi

# 6. ufw (must be active AND only 22/tcp ALLOW publicly visible)
hdr "ufw"
if command -v ufw >/dev/null 2>&1; then
  ufw_status_full=$(sudo ufw status 2>/dev/null || true)
  ufw_status_first=$(printf '%s\n' "${ufw_status_full}" | head -1)
  if echo "${ufw_status_first}" | grep -qi 'active'; then
    ok "ufw active"
    # Inspect ALLOW rules. Allow 22/tcp (and OpenSSH alias). Anything else ALLOW = warn.
    extra_allow=$(printf '%s\n' "${ufw_status_full}" \
      | awk '/ALLOW/ {print}' \
      | grep -viE '(^|[[:space:]])(22(/tcp)?|OpenSSH)([[:space:]]|$)' \
      || true)
    if [ -z "${extra_allow}" ]; then
      ok "ufw only allows 22/tcp publicly"
    else
      warn "ufw has ALLOW rules other than 22/tcp:\n${extra_allow}"
    fi
  else
    fail "ufw inactive (${ufw_status_first})"
  fi
else
  fail "ufw not installed"
fi

# 7. Ports 18000 / 18001 free
hdr "ports"
for port in 18000 18001; do
  if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE ":${port}$"; then
    fail "port ${port} already in use"
  else
    ok "port ${port} free"
  fi
done

# 8. /opt/carelink target dir
# Existing dir is a WARN (re-deploy allowed), not a hard fail.
hdr "target dir"
if [ -e /opt/carelink ]; then
  if [ -z "$(ls -A /opt/carelink 2>/dev/null)" ]; then
    ok "/opt/carelink exists and is empty"
  else
    warn "/opt/carelink exists and is NOT empty (re-deploy path; verify clone target manually)"
  fi
else
  ok "/opt/carelink does not exist (will be created by deploy)"
fi

# Summary
hdr "summary"
printf 'pass=%d warn=%d fail=%d\n' "${PASS}" "${WARN}" "${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
  exit 1
fi
exit 0
