from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jevlab.core.confidence import choice_confidence, score_confidence
from jevlab.core.engine import AnswerView
from jevlab.ops import misses, tuning
from jevlab.ops import questions as oq
from jevlab.ops.models import IngestRequest, Item, MissReport, Settings
from jevlab.ops.pii import Span
from jevlab.ops.pipeline import Pipeline, decide_route, safe_title
from jevlab.ops.store import Store


def make_item(**update: Any) -> Item:
    base = Item(
        id="T-0001",
        seq=1,
        channel="mail",
        from_name="",
        from_address="",
        subject="",
        body="x",
        text="x",
        received_at="2026-09-26T00:00:00+00:00",
        updated_at="2026-09-26T00:00:00+00:00",
    )
    return base.model_copy(update=update)


def answers(conf: float, frustration: int = 0, urgent: float = 0.1) -> dict[str, AnswerView]:
    return {
        "category": AnswerView(type="choice", prediction="inquiry", confidence=conf, probabilities={"inquiry": conf}),
        "frustration": AnswerView(type="score", prediction=frustration, value=float(frustration), confidence=0.9),
        "urgent": AnswerView(type="noul", prediction=urgent >= 0.5, value=urgent),
    }


def test_field_candidates_and_questions() -> None:
    text = "前回の KM-250901-0001 ではなく今回の KM-250914-0031 です。9月28日までに3,300円を返金してください。明日連絡します"
    cands = oq.field_candidates(text)
    assert cands["order_id"] == ["KM-250901-0001", "KM-250914-0031"]
    assert cands["amount"] == ["3,300円"]
    assert cands["due_date"] == ["9月28日", "明日"]
    qs = oq.field_questions(cands)
    order = qs["order_id"]
    assert order.type == "choice"
    assert set(order.criteria) == {"KM-250901-0001", "KM-250914-0031", oq.NONE_KEY}
    assert oq.picked_value(cands, "order_id", "KM-250914-0031") == "KM-250914-0031"
    assert oq.picked_value(cands, "order_id", oq.NONE_KEY) is None
    assert oq.picked_value(cands, "order_id", "KM-999999-9999") is None
    assert oq.field_questions({"order_id": []}) == {}


def labeled(conf: float, ok: bool, i: int) -> Item:
    return make_item(
        id=f"T-{i:04d}",
        seq=i,
        status="routed",
        first_route="review",
        category="inquiry",
        confidence=conf,
        answers={"category": AnswerView(type="choice", prediction="inquiry" if ok else "other", confidence=conf)},
    )


def test_tuning_recommends_lowest_threshold_meeting_target() -> None:
    # 確信度 0.8 以上はすべて正しく、それ未満は半分誤り
    items = [labeled(0.8 + i * 0.005, True, i) for i in range(30)] + [
        labeled(0.6, i % 2 == 0, 100 + i) for i in range(10)
    ]
    report = tuning.report(items, "human", 0.02, ["inquiry", "other"])
    assert report.n == 40
    assert report.overall.recommended is not None
    assert 0.6 < report.overall.recommended <= 0.8
    point = next(p for p in report.overall.points if p.threshold == report.overall.recommended)
    assert point.error_rate == 0.0 and point.auto == 30


def test_tuning_needs_enough_samples_and_ignores_unchecked() -> None:
    few = [labeled(0.9, True, i) for i in range(5)]
    assert tuning.report(few, "human", 0.02, ["inquiry"]).overall.recommended is None
    # 自動で振り分けて誰も見ていない件は、人の確認を正解に使うときは数えない
    unchecked = [labeled(0.95, True, i).model_copy(update={"first_route": "routed"}) for i in range(30)]
    assert tuning.report(unchecked, "human", 0.02, ["inquiry"]).n == 0


def test_store_recover_requeues_processing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from jevlab.ops.models import IngestRequest
    from jevlab.ops.store import Store

    store = Store(tmp_path / "ops.db")
    item = store.add_item(IngestRequest(channel="mail", body="x"))
    assert store.claim_next() is not None
    assert store.get(item.id).status == "processing"
    assert store.recover() == 1
    assert store.get(item.id).status == "queued"
    store.close()


def _choice(key: str, conf: float = 0.9) -> dict[str, AnswerView]:
    return {oq.ASSIGNEE_ID: AnswerView(type="choice", prediction=key, confidence=conf, probabilities={key: conf})}


