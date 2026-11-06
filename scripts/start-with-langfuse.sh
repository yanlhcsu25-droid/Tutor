#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
fi
if [ -z "$UV_BIN" ]; then
  echo "未找到 uv，请先运行 ./scripts/setup.sh"
  exit 1
fi

# Prefer an available HTTP(S) proxy when httpx lacks optional SOCKS support.
if [[ "${ALL_PROXY:-${all_proxy:-}}" == socks* ]] && [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
  unset ALL_PROXY all_proxy
fi

UV_RUN=("$UV_BIN" run)
if [ -f .env ]; then
  UV_RUN+=(--env-file .env)
fi

"${UV_RUN[@]}" python - <<'PY'
import os

missing = [
    name
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    if not os.getenv(name)
]
if missing:
    raise SystemExit(f"缺少 Langfuse 配置：{', '.join(missing)}")

from langfuse import get_client

client = get_client()
if not client.auth_check():
    raise SystemExit("Langfuse API 认证失败，请检查 Key 和 LANGFUSE_BASE_URL")
client.flush()
print("Langfuse API 连接成功")
PY

exec "$PROJECT_DIR/scripts/start.sh"
