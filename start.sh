#!/usr/bin/env bash
# Start Unstuck: backend (:8000) + frontend (:5173), then open the browser.
# Press Ctrl+C (or close the window) to stop both.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少 $1，请先安装：brew install $2"
    exit 1
  fi
}
need uv uv
need npm node
need ffmpeg ffmpeg

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "已创建 backend/.env，请在里面填入 LLM_API_KEY 后重新运行。"
  exit 1
fi
if grep -q "your-zhipu-api-key" backend/.env; then
  echo "请先在 backend/.env 里填入 LLM_API_KEY。"
  exit 1
fi

for port in 8000 5173; do
  if lsof -ti "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 $port 已被占用，Unstuck 可能已经在运行：http://localhost:5173"
    exit 1
  fi
done

echo "检查依赖…"
(cd backend && uv sync --quiet)
[ -d frontend/node_modules ] || (cd frontend && npm install --silent)

echo "启动后端和前端…"
(cd backend && exec .venv/bin/uvicorn unstuck.api.app:create_app --factory --port 8000 --log-level warning) &
BACKEND=$!
(cd frontend && exec ./node_modules/.bin/vite --port 5173 --strictPort --logLevel warn) &
FRONTEND=$!

stop() {
  trap - EXIT INT TERM HUP
  kill "$BACKEND" "$FRONTEND" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "Unstuck 已停止。"
}
trap stop EXIT INT TERM HUP

for _ in $(seq 1 60); do
  if curl -s -o /dev/null http://localhost:8000/api/health && curl -s -o /dev/null http://localhost:5173; then
    break
  fi
  sleep 1
done

[ -n "${UNSTUCK_NO_OPEN:-}" ] || open http://localhost:5173
echo "Unstuck 已启动：http://localhost:5173 （按 Ctrl+C 或关闭这个窗口即可停止）"

# Stop everything if either server exits (bash 3.2 on macOS has no `wait -n`).
while kill -0 "$BACKEND" 2>/dev/null && kill -0 "$FRONTEND" 2>/dev/null; do
  sleep 1
done
