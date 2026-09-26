"""評価の実行結果（集計のみ）の記録。

接続先（mock / custom / jev）ごとの前回結果を比べられるよう、1 回の評価を 1 行の JSONL に追記する。
ケースごとの詳細は保存しない。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from jevlab.core import target
from jevlab.core.engine import EvalReport


class QuestionScore(BaseModel):
    id: str
    accuracy: float


class RunRecord(BaseModel):
    at: str
    app: str
    mode: target.Target
    model: str
    n: int
    total: int
    mean_accuracy: float
    questions: list[QuestionScore]
    mean_latency_ms: float
    total_cost_usd: float


def default_path() -> Path:
    return Path(os.environ.get("JEVLAB_VAR_DIR", "var")) / "runs.jsonl"


def to_record(report: EvalReport, *, total: int, mode: target.Target) -> RunRecord:
    questions = [QuestionScore(id=m.id, accuracy=m.accuracy) for m in report.questions]
    return RunRecord(
        at=datetime.now(UTC).isoformat(timespec="seconds"),
        app=report.app,
        mode=mode,
        model=report.cases[0].result.model if report.cases else "-",
        n=report.n,
        total=total,
        mean_accuracy=sum(q.accuracy for q in questions) / len(questions) if questions else 0.0,
        questions=questions,
        mean_latency_ms=report.mean_latency_ms,
        total_cost_usd=report.total_cost_usd,
    )


def append(path: Path, record: RunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")


def load(path: Path) -> list[RunRecord]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [RunRecord.model_validate_json(line) for line in lines if line.strip()]


def latest_by_app_mode(records: list[RunRecord]) -> list[RunRecord]:
    """アプリ×接続先ごとの最新 1 件。記録は追記順なので後勝ちでよい。"""
    latest = {(r.app, r.mode): r for r in records}
    return list(latest.values())
