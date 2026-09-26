from __future__ import annotations

import io
import json
import zipfile
from email.message import EmailMessage

import pytest

from jevlab.ops.importers import FileFormatError, parse_file


def _xlsx(rows: list[list[str]]) -> bytes:
    """文字列だけの最小の .xlsx（共有文字列を使う）。"""
    strings = [v for r in rows for v in r]
    cols = "ABCDEFGHIJ"
    sheet_rows = "".join(
        f'<row r="{i + 1}">'
        + "".join(f'<c r="{cols[j]}{i + 1}" t="s"><v>{strings.index(v)}</v></c>' for j, v in enumerate(r) if v != "")
        + "</row>"
        for i, r in enumerate(rows)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook {ns} {rel_ns}><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet_rows}</sheetData></worksheet>")
    return buf.getvalue()


def _mail(subject: str, body: str, sender: str = "山田 花子 <hanako@example.com>") -> EmailMessage:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "support@komorebi.example"
    m["Subject"] = subject
    m["Date"] = "Fri, 25 Sep 2026 10:00:00 +0900"
    m.set_content(body)
    return m


def test_csv_in_shift_jis_and_utf8_bom() -> None:
    text = "件名,本文,分類\n箱が潰れていた,交換してください,クレーム\n"
    for data in (text.encode("cp932"), b"\xef\xbb\xbf" + text.encode("utf-8")):
        parsed = parse_file("export.csv", data)
        assert parsed.kind == "table"
        assert parsed.table == [["件名", "本文", "分類"], ["箱が潰れていた", "交換してください", "クレーム"]]


def test_xlsx_first_sheet() -> None:
    parsed = parse_file("管理表.xlsx", _xlsx([["件名", "本文"], ["在庫", "再入荷しますか"], ["", "空の件名"]]))
    assert parsed.table == [["件名", "本文"], ["在庫", "再入荷しますか"], ["", "空の件名"]]


def test_eml_and_mbox() -> None:
    eml = parse_file("m.eml", _mail("注文について", "届いていません").as_bytes())
    assert eml.kind == "messages" and eml.channel == "mail"
    (row,) = eml.messages
    assert (row.from_name, row.from_address, row.subject) == ("山田 花子", "hanako@example.com", "注文について")
    assert row.body == "届いていません" and row.received_at is not None and row.received_at.startswith("2026-09-25")
    box = b"".join(
        b"From MAILER-DAEMON Fri Sep 25 10:00:00 2026\n" + _mail(f"件{i}", f"本文{i}").as_bytes() + b"\n"
        for i in range(3)
    )
    mbox = parse_file("Takeout.mbox", box)
    assert [m.subject for m in mbox.messages] == ["件0", "件1", "件2"]


def test_html_only_mail_is_converted_to_text() -> None:
    m = EmailMessage()
    m["From"] = "a@example.com"
    m["Subject"] = "HTML"
    m.set_content("<p>一行目</p><p>二行目 &amp; 記号</p>", subtype="html")
    (row,) = parse_file("h.eml", m.as_bytes()).messages
    assert "一行目" in row.body and "二行目 & 記号" in row.body and "<p>" not in row.body


def test_slack_export_takes_top_level_human_messages() -> None:
    day = [
        {"type": "message", "user": "U1", "text": "注文の件です <@U2>", "ts": "1790000000.000100"},
        {
            "type": "message",
            "user": "U1",
            "text": "スレッドの返信",
            "ts": "1790000001.0",
            "thread_ts": "1790000000.000100",
        },
        {"type": "message", "subtype": "channel_join", "user": "U3", "text": "参加しました", "ts": "1790000002.0"},
        {"type": "message", "bot_id": "B1", "text": "ボット", "ts": "1790000003.0"},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("channels.json", "[]")
        z.writestr("support/2026-09-25.json", json.dumps(day))
    parsed = parse_file("export.zip", buf.getvalue())
    assert parsed.channel == "slack"
    (row,) = parsed.messages
    assert row.body == "注文の件です" and row.source == "#support" and row.from_name == "Slack ユーザー U1"


@pytest.mark.parametrize("name", ["old.xls", "mail.msg", "archive.pst", "note.pdf"])
def test_unsupported_formats_explain_what_to_use(name: str) -> None:
    with pytest.raises(FileFormatError):
        parse_file(name, b"data")
