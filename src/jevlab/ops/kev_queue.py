"""Kev（ローカル・CPU）の処理待ち。

Kev は 1 件ずつしか処理しないため、詰まると個人情報のチェックや仕分けが止まって見える。
次の段階で Kev を使う件の数と、最近の Kev の所要時間から、処理が終わるまでの見込みを出す。
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median
from typing import Literal

from pydantic import BaseModel

from jevlab.ops.models import Event, Item, Settings

# 見込みに使う、最近の Kev の所要時間の件数（個人情報のチェックと仕分けを合わせて）
SAMPLES = 40


class KevQueue(BaseModel):
    # 次の段階で Kev を使う、処理待ち・処理中の件
    waiting: int
    running: int
    # 待っている件が、これから Kev を呼ぶ回数（個人情報のチェックと仕分けの両方なら 2 回）
    calls: int = 0
    # 最近の Kev の 1 回の所要時間（中央値）。記録がなければ None
    median_ms: float | None
    samples: int
    # 待っている件を処理し終えるまでの見込み（秒）。所要時間の記録がなければ None
    eta_s: float | None
    # 最後に Kev が判定を終えた時刻（止まっていないかの目印）
    last_at: str | None


Phase = Literal["guard", "classify"]


def kev_calls(item: Item, settings: Settings) -> list[Phase]:
    """この件が、これから Kev を何回（どの段階で）使うか。

    個人情報のチェックと仕分けの両方で Kev を使う件は 2 回と数える（1 回と数えると見込みが小さく出る）。
    """
    g = settings.guard
    calls: list[Phase] = []
    guard_pending = item.pii_decision is None and not item.backfill and g.enabled
    # 個人情報のチェックがまだで、モデルで判定するなら Kev（規則だけならモデルは呼ばない）
    if guard_pending and g.use_model and g.target == "custom":
        calls.append("guard")
    if item.pii_decision == "blocked":
        # ブロックした件は、Kev だけで仕分けるか、Kev で参考に判定する
        if g.target == "custom" and (g.blocked_route == "kev" or g.use_model):
            calls.append("classify")
        return calls
    if settings.classify.target == "custom" or settings.kev_first.enabled:
        calls.append("classify")
    return calls


def needs_kev(item: Item, settings: Settings) -> bool:
    """この件が、これから Kev を使うか。"""
    return bool(kev_calls(item, settings))


def _latency(e: Event) -> float | None:
    v = e.data.get("latency_ms")
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def summarize(pending: Iterable[Item], kev_events: Iterable[Event], settings: Settings, concurrency: int) -> KevQueue:
    needing = [(i, kev_calls(i, settings)) for i in pending if i.status in ("queued", "processing")]
    needing = [(i, c) for i, c in needing if c]
    events = list(kev_events)
    samples = [(e.kind, lat) for e in events if (lat := _latency(e)) is not None][:SAMPLES]
    latencies = [lat for _, lat in samples]
    med = median(latencies) if latencies else None
    # 段階ごとの所要時間（個人情報のチェックと仕分けでは問いの数が違い、時間も違う）。記録のない段階は全体の中央値で代える
    by_phase: dict[Phase, float | None] = {}
    for phase in ("guard", "classify"):
        own = [lat for kind, lat in samples if (kind == "guard") == (phase == "guard")]
        by_phase[phase] = median(own) if own else med
    total_ms = sum(by_phase[p] or 0.0 for _, calls in needing for p in calls) if med is not None else None
    return KevQueue(
        waiting=len(needing),
        running=sum(1 for i, _ in needing if i.status == "processing"),
        calls=sum(len(c) for _, c in needing),
        median_ms=round(med, 1) if med is not None else None,
        samples=len(latencies),
        eta_s=round(total_ms / 1000 / max(concurrency, 1), 1) if total_ms is not None else None,
        last_at=events[0].at if events else None,
    )
