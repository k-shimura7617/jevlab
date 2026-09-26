from __future__ import annotations

from pathlib import Path

from jevlab.ops.models import IngestRequest
from jevlab.ops.pii import Span
from jevlab.ops.pii_eval import summarize
from jevlab.ops.store import Store


def _span(start: int, end: int, *, score: float | None, confirmed: bool, source: str = "rule") -> dict[str, object]:
    return Span.model_validate(
        {
            "start": start,
            "end": end,
            "type": "person_name",
            "text": "",
            "source": source,
            "score": score,
            "confirmed": confirmed,
        }
    ).model_dump()


def _reviewed_item(
    store: Store,
    model: list[dict[str, object]],
    final: list[dict[str, object]],
    leftover: float | None,
    *,
    message: str = "人が確認: 追加 0 件・除外 0 件",
    data: dict[str, object] | None = None,
) -> str:
    item = store.add_item(IngestRequest(channel="mail", subject="件名", body="本文です。山田と木村と佐藤"))
    store.add_event(item.id, "guard", "kev", "判定", {"candidates": model})
    store.add_event(item.id, "pii_review", "human", message, {"added": [], "reviewed": True} if data is None else data)
    store.update(
        item.id,
        lambda i: i.model_copy(update={"pii": [Span.model_validate(s) for s in final], "pii_leftover": leftover}),
    )
    return item.id


def test_candidates_and_leftover_matrix(tmp_path: Path) -> None:
    store = Store(tmp_path / "ops.db")
    # モデル: A=個人情報、B=でない、C=個人情報 ／ 人: A=個人情報、B=個人情報（見逃し）、C=でない（伏せすぎ）
    a, b, c = (0, 2), (3, 5), (6, 8)
    model = [
        _span(*a, score=0.9, confirmed=True),
        _span(*b, score=0.1, confirmed=False),
        _span(*c, score=0.8, confirmed=True),
    ]
    final = [
        _span(*a, score=0.9, confirmed=True),
        _span(*b, score=0.1, confirmed=True),
        _span(*c, score=0.8, confirmed=False),
    ]
    _reviewed_item(store, model, final, leftover=0.5)
    # 候補外に人が足した件（残りの判定 0.2 は見逃し）
    _reviewed_item(store, [], [_span(9, 11, score=None, confirmed=True, source="human")], leftover=0.2)
    # 一括で編集なしに流した件・規則だけの件（モデルの候補なし）は、候補の集計に入らない
    _reviewed_item(store, model, final, leftover=0.9, data={"added": [], "reviewed": False})
    _reviewed_item(store, model, final, leftover=0.9, message="一括で確認: 追加 0 件・除外 0 件", data={"added": []})
    # 人の確認に回らなかった件は含めない
    other = store.add_item(IngestRequest(channel="mail", subject="件名", body="本文"))
    store.add_event(other.id, "guard", "kev", "判定", {"candidates": model})

    r = summarize(store.items(), store.events_of_kind(["guard", "pii_review"]), leftover_threshold=0.5)
    assert r.items == 2
    assert (r.candidates.tp, r.candidates.fp, r.candidates.fn, r.candidates.tn) == (1, 1, 1, 0)
    # 1 件目: 0.5 ≥ 0.5 で疑ったが、人は何も足していない（伏せすぎ側）。2 件目: 0.2 で疑わなかったが、人が足した（見逃し）
    assert (r.leftover.tp, r.leftover.fp, r.leftover.fn, r.leftover.tn) == (0, 1, 1, 0)
