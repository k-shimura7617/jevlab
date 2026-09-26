#!/usr/bin/env bash
# 検証用 Web アプリを起動する。1 つのサーバで Jev / Kev / モックを画面から切り替え・比較できる。
# 引数は既定の接続先を選ぶ（画面で最初に選ばれる接続先）。
#   scripts/serve.sh          … 既定 Jev（.env の TYPESAFE_API_KEY が必要）
#   scripts/serve.sh --kev    … 既定 Kev（ローカルの Kev サーバ。課金なし）
#   scripts/serve.sh --mock   … 既定モック（API を呼ばない）
# .env があれば読み込み、TYPESAFE_API_KEY があれば Jev も選べるようにする。
# PORT で待受ポートを変更できる（既定 8000）。127.0.0.1 のみで待ち受ける。
# KEV_URL で Kev の接続先を変更できる（既定 http://127.0.0.1:8009）。Kev は後から起動してもよい。
# JEVLAB_DEV=1 のときは frontend のビルドを省き、src/ の変更で自動リロードする（scripts/dev.sh 用）。
set -euo pipefail
cd "$(dirname "$0")/.."

# 画面は frontend/ の Vite ビルド出力を配信する。未ビルドなら先にビルドする
if [[ -z "${JEVLAB_DEV:-}" && ! -f src/jevlab/static/index.html ]]; then
  if ! command -v npm >/dev/null; then
    echo "frontend が未ビルドで npm もありません。Node.js を入れて cd frontend && npm install && npm run build を実行してください。" >&2
    exit 1
  fi
  echo "frontend をビルドします…" >&2
  (cd frontend && { [[ -d node_modules ]] || npm install; } && npm run build)
fi

port="${PORT:-8000}"
uvicorn_args=(jevlab.web:app --host 127.0.0.1 --port "$port")
[[ -n "${JEVLAB_DEV:-}" ]] && uvicorn_args+=(--reload --reload-dir src)
export KEV_URL="${KEV_URL:-http://127.0.0.1:8009}"
env_file=()
[[ -f .env ]] && env_file=(--env-file .env)

case "${1:-}" in
  --mock) export JEVLAB_DEFAULT_TARGET=mock ;;
  --kev)
    export JEVLAB_DEFAULT_TARGET=custom
    if ! curl -sS -o /dev/null --max-time 3 "$KEV_URL/v1/models"; then
      echo "注意: Kev サーバ ($KEV_URL) に接続できません。Kev を起動すると画面から選べるようになります。" >&2
    fi
    ;;
  "")
    if [[ ! -f .env ]]; then
      echo ".env がありません。.env.example をコピーして TYPESAFE_API_KEY を設定するか、--kev / --mock で起動してください。" >&2
      exit 1
    fi
    export JEVLAB_DEFAULT_TARGET=jev
    ;;
  *)
    echo "使い方: $0 [--mock | --kev]" >&2
    exit 2
    ;;
esac

exec uv run "${env_file[@]}" uvicorn "${uvicorn_args[@]}"
