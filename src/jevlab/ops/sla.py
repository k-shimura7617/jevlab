"""エスカレーションの対応目安（営業時間で数える）。

営業時間（曜日と開始〜終了の時刻）の外と休日は数えない。昼休みは区別しない。
例: 対応目安 9 営業時間、営業時間が平日 9:00〜18:00 なら、金曜 17:00 に届いた件の期限は翌週月曜 17:00。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jevlab.ops.models import SlaSettings


def _day_window(day: date, sla: SlaSettings, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    if day.weekday() not in sla.days or day.isoformat() in sla.holidays:
        return None
    return (
        datetime.combine(day, time.fromisoformat(sla.start), tz),
        datetime.combine(day, time.fromisoformat(sla.end), tz),
    )


def business_minutes(start: datetime, end: datetime, sla: SlaSettings) -> float:
    """start から end までのうち、営業時間に入る分数。"""
    tz = ZoneInfo(sla.timezone)
    s, e = start.astimezone(tz), end.astimezone(tz)
    if e <= s:
        return 0.0
    total = 0.0
    day = s.date()
    while day <= e.date():
        window = _day_window(day, sla, tz)
        if window is not None:
            a, b = max(s, window[0]), min(e, window[1])
            if b > a:
                total += (b - a).total_seconds() / 60
        day += timedelta(days=1)
    return total


def minutes_left(received_at: str, now: datetime, sla: SlaSettings) -> float:
    """対応目安までの残り（営業時間の分）。過ぎていれば負の値。"""
    return sla.hours * 60 - business_minutes(datetime.fromisoformat(received_at), now, sla)
