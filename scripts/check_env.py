"""`.env` の TYPESAFE_API_KEY を、値を表示せずに検証する。

静的チェック（形式）と、最小リクエストによる疎通チェック（認証が通るか）を行う。
疎通チェックは Noul 1問のみで、消費は数百トークン（$0.00002 未満）。
"""

from __future__ import annotations

import json
import stat
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
KEY_NAME = "TYPESAFE_API_KEY"
API_URL = "https://api.typesafe.ai/v1/systemone"
PLACEHOLDERS = frozenset({"", "sk-...", "your-api-key", "xxx", "changeme"})


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def read_entries(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    return [(k.strip().removeprefix("export ").strip(), v) for k, v in pairs]


def static_checks(path: Path) -> tuple[list[Check], str | None]:
    if not path.exists():
        return [Check("ファイル存在", False, f"{path} がありません")], None

    mode = stat.S_IMODE(path.stat().st_mode)
    checks = [
        Check("ファイル存在", True, str(path)),
        Check("パーミッション", mode == 0o600, f"{mode:o}（600 推奨）"),
    ]

    values = [v for k, v in read_entries(path) if k == KEY_NAME]
    checks.append(Check("キー定義", len(values) == 1, f"{KEY_NAME} の定義数: {len(values)}"))
    if len(values) != 1:
        return checks, None

    raw = values[0]
    value = raw.strip().strip("\"'")
    checks += [
        Check("空でない", value not in PLACEHOLDERS, "プレースホルダ/空" if value in PLACEHOLDERS else "値あり"),
        Check(
            "前後の空白・改行なし",
            raw == raw.strip() and "\r" not in raw,
            "余計な空白/CR を検出" if raw != raw.strip() else "OK",
        ),
        Check(
            "引用符なし",
            raw == raw.strip("\"'"),
            "引用符で囲まれている（uv の --env-file では除去される）" if raw != raw.strip("\"'") else "OK",
        ),
        Check(
            "ASCII のみ",
            value.isascii() and value.isprintable(),
            "全角文字/制御文字を検出" if not (value.isascii() and value.isprintable()) else "OK",
        ),
        Check(
            "内部に空白なし",
            not any(c.isspace() for c in value),
            "OK" if not any(c.isspace() for c in value) else "空白を含む",
        ),
        Check(
            "形式（参考）", True, f"長さ {len(value)} 文字 / 先頭 'sk-' {'あり' if value.startswith('sk-') else 'なし'}"
        ),
    ]
    return checks, value


def live_check(api_key: str) -> Check:
    body = {
        "state": "Help! My payouts have been failing for 3 days.",
        "model": "jev-latest",
        "questions": {"is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"}},
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload: dict[str, object] = json.loads(response.read())
    except urllib.error.HTTPError as e:
        # エラー本文にキーが反射されている可能性に備えて伏せ字にする
        text = e.read().decode(errors="replace").replace(api_key, "***")[:200]
        hint = {401: "キーが無効", 403: "権限なし/アーリーアクセス未承認", 429: "レート制限", 529: "サーバ過負荷"}.get(
            e.code, ""
        )
        return Check("疎通（API認証）", False, f"HTTP {e.code} {hint}: {text}")
    except urllib.error.URLError as e:
        return Check("疎通（API認証）", False, f"接続失敗: {e.reason}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    answers = payload.get("answers", {})
    noul = answers.get("is_urgent", {}).get("noul") if isinstance(answers, dict) else None
    return Check(
        "疎通（API認証）",
        True,
        f"HTTP 200 / model={payload.get('model')} / noul={noul} / usage={payload.get('usage')} / {elapsed_ms:.0f}ms",
    )


def main() -> int:
    checks, value = static_checks(ENV_PATH)
    static_ok = all(c.ok for c in checks)
    if static_ok and value is not None and "--offline" not in sys.argv:
        checks.append(live_check(value))

    for c in checks:
        print(f"[{'OK' if c.ok else 'NG'}] {c.name}: {c.detail}")

    all_ok = all(c.ok for c in checks)
    print("\n結果:", "すべて OK" if all_ok else "NG あり")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
