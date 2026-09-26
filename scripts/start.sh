#!/usr/bin/env bash
# ビルド版を 1 コマンドで起動する。frontend を毎回ビルドしてから FastAPI で配信する。
#   scripts/start.sh [--mock | --kev]   … 引数は serve.sh と同じ
# 画面・API とも http://127.0.0.1:${PORT:-8000} で開く。
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null; then
  echo "npm がありません。Node.js を入れてから再実行してください。" >&2
  exit 1
fi
(cd frontend && { [[ -d node_modules ]] || npm install; } && npm run build)
exec scripts/serve.sh "$@"