def test_assign_suggestion_ignores_none_and_unknown_ids() -> None:
    settings = Settings()
    assert Pipeline._suggestion(_choice("suzuki"), settings) == ("suzuki", 0.9)
    assert Pipeline._suggestion(_choice(oq.NONE_KEY), settings)[0] is None
    # 推定の後に削除された担当者は、推定なしとして扱う
    assert Pipeline._suggestion(_choice("deleted"), settings)[0] is None
    assert Pipeline._suggestion({}, settings) == (None, None)


def test_assign_examples_use_only_human_assigned_items_cleared_to_send(tmp_path: Path) -> None:
    store = Store(tmp_path / "ops.db")
    pipeline = Pipeline(store=store, backends={})

    def closed(subject: str, **update: Any) -> None:
        item = store.add_item(IngestRequest(channel="mail", subject=subject, body="本文"))
        store.update(item.id, lambda i: i.model_copy(update={"status": "closed", "assignee": "sato", **update}))

    closed("人が割り当て", pii_decision="none", assigned_by="human")
    closed("ブロックした件", pii_decision="blocked", assigned_by="human")
    closed("ガードなし", pii_decision="skipped", assigned_by="human")
    closed("自動のまま", pii_decision="none", assigned_by="auto")
    examples = pipeline._assign_examples(Settings())
    assert examples == {"sato": ["人が割り当て"]}


def test_safe_title_masks_unconfirmed_candidates_and_limits_length() -> None:
    text = "件名: 山田花子の件" + "あ" * 50 + "\n\n本文"
    span = Span(type="person_name", start=4, end=8, text="山田花子", source="rule", confirmed=False)
    title = safe_title(make_item(subject="x", text=text, pii=[span]), limit=24)
    assert "山田花子" not in title and len(title) <= 24


def test_safe_title_masks_candidates_when_guard_was_skipped() -> None:
    # ガードレールが無効で候補を調べていない件も、見出しは規則で拾った候補を伏せて出す
    text = "件名: 090-1234-5678 に連絡ください\n\n本文"
    title = safe_title(make_item(subject="x", text=text, pii_decision="skipped"))
    assert "090-1234-5678" not in title and "に連絡ください" in title


def _choice_view(probs: dict[str, float]) -> AnswerView:
    top = max(probs, key=lambda k: probs[k])
    return AnswerView(type="choice", prediction=top, confidence=0.99, probabilities=probs)


def _score_view(p0: float, p1: float, p2: float, conf: float) -> AnswerView:
    return AnswerView(
        type="score",
        prediction=round(p1 + 2 * p2),
        value=p1 + 2 * p2,
        confidence=conf,
        probabilities={"0": p0, "1": p1, "2": p2},
    )


def test_split_judgments_go_to_review() -> None:
    s = Settings()
    close = {**answers(0.99), "category": _choice_view({"inquiry": 0.48, "complaint": 0.44, "thanks": 0.08})}
    route, reason = decide_route(make_item(category="inquiry", confidence=0.95, answers=close), s)
    assert route == "review" and "僅差" in reason
    split = {**answers(0.99), "frustration": _score_view(0.5, 0.2, 0.3, 0.0)}
    s2 = s.model_copy(update={"classify": s.classify.model_copy(update={"strong_frustration_at": 0.5})})
    route, reason = decide_route(make_item(category="inquiry", confidence=0.95, answers=split), s2)
    assert route == "review" and "割れて" in reason
    clear = {**answers(0.99), "category": _choice_view({"inquiry": 0.9, "complaint": 0.05, "thanks": 0.05})}
    assert decide_route(make_item(category="inquiry", confidence=0.95, answers=clear), s)[0] == "routed"


def test_assign_suggestion_uses_probability_not_confidence() -> None:
    view = AnswerView(type="choice", prediction="suzuki", confidence=0.6, probabilities={"suzuki": 0.72, "none": 0.28})
    assert Pipeline._suggestion({oq.ASSIGNEE_ID: view}, Settings()) == ("suzuki", 0.72)


def test_official_confidence_formulas() -> None:
    assert round(score_confidence([0.9, 0.05, 0.05]), 3) == 0.775
    assert score_confidence([0.55, 0.05, 0.40]) == 0.0
    assert round(choice_confidence(0.925, 4), 3) == 0.9
    assert choice_confidence(0.2, 4) == 0.0


def test_assign_suggestion_without_probability_is_not_auto_assignable() -> None:
    view = AnswerView(type="choice", prediction="suzuki", confidence=0.95, probabilities={})
    assert Pipeline._suggestion({oq.ASSIGNEE_ID: view}, Settings()) == ("suzuki", None)


