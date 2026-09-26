"""Kev（ローカル・CPU）の処理待ち。

Kev は 1 件ずつしか処理しないため、詰まると個人情報のチェックや仕分けが止まって見える。
次の段階で Kev を使う件の数と、最近の Kev の所要時間から、処理が終わるまでの見込みを出す。
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median

from pydantic import BaseModel

from jevlab.ops.models import Event, Item, Settings

# 見込みに使う、最近の Kev の所要時間の件数
SAMPLES = 20


class KevQueue(BaseModel):
    # 次の段階で Kev を使う、処理待ち・処理中の件
    waiting: int
    running: int
    # 最近の Kev の 1 回の所要時間（中央値）。記録がなければ None
    median_ms: float | None
    samples: int
    # 待っている件を処理し終えるまでの見込み（秒）。所要時間の記録がなければ None
    eta_s: float | None
    # 最後に Kev が判定を終えた時刻（止まっていないかの目印）
    last_at: str | None


def needs_kev(item: Item, settings: Settings) -> bool:
    """この件が、これから Kev を使うか。"""
    g = settings.guard
    # 個人情報のチェックがまだで、モデルで判定するなら Kev（規則だけなら次の仕分けを見る）
    if item.pii_decision is None and not item.backfill and g.enabled and g.use_model and g.target == "custom":
        return True
    if item.pii_decision == "blocked":
        # ブロックした件は、Kev だけで仕分けるか、Kev で参考に判定する
        return g.target == "custom" and (g.blocked_route == "kev" or g.use_model)
    return settings.classify.target == "custom" or settings.kev_first.enabled


def summarize(pending: Iterable[Item], kev_events: Iterable[Event], settings: Settings, concurrency: int) -> KevQueue:
    needing = [i for i in pending if i.status in ("queued", "processing") and needs_kev(i, settings)]
    events = list(kev_events)
    latencies = [float(e.data["latency_ms"]) for e in events if isinstance(e.data.get("latency_ms"), int | float)][
        :SAMPLES
    ]
    med = median(latencies) if latencies else None
    return KevQueue(
        waiting=len(needing),
        running=sum(1 for i in needing if i.status == "processing"),
        median_ms=round(med, 1) if med is not None else None,
        samples=len(latencies),
        eta_s=round(len(needing) * med / 1000 / max(concurrency, 1), 1) if med is not None else None,
        last_at=events[0].at if events else None,
    )
