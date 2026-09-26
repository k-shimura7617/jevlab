"""監査ログ（経過）の CSV 出力。

情報システム部門・監査部門に「いつ・何が・どう判定され・誰が変えたか」を渡すためのもの。
本文と個人情報は出力しない（件の番号で jevlab の中を見れば分かる。CSV は外に出ると取り消せないため）。
- 受信の記録の差出人（名前・アドレス）と、メモの本文は伏せる
- ガードの記録の候補の文字列など、経過に付いた詳しいデータは出さない（変更の前後・モデル・所要時間だけ）
- 念のため、内容の文は Slack の見出しと同じ規則で個人情報の候補をすべて伏せる
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from jevlab.ops.models import Actor, Event, EventKind
from jevlab.ops.pii import detect, mask_all

Encoding = Literal["utf-8", "shift_jis"]
TZ: Final = ZoneInfo("Asia/Tokyo")

KIND_LABELS: Final[dict[EventKind, str]] = {
    "received": "受信",
    "guard": "個人情報のガード",
    "pii_review": "個人情報の確認",
    "classify": "仕分け",
    "route": "振り分け",
    "review": "分類の確認",
    "escalate": "エスカレーション",
    "assign": "担当",
    "note": "メモ",
    "close": "完了",
    "audit": "抜き取り確認",
    "error": "エラー",
    "retry": "再実行",
    "miss": "検知漏れの報告",
}
ACTOR_LABELS: Final[dict[Actor, str]] = {
    "system": "システム",
    "kev": "Kev",
    "jev": "Jev",
    "mock": "MOCK",
    "human": "人",
    "connector": "コネクタ",
}
HEADER: Final = ("日時", "件", "種類", "主体", "内容", "変更前", "変更後", "モデル", "所要時間（ミリ秒）")

_RECEIVED: Final = re.compile(r"で受信: .*$", re.DOTALL)


def safe_message(event: Event) -> str:
    """経過の文から、差出人・メモの本文・個人情報の候補を伏せる。"""
    if event.kind == "received":
        message = _RECEIVED.sub("で受信（差出人は伏せる）", event.message)
    elif event.kind == "note":
        message = "メモを追加（本文は伏せる）"
    else:
        message = event.message
    return mask_all(message, detect(message))


def _text(v: object) -> str:
    return "" if v is None else str(v)


def row(event: Event) -> list[str]:
    d: Mapping[str, object] = event.data
    at = datetime.fromisoformat(event.at).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    latency = d.get("latency_ms")
    return [
        at,
        event.item_id,
        KIND_LABELS.get(event.kind, event.kind),
        ACTOR_LABELS.get(event.actor, event.actor),
        safe_message(event),
        _text(d.get("before")),
        _text(d.get("after")),
        _text(d.get("model")),
        f"{latency:.0f}" if isinstance(latency, int | float) else "",
    ]


def to_csv(events: Iterable[Event], encoding: Encoding) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    for e in events:
        w.writerow(row(e))
    text = buf.getvalue()
    # Excel で開けるよう、UTF-8 は BOM を付ける。Shift_JIS にない文字は ? にする
    return text.encode("utf-8-sig") if encoding == "utf-8" else text.encode("cp932", errors="replace")


def day_range(start: date | None, end: date | None) -> tuple[str | None, str | None]:
    """日本時間の日付の範囲（end の日を含む）を、経過の日時（UTC の ISO 8601）と比べられる形にする。"""
    lo = datetime.combine(start, time.min, TZ).astimezone(UTC).isoformat(timespec="milliseconds") if start else None
    hi = (
        datetime.combine(end + timedelta(days=1), time.min, TZ).astimezone(UTC).isoformat(timespec="milliseconds")
        if end
        else None
    )
    return lo, hi
