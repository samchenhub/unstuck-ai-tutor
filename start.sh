#!/usr/bin/env bash
# Start Unstuck and open it in the browser (http://localhost:8000).
# Everything it needs is installed automatically on the first run. Press Ctrl+C or close the window to stop.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "第一次运行：正在安装 uv（用来安装 Python 和其他组件）…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "已创建 backend/.env，请在里面填入 LLM_API_KEY 后重新运行。"
  exit 1
fi
if grep -q "your-zhipu-api-key" backend/.env; then
  echo "请先在 backend/.env 里填入 LLM_API_KEY。"
  exit 1
fi

# The web page: shipped pre-built in packages; a source checkout (with Node) builds it when missing or stale.
web_stale() {
  [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html 2>/dev/null | head -1)" ]
}
if web_stale; then
  if command -v npm >/dev/null 2>&1; then
    echo "正在生成网页…"
    (cd frontend && npm install --silent && npm run build --silent)
  elif [ ! -f frontend/dist/index.html ]; then
    echo "缺少网页文件（frontend/dist）。请使用完整安装包，或者安装 Node.js 后重新运行。"
    exit 1
  fi
fi

if lsof -ti tcp:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 8000 已被占用，Unstuck 可能已经在运行：http://localhost:8000"
  exit 1
fi

echo "检查组件（第一次运行要下载 Python 和 AI 组件，约 2–3 GB，可能需要 10–20 分钟，请耐心等待）…"
(cd backend && uv sync --quiet)

echo "启动 Unstuck…"
(cd backend && exec .venv/bin/uvicorn unstuck.api.app:create_app --factory --host 127.0.0.1 --port 8000 --log-level warning) &
SERVER=$!

stop() {
  trap - EXIT INT TERM HUP
  kill "$SERVER" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "Unstuck 已停止。"
}
trap stop EXIT INT TERM HUP

for _ in $(seq 1 120); do
  curl -s -o /dev/null http://127.0.0.1:8000/api/health && break
  kill -0 "$SERVER" 2>/dev/null || { echo "Unstuck 没能启动，请查看上面的报错。"; exit 1; }
  sleep 1
done

if [ -z "${UNSTUCK_NO_OPEN:-}" ]; then
  if command -v open >/dev/null 2>&1; then open http://localhost:8000; else xdg-open http://localhost:8000 >/dev/null 2>&1 || true; fi
fi
echo "Unstuck 已启动：http://localhost:8000 （按 Ctrl+C 或关闭这个窗口即可停止）"
[ -z "${UNSTUCK_SMOKE_TEST:-}" ] || exit 0  # CI: started and answering is enough

while kill -0 "$SERVER" 2>/dev/null; do
  sleep 1
done
