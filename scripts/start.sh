#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -x web/node_modules/.bin/vite ]; then
  echo "前端依赖尚未安装，请先运行 ./scripts/setup.sh"
  exit 1
fi

HOST="${HOST:-127.0.0.1}"

uv run uvicorn calculus_agent.main:app --host "$HOST" --port 8000 &
BACKEND_PID=$!
(cd web && ./node_modules/.bin/vite --host "$HOST" --port 5173) &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "MathPaper Agent 已启动：http://127.0.0.1:5173"
if [ "$HOST" = "0.0.0.0" ]; then
  echo "局域网朋友可通过 http://你的电脑IP:5173 访问"
fi
wait
