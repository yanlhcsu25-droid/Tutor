#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

command -v uv >/dev/null 2>&1 || {
  echo "缺少 uv，请先安装：https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
}
command -v node >/dev/null 2>&1 || {
  echo "缺少 Node.js 18+，请先安装：https://nodejs.org/"
  exit 1
}

if command -v pnpm >/dev/null 2>&1; then
  PNPM=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  PNPM=(corepack pnpm)
else
  echo "缺少 pnpm。请执行：npm install -g pnpm"
  exit 1
fi

test -f .env || cp .env.example .env
uv sync
(cd web && CI=true "${PNPM[@]}" install --frozen-lockfile)
uv run python -m calculus_agent.demo

echo
echo "安装完成。运行 ./scripts/start.sh，然后打开 http://127.0.0.1:5173"
