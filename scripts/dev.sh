#!/usr/bin/env bash
# 開発用に 1 コマンドで起動する。
# FastAPI（自動リロード）と Vite dev サーバ（HMR）を同時に動かし、どちらかが止まれば両方止める。
#   scripts/dev.sh [--mock | --kev]   … 引数は serve.sh と同じ
# 画面は http://127.0.0.1:5173 で開く。/api は Vite が FastAPI（PORT、既定 8000）へ中継する。
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null; then
  echo "npm がありません。Node.js を入れてから再実行してください。" >&2
  exit 1
fi
[[ -d frontend/node_modules ]] || (cd frontend && npm install)

port="${PORT:-8000}"
# 子をそれぞれ独立したプロセスグループで起動し、停止時はグループごと止める（孫プロセスを残さない）
set -m
JEVLAB_DEV=1 scripts/serve.sh "$@" &
api_pid=$!
# Vite は端末のキー入力（h で操作一覧など）を読もうとする。
# set -m で別グループにした裏のプロセスが端末を読むと SIGTTIN で止まり、画面が開かなくなるため、入力は /dev/null にする
(cd frontend && JEVLAB_API_URL="http://127.0.0.1:$port" exec npm run dev </dev/null) &
web_pid=$!

# Ctrl+C や片方の異常終了時に、両方のプロセスグループを止める
stop() {
  trap - EXIT INT TERM
  kill -TERM -- "-$api_pid" "-$web_pid" 2>/dev/null || true
  # 一時停止中のプロセスは TERM を受け取れないので、再開させて終了させる
  kill -CONT -- "-$api_pid" "-$web_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop EXIT INT TERM

set +e
wait -n "$api_pid" "$web_pid"
status=$?
set -e
echo "dev を停止しました（終了コード $status）。" >&2
exit "$status"
