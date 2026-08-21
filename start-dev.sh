#!/usr/bin/env bash
# tickflow-stock-panel 兼容启动入口
#
# 用法: bash start-dev.sh
#
# 后端 → http://localhost:3020
# 前端 → http://localhost:3011

set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-3020}"
export BACKEND_PORT

# 历史入口只保留端口兼容，依赖检查与进程清理由 dev.sh 统一维护。
exec "$REPOSITORY_ROOT/dev.sh"