def test_mock_choice_picks_the_most_probable_option() -> None:
    from jevlab.core.client import _mock_answer

    for n in range(200):
        a = _mock_answer(f"state{n}", "q", {"type": "choice", "criteria": {"yes": "", "no": ""}})
        probs = a["probabilities"]
        assert isinstance(probs, dict)
        assert max(probs, key=lambda k: probs[k]) == a["choice"]


def test_seen_messages_are_pruned_after_30_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "ops.db")
    old = datetime(2026, 1, 1, tzinfo=UTC)
    assert store.mark_seen("a", now=old)
    assert not store.mark_seen("a", now=old + timedelta(days=1))
    # 30 日を過ぎた記録は、次に記録するときに消える
    assert store.mark_seen("b", now=old + timedelta(days=31))
    assert store.mark_seen("a", now=old + timedelta(days=31))


def _miss(item_id: str, leftover: float | None, type_: str = "sns_account") -> MissReport:
    return MissReport.model_validate(
        {"id": 1, "item_id": item_id, "type": type_, "start": 0, "end": 5, "length": 5, "leftover": leftover, "at": ""}
    )


def test_miss_summary_suggests_threshold_and_extra_reviews() -> None:
    reports = [
        _miss("T-1", 0.42),
        _miss("T-1", 0.42, "phone"),
        _miss("T-2", 0.18),
        _miss("T-3", 0.05),
        _miss("T-4", None),
    ]
    base = {
        "channel": "mail",
        "from_name": "",
        "from_address": "",
        "subject": "",
        "body": "",
        "received_at": "",
        "text": "",
        "updated_at": "",
    }
    items = [
        Item.model_validate({**base, "id": f"X-{i}", "seq": i, "pii_leftover": v})
        for i, v in enumerate([0.6, 0.42, 0.3, 0.18, 0.1, 0.05, None])
    ]
    s = misses.summarize(reports, items, catch=0.6, threshold=0.5)
    assert s.reports == 5 and s.by_type == {"sns_account": 4, "phone": 1}
    assert s.leftovers == [0.05, 0.18, 0.42] and s.unscored == 1
    # 3 件の 6 割（2 件）を確認に回すには 0.18 まで下げる
    assert s.suggested == 0.18 and s.caught_now == 0 and s.caught_suggested == 2
    assert (s.scored_items, s.review_now, s.review_suggested) == (6, 1, 4)
    # いまの閾値で足りていれば上げない
    assert misses.suggest([0.9, 0.8], 1.0, 0.5) == 0.5
    assert misses.suggest([], 0.8, 0.5) is None


def test_reopen_returns_an_auto_closed_item_to_routed(tmp_path: Path) -> None:
    store = Store(tmp_path / "ops.db")
    pipeline = Pipeline(store=store, backends={})
    item = store.add_item(IngestRequest(channel="mail", subject="ありがとう", body="本文"))
    store.update(item.id, lambda i: i.model_copy(update={"status": "routed", "category": "thanks"}))
    pipeline._auto_close(store.get(item.id), "返信のいらない分類（お礼）")
    assert store.get(item.id).status == "closed"
    reopened = pipeline.reopen(item.id)
    assert reopened.status == "routed" and not reopened.auto_closed and reopened.closed_at is None
    assert [e.kind for e in store.events(item.id)][-1] == "reopen"
    # 分類を直して完了にし直せる（人が直した記録になる）
    fixed = pipeline.close(item.id, "complaint")
    assert fixed.status == "closed" and fixed.category == "complaint" and fixed.decided_by == "human"
    # 人が完了にした件は、自動の完了ではないので取り消せない
    try:
        pipeline.reopen(item.id)
    except ValueError as e:
        assert "自動で完了" in str(e)
    else:
        raise AssertionError("人が完了にした件を取り消せてしまった")


def test_confusion_counts_predicted_against_truth() -> None:
    from jevlab.ops.tuning import Labeled, confusion

    rows = [
        Labeled(id="1", predicted="inquiry", truth="inquiry", confidence=0.9),
        Labeled(id="2", predicted="inquiry", truth="complaint", confidence=0.9),
        Labeled(id="3", predicted="inquiry", truth="inquiry", confidence=0.8),
        Labeled(id="4", predicted="thanks", truth="thanks", confidence=0.99),
    ]
    assert confusion(rows) == {"inquiry": {"inquiry": 2, "complaint": 1}, "thanks": {"thanks": 1}}
