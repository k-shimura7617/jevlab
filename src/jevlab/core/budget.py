"""API 使用量の記録と予算ガード。

使用量は JSONL に追記し、累計コストが上限を超えたら以降の呼び出しを拒否する。
料金は入力トークンのみ課金（出力は無料）。
接続先ごとに記録ファイルを分け、課金のない接続先（モック・Kev など）は $0 で記録する。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from jevlab.core import target

USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
DEFAULT_CAP_USD = 1.00


class BudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageRecord:
    at: str
    app: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float


def cost_of(input_tokens: int) -> float:
    return input_tokens * USD_PER_INPUT_TOKEN


def load_records(path: Path) -> list[UsageRecord]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [UsageRecord(**json.loads(line)) for line in lines if line.strip()]


@dataclass(frozen=True)
class Ledger:
    path: Path
    cap_usd: float
    billed: bool = True

    @classmethod
    def for_target(cls, t: target.Target) -> Ledger:
        root = Path(os.environ.get("JEVLAB_VAR_DIR", "var"))
        # Jev 以外の記録は本番の累計と混ぜない
        name = "usage.jsonl" if t == "jev" else f"usage.{t}.jsonl"
        cap = float(os.environ.get("JEVLAB_BUDGET_USD", DEFAULT_CAP_USD))
        return cls(root / name, cap, billed=target.is_billed(t))

    @classmethod
    def from_env(cls) -> Ledger:
        return cls.for_target(target.current())

    def total_usd(self) -> float:
        return sum(r.cost_usd for r in load_records(self.path))

    def ensure_within(self, expected_usd: float = 0.0) -> None:
        if not self.billed:
            return
        total = self.total_usd()
        if total + expected_usd > self.cap_usd:
            raise BudgetExceededError(
                f"予算上限 ${self.cap_usd:.4f} に達するため中止しました（累計 ${total:.6f} + 見込み ${expected_usd:.6f}）。"
                " JEVLAB_BUDGET_USD で上限を変更できます。"
            )

    def record(self, *, app: str, model: str, input_tokens: int, output_tokens: int, latency_ms: float) -> UsageRecord:
        rec = UsageRecord(
            at=datetime.now(UTC).isoformat(timespec="seconds"),
            app=app,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 1),
            cost_usd=cost_of(input_tokens) if self.billed else 0.0,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        return rec
