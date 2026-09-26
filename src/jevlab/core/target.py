"""接続先の判定。環境変数だけから決まる。

1 つのサーバで 3 つの接続先を並べて使える（画面で切り替え・比較する）:
- mock: API を呼ばない。常に使える
- custom: Kev などの互換サーバ（ローカル実行のため課金なし扱い）。KEV_URL（既定 http://127.0.0.1:8009）
- jev: TypeSafe の Jev API（課金あり）。TYPESAFE_API_KEY があるときだけ使える

既定の接続先は JEVLAB_DEFAULT_TARGET で選ぶ。
未設定なら従来どおり JEVLAB_MOCK=1 → mock、TYPESAFE_BASE_URL あり → custom、それ以外 → jev。
"""

from __future__ import annotations

import os
from typing import Literal, get_args

Target = Literal["mock", "jev", "custom"]
TARGETS: tuple[Target, ...] = get_args(Target)
JEV_ENDPOINT = "https://api.typesafe.ai"
DEFAULT_KEV_URL = "http://127.0.0.1:8009"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def base_url() -> str:
    """従来の custom 起動（TYPESAFE_BASE_URL を互換サーバに向ける）の接続先。"""
    return _env("TYPESAFE_BASE_URL")


def kev_url() -> str:
    return (_env("KEV_URL") or base_url() or DEFAULT_KEV_URL).rstrip("/")


def kev_api_key() -> str:
    # Kev は KEV_API_KEY 未設定なら鍵を検証しない。SDK が鍵を必須とするためダミーを渡す
    if key := _env("KEV_API_KEY"):
        return key
    return (_env("TYPESAFE_API_KEY") if base_url() else "") or "local"


def jev_available() -> bool:
    # TYPESAFE_BASE_URL がある従来の custom 起動では、TYPESAFE_API_KEY は Kev 用のダミーなので Jev には使わない
    return bool(_env("TYPESAFE_API_KEY")) and not base_url()


def parse(value: str) -> Target:
    for t in TARGETS:
        if t == value:
            return t
    raise ValueError(f"接続先 {value!r} は不明です（{' / '.join(TARGETS)} のいずれか）")


def current() -> Target:
    """既定の接続先。"""
    if explicit := _env("JEVLAB_DEFAULT_TARGET"):
        return parse(explicit)
    if os.environ.get("JEVLAB_MOCK") == "1":
        return "mock"
    return "custom" if base_url() else "jev"


def is_billed(target: Target) -> bool:
    # mock は予算ガードを試せるよう Jev の料金を模擬する
    return target != "custom"


def endpoint(target: Target) -> str:
    return {"mock": "API を呼ばない", "jev": JEV_ENDPOINT, "custom": kev_url()}[target]
