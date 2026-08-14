#!/usr/bin/env bash
# tickflow-stock-panel 本地一键启动脚本
#
# 用法: bash start-dev.sh
#
# 后端 → http://localhost:3020
# 前端 → http://localhost:3011

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
NODE_EXE="C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2/node.exe"

# 启动后端
echo "[dev] 启动后端 (port 3020)..."
cd "$BACKEND_DIR"
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 3020 &
BACKEND_PID=$!

# 启动前端
echo "[dev] 启动前端 (port 3011)..."
cd "$FRONTEND_DIR"
"$NODE_EXE" node_modules/vite/bin/vite.js --host 0.0.0.0 --port 3011 &
FRONTEND_PID=$!

echo ""
echo "╭──────────────────────────────────────────────╮"
echo "│  tickflow-stock-panel                        │"
echo "│                                              │"
echo "│  backend   http://localhost:3020             │"
echo "│  frontend  http://localhost:3011             │"
echo "│                                              │"
echo "│  Ctrl-C 同时关闭两端                          │"
echo "╰──────────────────────────────────────────────╯"
echo ""

# Ctrl-C 关闭两端
cleanup() {
  echo ""
  echo "[dev] 关闭服务..."
  kill "$BACKEND_PID" 2>/dev/null || true
  kill "$FRONTEND_PID" 2>/dev/null || true
  echo "[dev] 已退出"
  exit 0
}
trap cleanup INT TERM

wait
