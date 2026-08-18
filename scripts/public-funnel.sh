#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_path="${repo_root}/.env"
auth_path="${repo_root}/data/user_data/auth.json"

if [[ ! -f "${env_path}" ]]; then
  echo "缺少 .env。请先复制 .env.example 为 .env，并设置 AUTH_PASSWORD。" >&2
  exit 1
fi

env_value() {
  local name="$1"
  sed -n "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*//p" "${env_path}" | tail -n 1
}

admin_password="$(env_value AUTH_PASSWORD)"
if [[ -z "${admin_password}" && ! -f "${auth_path}" ]]; then
  echo "尚未初始化 admin：请在 .env 设置至少 6 位 AUTH_PASSWORD。" >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "未找到 tailscale。请先安装、登录，并在管理台启用 Funnel。" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker。请先安装 Docker 与 Compose 插件。" >&2
  exit 1
fi

cd "${repo_root}"
if [[ "${1:-}" == "--skip-build" ]]; then
  docker compose up -d
else
  docker compose up -d --build
fi

healthy=0
for _attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:3018/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ "${healthy}" != "1" ]]; then
  echo "应用在 60 秒内未通过 /health 检查，请运行 docker compose logs app。" >&2
  exit 1
fi

tailscale status >/dev/null
tailscale funnel --bg --yes 3018
tailscale funnel status
