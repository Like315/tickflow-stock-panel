#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app_env_path="${repo_root}/.env"
tunnel_env_path="${repo_root}/.env.cloudflare.local"
auth_path="${repo_root}/data/user_data/auth.json"
compose_path="${repo_root}/docker-compose.yml"
cloudflare_compose_path="${repo_root}/docker-compose.cloudflare.yml"

env_value() {
  local path="$1"
  local name="$2"
  sed -n "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*//p" "${path}" | tail -n 1
}

if [[ ! -f "${app_env_path}" ]]; then
  echo "缺少 .env。请先复制 .env.example，并设置 AUTH_PASSWORD。" >&2
  exit 1
fi
if [[ ! -f "${tunnel_env_path}" ]]; then
  echo "缺少 .env.cloudflare.local。请复制 .env.cloudflare.example 并填写配置。" >&2
  exit 1
fi

admin_password="$(env_value "${app_env_path}" AUTH_PASSWORD)"
if [[ -z "${admin_password}" && ! -f "${auth_path}" ]]; then
  echo "尚未初始化 admin：请在 .env 设置至少 6 位 AUTH_PASSWORD。" >&2
  exit 1
fi

local_port="$(env_value "${app_env_path}" PORT)"
local_port="${local_port:-3018}"
if [[ ! "${local_port}" =~ ^[0-9]+$ ]] || (( local_port < 1 || local_port > 65535 )); then
  echo ".env 中的 PORT 必须是 1 到 65535 之间的整数。" >&2
  exit 1
fi

tunnel_token="$(env_value "${tunnel_env_path}" TUNNEL_TOKEN)"
if [[ ${#tunnel_token} -lt 20 ]]; then
  echo ".env.cloudflare.local 中缺少有效的 TUNNEL_TOKEN。" >&2
  exit 1
fi
public_hostname="$(env_value "${tunnel_env_path}" PUBLIC_HOSTNAME)"
if [[ ! "${public_hostname}" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]]; then
  echo "PUBLIC_HOSTNAME 格式无效，请只填写类似 stocks.example.com 的主机名。" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker。请先安装 Docker 与 Compose 插件。" >&2
  exit 1
fi

compose=(docker compose -f "${compose_path}" -f "${cloudflare_compose_path}")
cd "${repo_root}"
"${compose[@]}" config --quiet
if [[ "${1:-}" == "--skip-build" ]]; then
  "${compose[@]}" up -d
else
  "${compose[@]}" up -d --build
fi

local_healthy=0
for _attempt in $(seq 1 30); do
  if curl --fail --silent "http://127.0.0.1:${local_port}/health" >/dev/null; then
    local_healthy=1
    break
  fi
  sleep 2
done
if [[ "${local_healthy}" != "1" ]]; then
  echo "应用在 60 秒内未通过本地 /health 检查，请查看 docker compose logs app。" >&2
  exit 1
fi

if [[ "$(docker inspect --format '{{.State.Running}}' TickFlow_Cloudflare_Tunnel 2>/dev/null || true)" != "true" ]]; then
  echo "cloudflared 容器未运行，请查看 docker compose logs cloudflared。" >&2
  exit 1
fi

public_health_url="https://${public_hostname}/health"
public_healthy=0
for _attempt in $(seq 1 30); do
  if curl --fail --silent "${public_health_url}" >/dev/null; then
    public_healthy=1
    break
  fi
  sleep 2
done
if [[ "${public_healthy}" != "1" ]]; then
  echo "Tunnel 已启动，但 ${public_health_url} 尚不可用。请确认 Published application 指向 http://app:3018。" >&2
  exit 1
fi

echo "公网面板已就绪：https://${public_hostname}"
"${compose[@]}" ps
