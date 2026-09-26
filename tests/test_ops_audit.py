from __future__ import annotations

import csv
import io
from datetime import date

from jevlab.ops import audit
from jevlab.ops.models import Event


def _event(kind: str, message: str, **data: object) -> Event:
    return Event.model_validate(
        {
            "id": 1,
            "item_id": "T-0001",
            "at": "2026-09-26T01:02:03.000+00:00",
            "kind": kind,
            "actor": "human",
            "message": message,
            "data": data,
        }
    )


def test_sender_note_and_pii_are_masked() -> None:
    received = _event("received", "メール で受信: 山田 花子 <hanako@example.com>")
    assert audit.safe_message(received) == "メール で受信（差出人は伏せる）"
    note = _event("note", "メモ: 山田様に 090-0000-1234 で電話済み")
    assert audit.safe_message(note) == "メモを追加（本文は伏せる）"
    other = _event("close", "対応完了。連絡先 090-0000-1234")
    assert "090-0000-1234" not in audit.safe_message(other)


def test_row_has_labels_changes_and_local_time() -> None:
    e = _event(
        "close",
        "対応完了",
        before="inquiry",
        after="complaint",
        model="m",
        latency_ms=12.3,
        candidates=[{"text": "山田"}],
    )
    row = audit.row(e)
    assert row[:4] == ["2026-09-26 10:02:03", "T-0001", "完了", "人"]
    assert row[5:] == ["inquiry", "complaint", "m", "12"]
    # 経過に付いた詳しいデータ（候補の文字列）は出さない
    assert "山田" not in "".join(row)


def test_csv_encodings() -> None:
    events = [_event("received", "メール で受信: 顧客")]
    utf8 = audit.to_csv(events, "utf-8")
    assert utf8.startswith(b"\xef\xbb\xbf")
    sjis = audit.to_csv(events, "shift_jis").decode("cp932")
    assert next(csv.reader(io.StringIO(sjis)))[0] == "日時"


def test_day_range_is_japan_time() -> None:
    lo, hi = audit.day_range(date(2026, 9, 26), date(2026, 9, 26))
    assert lo == "2026-09-25T15:00:00.000+00:00" and hi == "2026-09-26T15:00:00.000+00:00"
    assert audit.day_range(None, None) == (None, None)
