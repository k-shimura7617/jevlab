from __future__ import annotations

from typing import Any

from jevlab.ops.kev_queue import kev_calls, needs_kev, summarize
from jevlab.ops.models import Event, Item, Settings


def _item(n: int, **update: Any) -> Item:
    base = Item(
        id=f"T-{n:04d}",
        seq=n,
        channel="mail",
        from_name="",
        from_address="",
        subject="",
        body="本文",
        text="本文",
        received_at="2026-09-26T00:00:00+00:00",
        updated_at="2026-09-26T00:00:00+00:00",
    )
    return base.model_copy(update=update)


def _event(n: int, latency: float | None) -> Event:
    return Event(
        id=n,
        item_id="T-0001",
        at=f"2026-09-26T00:00:{n:02d}+00:00",
        kind="guard",
        actor="kev",
        message="",
        data={} if latency is None else {"latency_ms": latency},
    )


def test_needs_kev_follows_the_settings() -> None:
    s = Settings()  # 既定: ガードは Kev のモデルで判定、仕分けは Jev
    assert needs_kev(_item(1), s)
    guarded = _item(2, pii_decision="masked")
    assert not needs_kev(guarded, s)
    rules_only = s.model_copy(update={"guard": s.guard.model_copy(update={"use_model": False})})
    assert not needs_kev(_item(3), rules_only)
    kev_first = s.model_copy(update={"kev_first": s.kev_first.model_copy(update={"enabled": True})})
    assert needs_kev(guarded, kev_first)
    assert not needs_kev(_item(4, backfill=True), s)


def test_summarize_estimates_time_from_recent_latencies() -> None:
    items = [_item(1), _item(2, status="processing"), _item(3, pii_decision="masked"), _item(4, status="routed")]
    events = [_event(3, 2000.0), _event(2, 4000.0), _event(1, None)]
    q = summarize(items, events, Settings(), concurrency=1)
    # Kev を待つのは T-0001・T-0002。中央値 3 秒 × 2 件 = 6 秒
    assert (q.waiting, q.running, q.median_ms, q.samples, q.eta_s) == (2, 1, 3000.0, 2, 6.0)
    assert q.last_at == "2026-09-26T00:00:03+00:00"
    empty = summarize([], [], Settings(), concurrency=1)
    assert empty.waiting == 0 and empty.eta_s is None and empty.last_at is None


def test_items_that_use_kev_twice_count_twice_with_phase_medians() -> None:
    s = Settings()
    both = s.model_copy(update={"classify": s.classify.model_copy(update={"target": "custom"})})
    assert kev_calls(_item(1), both) == ["guard", "classify"]
    assert kev_calls(_item(2, pii_decision="masked"), both) == ["classify"]
    guard_ev = _event(2, 1000.0)
    classify_ev = _event(1, 3000.0).model_copy(update={"kind": "classify"})
    q = summarize([_item(1), _item(2, pii_decision="masked")], [guard_ev, classify_ev], both, concurrency=1)
    # T-0001 はチェック 1 秒＋仕分け 3 秒、T-0002 は仕分け 3 秒 → 7 秒
    assert (q.waiting, q.calls, q.eta_s) == (2, 3, 7.0)
