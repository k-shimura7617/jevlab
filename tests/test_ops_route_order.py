"""仕分けの順番（pipeline.py の表 R1〜R8・A2〜A3、docs/adr/0029）を 1 行ずつ固定する。

行を足す・順番を変えるときは、表・ADR・このテストを一緒に直す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jevlab.core.engine import AnswerView
from jevlab.ops import questions as oq
from jevlab.ops.models import IngestRequest, Item, Settings
from jevlab.ops.pipeline import after_route, decide_route
from jevlab.ops.store import Store


@dataclass(frozen=True)
class Case:
    """仕分けの入力。既定は「問い合わせを確信度 0.99 で、不満・緊急・割れ・材料不足なし」。"""

    category: str = "inquiry"
    confidence: float = 0.99
    top_two: tuple[float, float] = (0.95, 0.03)
    strong_frustration: float = 0.0
    urgent: bool = False
    insufficient: float = 0.0
    needs_reply: float = 0.9
    settings: dict[str, object] = field(default_factory=dict)


def views(c: Case) -> dict[str, AnswerView]:
    rest = {"inquiry", "complaint", "thanks", "other"} - {c.category}
    second = min(rest)
    probs = {c.category: c.top_two[0], second: c.top_two[1]}
    probs.update({k: 0.0 for k in rest - {second}})
    # 不満度は 1 と 2 に分ける（0 と 2 に分けると「判断が割れている」になる）
    frustration = {"0": 0.0, "1": 1 - c.strong_frustration, "2": c.strong_frustration}
    return {
        "category": AnswerView(type="choice", prediction=c.category, confidence=c.confidence, probabilities=probs),
        "frustration": AnswerView(
            type="score",
            prediction=2 if c.strong_frustration >= 0.5 else 0,
            value=1 + c.strong_frustration,
            confidence=0.9,
            probabilities=frustration,
        ),
        "urgent": AnswerView(type="noul", prediction=c.urgent, value=0.9 if c.urgent else 0.1),
        "insufficient": AnswerView(type="noul", prediction=c.insufficient >= 0.5, value=c.insufficient),
        oq.REPLY_ID: AnswerView(type="noul", prediction=c.needs_reply >= 0.5, value=c.needs_reply),
    }


def item_of(store: Store, c: Case) -> tuple[Item, Settings]:
    base = Settings()
    settings = base.model_copy(update={"classify": base.classify.model_copy(update=c.settings)})
    item = store.add_item(IngestRequest(channel="mail", subject="件名", body="本文"))
    item = store.update(
        item.id,
        lambda i: i.model_copy(update={"answers": views(c), "category": c.category, "confidence": c.confidence}),
    )
    return item, settings


ROUTE_ORDER: list[tuple[str, Case, str]] = [
    # R1 返信のいらない分類は、強い不満・緊急があっても自動（そのまま完了）
    ("R1", Case(category="thanks", strong_frustration=0.9, urgent=True), "routed"),
    # R1 に当たらない（確信度が低い）お礼は、ふつうの順番で見る
    ("R1-below", Case(category="thanks", confidence=0.6), "review"),
    ("R2", Case(strong_frustration=0.4), "escalated"),
    ("R2-off", Case(strong_frustration=0.4, settings={"escalate_strong_frustration": False}), "routed"),
    ("R3", Case(urgent=True), "escalated"),
    ("R3-off", Case(urgent=True, settings={"escalate_urgent": False}), "routed"),
    ("R4", Case(top_two=(0.5, 0.45)), "review"),
    ("R5", Case(insufficient=0.7), "review"),
    ("R5-off", Case(insufficient=0.7, settings={"insufficient_gate": False}), "routed"),
    ("R6", Case(), "routed"),
    ("R7", Case(confidence=0.6), "review"),
    ("R8", Case(confidence=0.3), "escalated"),
]


@pytest.mark.parametrize(("row", "case", "route"), ROUTE_ORDER, ids=[r for r, _, _ in ROUTE_ORDER])
def test_route_order(tmp_path: Path, row: str, case: Case, route: str) -> None:
    item, settings = item_of(Store(tmp_path / "ops.db"), case)
    assert decide_route(item, settings)[0] == route, row


AFTER_ROUTE: list[tuple[str, Case, str]] = [
    ("A2-thanks", Case(category="thanks"), "close"),
    # 返信の要否を判定する分類（その他）で、返信が要る確率が 0.5 未満
    ("A2-other", Case(category="other", needs_reply=0.2), "close"),
    ("A3", Case(), "assign"),
    ("A3-other", Case(category="other", needs_reply=0.8), "assign"),
    # 返信の要否を判定する分類でも、強い不満・緊急の兆しがあれば自動で完了にしない
    ("A3-other-urgent", Case(category="other", needs_reply=0.2, urgent=True), "assign"),
    # 返信のいらない分類は、緊急の兆しがあっても完了
    ("A2-thanks-urgent", Case(category="thanks", urgent=True), "close"),
]


@pytest.mark.parametrize(("row", "case", "then"), AFTER_ROUTE, ids=[r for r, _, _ in AFTER_ROUTE])
def test_after_route(tmp_path: Path, row: str, case: Case, then: str) -> None:
    item, settings = item_of(Store(tmp_path / "ops.db"), case)
    got, reason = after_route(item, settings)
    assert got == then, row
    assert (reason is not None) == (then == "close"), row
