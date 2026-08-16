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

# 注：历史上 setup.sh 会在此自动执行 `uv run python -m calculus_agent.demo` 向真实数据库
# 写入 15 道 built-in-demo 初中数学题。这些题非微积分内容，已于 2026-08-16 被物理删除，
# 故不再自动 seed，避免重新初始化时 demo 题再次进入真实库。
# 如需本地开发用 demo 数据，请显式手动执行该命令。

echo
echo "安装完成。运行 ./scripts/start.sh，然后打开 http://127.0.0.1:5173"
