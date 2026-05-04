#!/usr/bin/env bash
# CareLink VPS pre-flight check.
# Run on the target VPS *before* `git clone` / `docker compose up`.
# Exits non-zero if any check fails so it can be wired into a deploy pipeline.

set -u
set -o pipefail

PASS=0
FAIL=0

ok()   { printf '  [ OK ] %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL + 1)); }
hdr()  { printf '\n== %s ==\n' "$1"; }

# 1. Disk space (>=5 GB free on /)
hdr "disk"
disk_free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "${disk_free_gb:-0}" -ge 5 ]; then
  ok "free disk on / = ${disk_free_gb} GB"
else
  fail "free disk on / = ${disk_free_gb} GB (need >= 5)"
fi

# 2. Memory (>=1 GB available)
hdr "memory"
mem_avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "${mem_avail_mb:-0}" -ge 1024 ]; then
  ok "MemAvailable = ${mem_avail_mb} MB"
else
  fail "MemAvailable = ${mem_avail_mb} MB (need >= 1024)"
fi

# 3. Docker
hdr "docker"
if command -v docker >/dev/null 2>&1; then
  docker_ver=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
  ok "docker version = ${docker_ver}"
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

# 6. ufw (only 22/tcp public)
hdr "ufw"
if command -v ufw >/dev/null 2>&1; then
  ufw_status=$(sudo ufw status 2>/dev/null | head -1)
  if echo "${ufw_status}" | grep -qi 'active'; then
    ok "ufw active"
  else
    fail "ufw inactive (${ufw_status})"
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
hdr "target dir"
if [ -e /opt/carelink ]; then
  if [ -z "$(ls -A /opt/carelink 2>/dev/null)" ]; then
    ok "/opt/carelink exists and is empty"
  else
    fail "/opt/carelink exists and is NOT empty (clone will fail)"
  fi
else
  ok "/opt/carelink does not exist (will be created by deploy)"
fi

# Summary
hdr "summary"
printf 'pass=%d fail=%d\n' "${PASS}" "${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
  exit 1
fi
exit 0
