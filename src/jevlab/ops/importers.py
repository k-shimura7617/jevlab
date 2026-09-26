"""既存の問い合わせのファイルを読む（CSV・Excel・メール・Slack のエクスポート）。

どれも標準ライブラリだけで読む（依存を増やさない）。
- 表（CSV・.xlsx）: 行の配列を返し、列の対応は画面で決める。CSV は UTF-8 と Shift_JIS（cp932）を自動で見分ける
- メール（.eml・mbox）: 1 通ずつ差出人・件名・本文・受信日時を取り出す。Gmail の Takeout は mbox、Outlook（Web）は .eml
- Slack のエクスポート（.zip）: チャンネルごと・日ごとの JSON から、人が書いた最上位のメッセージを取り出す
"""

from __future__ import annotations

import csv
import html
import io
import json
import mailbox
import re
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Final, Literal
from xml.etree import ElementTree

from pydantic import BaseModel

MAX_BODY: Final = 4000
MAX_ROWS: Final = 5000
_TRUNCATED: Final = "\n…（長いため省略）"


class FileFormatError(ValueError):
    """読めないファイル（形式の誤り・壊れている・大きすぎる）。"""


class MessageRow(BaseModel):
    from_name: str = ""
    from_address: str = ""
    subject: str = ""
    body: str
    received_at: str | None = None
    # Slack のエクスポートでのチャンネル名（メールは空）
    source: str = ""


class ParsedFile(BaseModel):
    kind: Literal["table", "messages"]
    channel: Literal["csv", "mail", "slack"]
    # kind == "table": 1 行目が見出しの表
    table: list[list[str]] = []
    # kind == "messages": 取り出したメッセージ
    messages: list[MessageRow] = []
    # 本文が空などで取り込めなかった件数
    skipped: int = 0
    note: str = ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - len(_TRUNCATED)] + _TRUNCATED


# ---- 表 ----


