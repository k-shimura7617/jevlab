"""自動振り分けの閾値の調整。

正解が分かっている件（人が確認・修正した件、抜き取りの結果、デモの想定ラベル）から、
閾値を動かしたときの「自動で振り分ける割合」と「自動で振り分けた分の誤り率」を出し、
目標の誤り率を満たす一番低い閾値を提案する。反映は人が決める（自動では変えない）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from pydantic import BaseModel

from jevlab.ops.models import Item

TruthSource = Literal["human", "expected"]
# これより件数が少ないと誤り率がぶれるため提案しない
MIN_SAMPLES: Final = 20
# 自動で振り分ける件がこれより少ない閾値は、誤り率が偶然 0 になりやすいため提案に使わない
MIN_AUTO: Final = 10
_STEPS: Final = [round(0.5 + i * 0.01, 2) for i in range(50)]


class Labeled(BaseModel):
    id: str
    predicted: str
    truth: str
    confidence: float


class CurvePoint(BaseModel):
    threshold: float
    auto: int
    errors: int
    auto_rate: float
    error_rate: float | None


class Curve(BaseModel):
    label: str | None
    n: int
    points: list[CurvePoint]
    recommended: float | None
    note: str


class TuningReport(BaseModel):
    source: TruthSource
    target_error: float
    n: int
    overall: Curve
    by_label: list[Curve]


def _model_prediction(item: Item) -> str | None:
    """人が確定する前の、モデルの予測。"""
    a = item.answers.get("category")
    return str(a.prediction) if a is not None else None


def labeled_items(items: Iterable[Item], source: TruthSource) -> list[Labeled]:
    out: list[Labeled] = []
    for item in items:
        predicted = _model_prediction(item)
        if predicted is None or item.confidence is None:
            continue
        if source == "expected":
            truth = (item.expected or {}).get("category")
        elif item.backfill:
            # 過去の問い合わせは人が確認していない（分類は取り込んだ過去の分類として expected にある）
            truth = None
        else:
            # 人が確認した件だけを正解として使う（自動で振り分けて誰も見ていない件は含めない）
            human_checked = (
                item.status == "closed"
                or item.audit_result is not None
                or (item.first_route == "review" and item.status == "routed")
            )
            truth = item.category if human_checked else None
        if isinstance(truth, str):
            out.append(Labeled(id=item.id, predicted=predicted, truth=truth, confidence=item.confidence))
    return out


def curve(rows: list[Labeled], target_error: float, label: str | None) -> Curve:
    points: list[CurvePoint] = []
    for t in _STEPS:
        auto = [r for r in rows if r.confidence >= t]
        errors = sum(1 for r in auto if r.predicted != r.truth)
        points.append(
            CurvePoint(
                threshold=t,
                auto=len(auto),
                errors=errors,
                auto_rate=len(auto) / len(rows) if rows else 0.0,
                error_rate=errors / len(auto) if auto else None,
            )
        )
    if len(rows) < MIN_SAMPLES:
        return Curve(
            label=label,
            n=len(rows),
            points=points,
            recommended=None,
            note=f"正解のある件が {len(rows)} 件（{MIN_SAMPLES} 件未満）のため提案しません",
        )
    ok = [p for p in points if p.error_rate is not None and p.error_rate <= target_error and p.auto >= MIN_AUTO]
    if not ok:
        return Curve(
            label=label,
            n=len(rows),
            points=points,
            recommended=None,
            note="どの閾値でも目標の誤り率を満たしません（人の確認を続けます）",
        )
    best = min(ok, key=lambda p: p.threshold)
    return Curve(
        label=label,
        n=len(rows),
        points=points,
        recommended=best.threshold,
        note=f"閾値 {best.threshold:.2f} で自動 {best.auto_rate:.0%}・誤り率 {best.error_rate or 0:.1%}（{best.errors}/{best.auto} 件）",
    )


def report(items: Iterable[Item], source: TruthSource, target_error: float, labels: Iterable[str]) -> TuningReport:
    rows = labeled_items(items, source)
    # 分類ごとの閾値は「その分類と予測した件」で決める（自動で振り分けるかは予測した分類の閾値で判断するため）
    by_label = [curve([r for r in rows if r.predicted == k], target_error, k) for k in labels]
    return TuningReport(
        source=source,
        target_error=target_error,
        n=len(rows),
        overall=curve(rows, target_error, None),
        by_label=by_label,
    )
