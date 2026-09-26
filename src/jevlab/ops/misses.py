"""検知漏れの報告の集計。

報告のあった件の「候補以外に個人情報が残っている確率」（pii_leftover）を見て、
閾値をどこまで下げれば見逃しの何割を人の確認に回せたかと、その分増える確認の件数を出す。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel

from jevlab.ops.models import Item, MissReport


class MissSummary(BaseModel):
    reports: int
    by_type: dict[str, int]
    # 報告のあった件ごとの確率（昇順）。規則だけで判定した件などは確率が無く、unscored に数える
    leftovers: list[float]
    unscored: int
    catch: float
    threshold: float
    # 報告のあった件のうち、閾値以上で人の確認に回る（回った）件
    caught_now: int
    # catch の割合を確認に回せる閾値（いまの閾値より上にはしない）。確率のある件が無ければ None
    suggested: float | None
    caught_suggested: int
    # 確率のある全件のうち、閾値以上になる件（確認に回る件の上限。ほかの理由で回る件も含む）
    scored_items: int
    review_now: int
    review_suggested: int


def _floor2(v: float) -> float:
    return math.floor(round(v * 100, 6)) / 100


def suggest(leftovers: list[float], catch: float, threshold: float) -> float | None:
    """leftovers のうち catch の割合以上が閾値以上になる、最も高い閾値（0.01 刻み）。"""
    if not leftovers:
        return None
    ranked = sorted(leftovers, reverse=True)
    k = max(1, math.ceil(catch * len(ranked)))
    return min(_floor2(ranked[k - 1]), threshold)


def summarize(misses: Iterable[MissReport], items: Iterable[Item], catch: float, threshold: float) -> MissSummary:
    reports = list(misses)
    # 同じ件に複数の報告があっても、閾値で確認に回るかは件ごとに決まるので 1 件として数える
    per_item: dict[str, float | None] = {}
    for m in reports:
        per_item.setdefault(m.item_id, m.leftover)
    leftovers = sorted(v for v in per_item.values() if v is not None)
    scored = [i.pii_leftover for i in items if i.pii_leftover is not None]
    t = suggest(leftovers, catch, threshold)
    at = threshold if t is None else t
    return MissSummary(
        reports=len(reports),
        by_type=dict(Counter(m.type for m in reports).most_common()),
        leftovers=leftovers,
        unscored=sum(1 for v in per_item.values() if v is None),
        catch=catch,
        threshold=threshold,
        caught_now=sum(1 for v in leftovers if v >= threshold),
        suggested=t,
        caught_suggested=sum(1 for v in leftovers if v >= at),
        scored_items=len(scored),
        review_now=sum(1 for v in scored if v >= threshold),
        review_suggested=sum(1 for v in scored if v >= at),
    )