def decode_text(data: bytes) -> str:
    """UTF-8（BOM あり・なし）・UTF-16（BOM あり）・Shift_JIS を見分けて文字列にする。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        # Excel（日本語版）が保存する CSV は多くが Shift_JIS。cp932 は Windows の機種依存文字も含む
        return data.decode("cp932")
    except UnicodeDecodeError as e:
        raise FileFormatError("文字コードを判別できません（UTF-8 か Shift_JIS で保存してください）") from e


def parse_csv(data: bytes) -> list[list[str]]:
    text = decode_text(data)
    try:
        rows = [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]
    except csv.Error as e:
        raise FileFormatError(f"CSV の形式が不正です: {e}") from e
    return rows


_NS: Final = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col_index(ref: str) -> int:
    """セル番地（例: "AB12"）の列を 0 始まりの番号にする。"""
    letters = re.match(r"[A-Z]+", ref)
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_xlsx(data: bytes) -> list[list[str]]:
    """.xlsx の最初のシートを表にする（値だけ。書式・数式は読まない）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise FileFormatError("Excel ファイル（.xlsx）として読めません") from e
    names = set(zf.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")) for si in root.findall("m:si", _NS)]
    sheet = _first_sheet(zf, names)
    root = ElementTree.fromstring(zf.read(sheet))
    rows: list[list[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            kind = c.get("t")
            v = c.find("m:v", _NS)
            if kind == "s" and v is not None and v.text is not None:
                value = shared[int(v.text)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            else:
                value = v.text if v is not None and v.text is not None else ""
            cells[_col_index(c.get("r", "A1"))] = value
        if cells and any(x.strip() for x in cells.values()):
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _first_sheet(zf: zipfile.ZipFile, names: set[str]) -> str:
    """ブックの並びで最初のシートの XML のパス。"""
    try:
        book = ElementTree.fromstring(zf.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        first = book.find("m:sheets/m:sheet", _NS)
        rid = first.get(f"{{{_REL_NS}}}id") if first is not None else None
        for rel in rels:
            if rel.get("Id") == rid:
                target = rel.get("Target", "").lstrip("/")
                path = target if target.startswith("xl/") else f"xl/{target}"
                if path in names:
                    return path
    except KeyError:
        pass
    sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not sheets:
        raise FileFormatError("Excel ファイルにシートがありません")
    return sheets[0]


# ---- メール ----

_TAG: Final = re.compile(r"<[^>]+>")
_BLOCK_END: Final = re.compile(r"</(p|div|tr|li|h[1-6])>|<br\s*/?>", re.IGNORECASE)
_SCRIPT: Final = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def _html_to_text(markup: str) -> str:
    text = _SCRIPT.sub("", markup)
    text = _BLOCK_END.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", text)


def _mail_row(msg: EmailMessage) -> MessageRow | None:
    part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True)
            content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
        body = _html_to_text(content) if part.get_content_type() == "text/html" else str(content)
    body = _clip(body, MAX_BODY)
    if not body:
        return None
    name, address = (getaddresses([str(msg.get("From", ""))]) or [("", "")])[0]
    received: str | None = None
    if msg.get("Date"):
        try:
            dt = parsedate_to_datetime(str(msg["Date"]))
            received = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()
        except (TypeError, ValueError):
            received = None
    return MessageRow(
        from_name=_clip(name, 100),
        from_address=_clip(address, 200),
        subject=_clip(str(msg.get("Subject", "")), 200),
        body=body,
        received_at=received,
    )


def parse_eml(data: bytes) -> ParsedFile:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    row = _mail_row(msg) if isinstance(msg, EmailMessage) else None
    return ParsedFile(kind="messages", channel="mail", messages=[row] if row else [], skipped=0 if row else 1)


def parse_mbox(data: bytes) -> ParsedFile:
    # mailbox はファイルのパスから読むので、一時ファイルに書いてから読む
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mail.mbox"
        path.write_bytes(data)
        box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=policy.default).parse(f), create=False)
        rows, skipped = _collect(_mail_row(m) if isinstance(m, EmailMessage) else None for m in box)
        box.close()
    return ParsedFile(kind="messages", channel="mail", messages=rows, skipped=skipped)


def _collect(rows: Iterable[MessageRow | None]) -> tuple[list[MessageRow], int]:
    out: list[MessageRow] = []
    skipped = 0
    for r in rows:
        if r is None:
            skipped += 1
        elif len(out) < MAX_ROWS:
            out.append(r)
        else:
            skipped += 1
    return out, skipped


# ---- Slack のエクスポート ----

_MENTION: Final = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
_LINK: Final = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")


def _slack_text(text: str) -> str:
    text = _MENTION.sub("", text)
    text = _LINK.sub(lambda m: m.group(2) or m.group(1), text)
    return html.unescape(text).strip()


def _slack_row(channel: str, m: object) -> MessageRow | None:
    if not isinstance(m, dict) or m.get("type") != "message":
        return None
    # 編集・参加などの通知、ボットの投稿、スレッドの返信は取り込まない（受信の取り込みと同じ）
    if m.get("subtype") or m.get("bot_id"):
        return None
    ts, thread = str(m.get("ts", "")), m.get("thread_ts")
    if thread is not None and str(thread) != ts:
        return None
    body = _clip(_slack_text(str(m.get("text", ""))), MAX_BODY)
    if not body:
        return None
    try:
        received = datetime.fromtimestamp(float(ts), UTC).isoformat()
    except ValueError:
        received = None
    return MessageRow(
        from_name=f"Slack ユーザー {m.get('user', '不明')}",
        subject="",
        body=body,
        received_at=received,
        source=f"#{channel}",
    )


def parse_slack_zip(zf: zipfile.ZipFile) -> ParsedFile:
    rows: list[MessageRow | None] = []
    for name in sorted(zf.namelist()):
        path = PurePosixPath(name)
        # チャンネルのフォルダの中の、日ごとの JSON（例: general/2026-09-26.json）だけを読む
        if path.suffix != ".json" or len(path.parts) < 2:
            continue
        try:
            messages = json.loads(zf.read(name))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(messages, list):
            rows.extend(_slack_row(path.parts[-2], m) for m in messages)
    out, skipped = _collect(rows)
    if not out and not skipped:
        raise FileFormatError("Slack のエクスポート（チャンネルごとのフォルダに日ごとの JSON）が見つかりません")
    return ParsedFile(kind="messages", channel="slack", messages=out, skipped=skipped)


# ---- 振り分け ----


def parse_file(file_name: str, data: bytes) -> ParsedFile:
    """拡張子と中身から形式を決めて読む。"""
    ext = PurePosixPath(file_name.lower()).suffix
    if ext in (".csv", ".txt"):
        rows = parse_csv(data)
        return ParsedFile(kind="table", channel="csv", table=rows[: MAX_ROWS + 1])
    if ext == ".xlsx":
        rows = parse_xlsx(data)
        return ParsedFile(kind="table", channel="csv", table=rows[: MAX_ROWS + 1])
    if ext == ".eml":
        return parse_eml(data)
    if ext in (".mbox", ".mbx"):
        return parse_mbox(data)
    if ext == ".zip":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise FileFormatError("ZIP ファイルとして読めません") from e
        return parse_slack_zip(zf)
    if ext in (".xls", ".msg", ".pst", ".olm"):
        raise FileFormatError(
            f"{ext} は読めません。Excel は .xlsx か CSV で保存し、Outlook のメールは .eml で保存してください"
        )
    raise FileFormatError(f"{ext or '拡張子のない'} ファイルは読めません（CSV・.xlsx・.eml・.mbox・Slack の .zip）")
