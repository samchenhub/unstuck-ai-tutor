#!/usr/bin/env bash
# Build a ready-to-run package of the committed code (Mac and Windows): Unstuck.zip with the web page pre-built,
# so the people you send it to only double-click Unstuck.command / Unstuck.bat.
#
#   scripts/package.sh                         -> dist/Unstuck.zip (they fill in their own keys)
#   scripts/package.sh --with-env              -> also includes your backend/.env (your API keys!)
#   scripts/package.sh --with-env -o ~/Desktop/Unstuck.zip
#
# Never included: backend/data (your videos, chats and Bilibili login), dependencies, caches.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/Unstuck.zip"
WITH_ENV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-env) WITH_ENV=1 ;;
    -o) OUT="$2"; shift ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
  shift
done

cd "$ROOT"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "注意：有未提交的改动，安装包只包含已提交的代码。"
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar --prefix=Unstuck/ HEAD | tar -x -C "$STAGE"

echo "生成网页…"
(cd "$STAGE/Unstuck/frontend" && npm ci --silent && npm run build --silent && rm -rf node_modules)

if [ -n "$WITH_ENV" ]; then
  cp backend/.env "$STAGE/Unstuck/backend/.env"
  chmod 600 "$STAGE/Unstuck/backend/.env"
  echo "已包含 backend/.env（你的 API key）：只发给信任的人。"
fi

mkdir -p "$(dirname "$OUT")"
OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
rm -f "$OUT"
(cd "$STAGE" && zip -q -r -X "$OUT" Unstuck)
echo "完成：$OUT（$(du -h "$OUT" | cut -f1)）"
