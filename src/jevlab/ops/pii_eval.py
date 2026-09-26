"""個人情報の判定（Kev）の混同行列。

正解は、個人情報の確認で人が本文を見て決めた結果。人の確認に回らなかった件は誰も見ていないので含めない。
- 候補: モデルが判定した候補（氏名など。規則で確定した種類は除く）ごとに、モデルの判定と人の判断を比べる
- 候補外の残り: 件ごとに「候補外に残っている可能性」が閾値以上だったかと、人が候補外の箇所を足したかを比べる
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from jevlab.ops.models import Event, Item
from jevlab.ops.pii import Span


class Matrix(BaseModel):
    """行: モデル（個人情報とした / しなかった）、列: 人（個人情報 / でない）。"""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, actual: bool) -> None:
        if predicted and actual:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif actual:
            self.fn += 1
        else:
            self.tn += 1


class PiiEval(BaseModel):
    items: int
    candidates: Matrix
    leftover: Matrix
    leftover_threshold: float


def _model_candidates(events: list[Event]) -> list[Span]:
    """モデルが判定した候補（最後のガードの判定）。規則だけの判定・規則で確定した候補は含めない。"""
    judged = [e for e in events if e.kind == "guard" and e.actor != "system" and "candidates" in e.data]
    if not judged:
        return []
    spans = [Span.model_validate(c) for c in judged[-1].data["candidates"]]
    return [s for s in spans if s.score is not None]


def _reviewed(event: Event) -> bool:
    # 以前の記録には reviewed が無い。一括で流した件は、本文を見たか分からないので除く
    value = event.data.get("reviewed")
    return value if isinstance(value, bool) else not event.message.startswith("一括")


def summarize(items: Iterable[Item], events: Iterable[Event], leftover_threshold: float) -> PiiEval:
    by_item: dict[str, list[Event]] = {}
    for e in events:
        by_item.setdefault(e.item_id, []).append(e)
    result = PiiEval(items=0, candidates=Matrix(), leftover=Matrix(), leftover_threshold=leftover_threshold)
    for item in items:
        evs = by_item.get(item.id, [])
        human = [e for e in evs if e.kind == "pii_review" and e.actor == "human"]
        if not human or not _reviewed(human[-1]):
            continue
        result.items += 1
        final = {(s.start, s.end) for s in item.pii if s.confirmed}
        candidates = _model_candidates(evs)
        for c in candidates:
            result.candidates.add(c.confirmed, (c.start, c.end) in final)
        if item.pii_leftover is not None:
            known = {(s.start, s.end) for s in candidates}
            added = any(s.source == "human" and s.confirmed and (s.start, s.end) not in known for s in item.pii)
            result.leftover.add(item.pii_leftover >= leftover_threshold, added)
    return result
