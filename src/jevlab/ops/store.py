"""運用データの保存先（SQLite）。

件（チケット）・経過（監査ログ）・疑似 Slack の投稿・設定を持つ。
件は検索に使う列（状態・受信順）と、残りを JSON で持つ。
書き込みは 1 本のロックで直列化する（ローカルのデモ用途で、件数も少ないため）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jevlab.ops.models import Actor, Event, EventKind, IngestRequest, Item, MissReport, Post, Settings, Status

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status, seq);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL,
  message TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_item ON events(item_id, id);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,
  at TEXT NOT NULL,
  author TEXT NOT NULL,
  text TEXT NOT NULL,
  item_id TEXT,
  fields TEXT NOT NULL
);
-- 実際の Slack から受け取ったメッセージ（同じメッセージが再送されても 1 件だけ取り込む）
CREATE TABLE IF NOT EXISTS seen_messages (
  key TEXT PRIMARY KEY,
  at TEXT NOT NULL
);
-- 検知漏れの報告（個人情報そのものは残さない。種類・位置・長さだけ）
CREATE TABLE IF NOT EXISTS pii_misses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  type TEXT NOT NULL,
  start INTEGER NOT NULL,
  "end" INTEGER NOT NULL,
  leftover REAL,
  at TEXT NOT NULL,
  UNIQUE(item_id, start, "end")
);
-- 監査ログを CSV に出した記録（いつ・どの条件で・何行）
CREATE TABLE IF NOT EXISTS audit_exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  conditions TEXT NOT NULL,
  rows INTEGER NOT NULL
);
-- jevlab が実際の Slack に投稿したメッセージ（まとめて消すときに、同じボットのほかの投稿を消さないため）
CREATE TABLE IF NOT EXISTS slack_posts (
  channel TEXT NOT NULL,
  ts TEXT NOT NULL,
  PRIMARY KEY (channel, ts)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


# 実際の Slack から受け取ったメッセージの記録を残す日数（再送の重複を防ぐためだけに使う）
SEEN_KEEP_DAYS = 30


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def default_path() -> Path:
    return Path(os.environ.get("JEVLAB_VAR_DIR", "var")) / "ops.db"


class ItemNotFoundError(KeyError):
    pass


def compose_text(subject: str, body: str) -> str:
    """モデルに渡す文。件名があれば既存のメール仕分けと同じ「件名: …」の形にする。"""
    return f"件名: {subject}\n\n{body}" if subject.strip() else body


def _post(r: sqlite3.Row) -> Post:
    return Post(
        id=r["id"],
        channel=r["channel"],
        at=r["at"],
        author=r["author"],
        text=r["text"],
        item_id=r["item_id"],
        fields=json.loads(r["fields"]),
    )


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    # ---- 設定 ----

    def settings(self) -> Settings:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = 'settings'").fetchone()
        return Settings.model_validate_json(row["value"]) if row else Settings()

    def has_settings(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM settings WHERE key = 'settings'").fetchone() is not None

    def put_settings(self, settings: Settings) -> Settings:
        with self._tx() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('settings', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (settings.model_dump_json(),),
            )
        return settings

    def update_settings(self, f: Callable[[Settings], Settings]) -> Settings:
        return self.put_settings(f(self.settings()))

    # ---- 件 ----

    def add_item(self, req: IngestRequest) -> Item:
        at = now_iso()
        with self._tx() as db:
            # 番号は受付箱を空にしても戻さない（空にする前の処理の結果が、同じ番号の新しい件に書き込まれないように）
            row = db.execute("SELECT value FROM settings WHERE key = 'last_seq'").fetchone()
            last = max(
                int(row["value"]) if row else 0,
                db.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM items").fetchone()["n"],
            )
            seq = last + 1
            db.execute(
                "INSERT INTO settings(key, value) VALUES('last_seq', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(seq),),
            )
            item = Item(
                id=f"T-{seq:04d}",
                seq=seq,
                channel=req.channel,
                from_name=req.from_name,
                from_address=req.from_address,
                subject=req.subject,
                body=req.body,
                text=compose_text(req.subject, req.body),
                received_at=req.received_at if req.backfill and req.received_at else at,
                updated_at=at,
                expected=req.expected,
                backfill=req.backfill,
            )
            db.execute(
                "INSERT INTO items(id, seq, status, data) VALUES(?, ?, ?, ?)",
                (item.id, item.seq, item.status, item.model_dump_json()),
            )
        return item

    def get(self, item_id: str) -> Item:
        with self._lock:
            row = self._db.execute("SELECT data FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ItemNotFoundError(f"件 {item_id} はありません")
        return Item.model_validate_json(row["data"])

    def update(self, item_id: str, f: Callable[[Item], Item]) -> Item:
        """読み出し・変更・保存をロックの中で行う（処理ワーカーと画面操作の書き込みが重ならないように）。"""
        with self._tx() as db:
            row = db.execute("SELECT data FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise ItemNotFoundError(f"件 {item_id} はありません")
            item = f(Item.model_validate_json(row["data"])).model_copy(update={"updated_at": now_iso()})
            db.execute(
                "UPDATE items SET status = ?, data = ? WHERE id = ?", (item.status, item.model_dump_json(), item.id)
            )
        return item

    def items(self, statuses: Iterable[Status] | None = None, limit: int = 500) -> list[Item]:
        wanted = list(statuses) if statuses is not None else None
        with self._lock:
            if wanted is None:
                rows = self._db.execute("SELECT data FROM items ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            else:
                marks = ",".join("?" * len(wanted))
                rows = self._db.execute(
                    f"SELECT data FROM items WHERE status IN ({marks}) ORDER BY seq DESC LIMIT ?", (*wanted, limit)
                ).fetchall()
        return [Item.model_validate_json(r["data"]) for r in rows]

    def progress(self) -> tuple[int, int]:
        """受信した件数と、処理待ち（処理中を含む）の件数。過去の問い合わせ（試算用）は数えない。

        処理フローの進み具合を細かく見るためのもので、件を読み込まずに数だけ数える（軽い）。
        """
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS received, COALESCE(SUM(status IN ('queued', 'processing')), 0) AS waiting "
                "FROM items WHERE COALESCE(json_extract(data, '$.backfill'), 0) = 0"
            ).fetchone()
        return int(row["received"]), int(row["waiting"])

    def claim_next(self) -> Item | None:
        """処理待ちの最も古い件を処理中にして返す。"""
        with self._tx() as db:
            row = db.execute("SELECT data FROM items WHERE status = 'queued' ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                return None
            item = Item.model_validate_json(row["data"]).model_copy(
                update={"status": "processing", "updated_at": now_iso()}
            )
            db.execute(
                "UPDATE items SET status = ?, data = ? WHERE id = ?", (item.status, item.model_dump_json(), item.id)
            )
        return item

    def recover(self) -> int:
        """前回の終了時に処理中だった件を処理待ちに戻す。"""
        stuck = self.items(["processing"])
        for item in stuck:
            self.update(item.id, lambda i: i.model_copy(update={"status": "queued"}))
        return len(stuck)

    # ---- 経過（監査ログ） ----

    def add_event(
        self, item_id: str, kind: EventKind, actor: Actor, message: str, data: dict[str, Any] | None = None
    ) -> Event:
        at = now_iso()
        payload = data or {}
        with self._tx() as db:
            cur = db.execute(
                "INSERT INTO events(item_id, at, kind, actor, message, data) VALUES(?, ?, ?, ?, ?, ?)",
                (item_id, at, kind, actor, message, json.dumps(payload, ensure_ascii=False)),
            )
            event_id = cur.lastrowid
        if event_id is None:
            raise RuntimeError("経過の記録に失敗しました（ID が取得できません）")
        return Event(id=event_id, item_id=item_id, at=at, kind=kind, actor=actor, message=message, data=payload)

    def _events(self, sql: str, args: tuple[object, ...]) -> list[Event]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [
            Event(
                id=r["id"],
                item_id=r["item_id"],
                at=r["at"],
                kind=r["kind"],
                actor=r["actor"],
                message=r["message"],
                data=json.loads(r["data"]),
            )
            for r in rows
        ]

    def events(self, item_id: str) -> list[Event]:
        return self._events("SELECT * FROM events WHERE item_id = ? ORDER BY id", (item_id,))

    def events_of_kind(self, kinds: Sequence[str]) -> list[Event]:
        """指定した種類の経過を、全件から古い順に返す（集計用）。"""
        marks = ", ".join("?" for _ in kinds)
        return self._events(f"SELECT * FROM events WHERE kind IN ({marks}) ORDER BY id", tuple(kinds))

    def events_after(self, after_id: int, limit: int = 200) -> list[Event]:
        """id が after_id より大きい経過を古い順に返す（実際の Slack への返信用）。"""
        return self._events("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))

    def last_event_id(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(id) AS m FROM events").fetchone()
        return int(row["m"] or 0)

    def query_events(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        item_id: str | None = None,
        kinds: Sequence[str] = (),
        limit: int = 100_000,
    ) -> list[Event]:
        """監査ログの出力用。日時（UTC の ISO 8601。end は含まない）・件・種類で絞り、古い順に返す。"""
        where: list[str] = []
        args: list[object] = []
        if start:
            where.append("at >= ?")
            args.append(start)
        if end:
            where.append("at < ?")
            args.append(end)
        if item_id:
            where.append("item_id = ?")
            args.append(item_id)
        if kinds:
            where.append(f"kind IN ({', '.join('?' for _ in kinds)})")
            args.extend(kinds)
        sql = "SELECT * FROM events" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id LIMIT ?"
        return self._events(sql, (*args, limit))

    def clear_slack_refs(self, deleted: set[tuple[str, str]] | None = None) -> int:
        """件に控えた実際の Slack の投稿（親・そのほかの投稿）を忘れる。Slack 側の投稿を消した後に使う。

        deleted を渡すと、実際に消せた投稿（チャンネル, ts）の分だけ忘れる（途中で失敗したとき、残った投稿の控えを消さない）。
        """
        changed = 0
        gone_parent: dict[str, object] = {
            "slack_channel": None,
            "slack_ts": None,
            "slack_parent_pending": False,
            "slack_notified": False,
            "slack_reminded": False,
        }
        for item in self.items(limit=100_000):
            if deleted is None:
                if not (item.slack_ts or item.slack_more or item.slack_parent_pending):
                    continue
                update = {**gone_parent, "slack_more": []}
            else:
                parent_gone = item.slack_ts is not None and (item.slack_channel or "", item.slack_ts) in deleted
                more = [ref for ref in item.slack_more if (ref[0], ref[1]) not in deleted]
                if not parent_gone and more == item.slack_more:
                    continue
                update = {**(gone_parent if parent_gone else {}), "slack_more": more}
            self.update(item.id, lambda i, u=update: i.model_copy(update=u))
            changed += 1
        return changed

    # ---- 実際の Slack に投稿したメッセージ ----

    def add_slack_post(self, channel: str, ts: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO slack_posts(channel, ts) VALUES(?, ?)", (channel, ts))

    def slack_posts(self) -> set[tuple[str, str]]:
        with self._lock:
            rows = self._db.execute("SELECT channel, ts FROM slack_posts").fetchall()
        return {(str(r["channel"]), str(r["ts"])) for r in rows}

    def forget_slack_posts(self, pairs: set[tuple[str, str]]) -> None:
        with self._tx() as db:
            db.executemany("DELETE FROM slack_posts WHERE channel = ? AND ts = ?", list(pairs))

    def add_audit_export(self, conditions: str, rows: int) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO audit_exports(at, conditions, rows) VALUES(?, ?, ?)", (now_iso(), conditions, rows))

    def audit_exports(self, limit: int = 10) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT at, conditions, rows FROM audit_exports ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"at": r["at"], "conditions": r["conditions"], "rows": r["rows"]} for r in rows]

    def recent_events_by(self, actor: str, limit: int = 20) -> list[Event]:
        """主体ごとの最近の経過（新しい順）。Kev の所要時間の目安に使う。"""
        return self._events("SELECT * FROM events WHERE actor = ? ORDER BY id DESC LIMIT ?", (actor, limit))

    def recent_events(self, limit: int = 30, after: int = 0) -> list[Event]:
        return self._events("SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (after, limit))

    # ---- 疑似 Slack ----

    def add_post(
        self,
        channel: str,
        author: str,
        text: str,
        item_id: str | None = None,
        fields: dict[str, str | None] | None = None,
    ) -> Post:
        at = now_iso()
        payload = fields or {}
        with self._tx() as db:
            cur = db.execute(
                "INSERT INTO posts(channel, at, author, text, item_id, fields) VALUES(?, ?, ?, ?, ?, ?)",
                (channel, at, author, text, item_id, json.dumps(payload, ensure_ascii=False)),
            )
            post_id = cur.lastrowid
        if post_id is None:
            raise RuntimeError("投稿の記録に失敗しました（ID が取得できません）")
        return Post(id=post_id, channel=channel, at=at, author=author, text=text, item_id=item_id, fields=payload)

    # ---- 設定以外の小さな値（実際の Slack への転送の位置など） ----

    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (f"meta:{key}",)).fetchone()
        return str(row["value"]) if row else None

    def put_meta(self, key: str, value: str) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"meta:{key}", value),
            )

    def mark_seen(self, key: str, now: datetime | None = None) -> bool:
        """初めて見たメッセージなら記録して True。すでに見ていれば False。

        Slack の再送は数分以内なので、古い記録（SEEN_KEEP_DAYS 日より前）はここで消す。
        """
        at = now or datetime.now(UTC)
        cutoff = (at - timedelta(days=SEEN_KEEP_DAYS)).isoformat(timespec="milliseconds")
        with self._tx() as db:
            db.execute("DELETE FROM seen_messages WHERE at < ?", (cutoff,))
            cur = db.execute(
                "INSERT OR IGNORE INTO seen_messages(key, at) VALUES(?, ?)",
                (key, at.isoformat(timespec="milliseconds")),
            )
        return cur.rowcount == 1

    # ---- 検知漏れの報告 ----

    def add_miss(self, item_id: str, type_: str, start: int, end: int, leftover: float | None) -> MissReport:
        """報告を残す。同じ件の同じ範囲がすでにあれば PermissionError（二重に数えない）。"""
        at = now_iso()
        with self._tx() as db:
            cur = db.execute(
                'INSERT OR IGNORE INTO pii_misses(item_id, type, start, "end", leftover, at) VALUES(?, ?, ?, ?, ?, ?)',
                (item_id, type_, start, end, leftover, at),
            )
            miss_id = cur.lastrowid
        if cur.rowcount != 1 or miss_id is None:
            raise PermissionError("この範囲はすでに報告済みです")
        return MissReport.model_validate(
            {
                "id": miss_id,
                "item_id": item_id,
                "type": type_,
                "start": start,
                "end": end,
                "length": end - start,
                "leftover": leftover,
                "at": at,
            }
        )

    def misses(self) -> list[MissReport]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM pii_misses ORDER BY id").fetchall()
        return [MissReport.model_validate({**dict(r), "length": r["end"] - r["start"]}) for r in rows]

    def posts_after(self, after_id: int, limit: int = 100) -> list[Post]:
        """id が after_id より大きい投稿を古い順に返す（実際の Slack への転送用）。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM posts WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
            ).fetchall()
        return [_post(r) for r in rows]

    def last_post_id(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(id) AS m FROM posts").fetchone()
        return int(row["m"] or 0)

    def posts(self, limit: int = 300) -> list[Post]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_post(r) for r in rows]

    # ---- デモの初期化 ----

    def reset(self) -> None:
        """件・経過・投稿を消す。設定は残し、受信シミュレータだけ先頭に戻して止める。"""
        with self._tx() as db:
            db.execute("DELETE FROM items")
            db.execute("DELETE FROM events")
            db.execute("DELETE FROM posts")
            db.execute("DELETE FROM pii_misses")
            db.execute("DELETE FROM sqlite_sequence WHERE name IN ('events', 'posts', 'pii_misses')")
            # 投稿・経過の番号は 1 から振り直されるので、番号で覚えている転送の位置（Slack など）も 0 に戻す。
            # 戻さないと、空にした後の件数が前の位置に届くまで、新しい投稿が「送信済み」と見なされて飛ばされる
            db.execute(
                "UPDATE settings SET value = '0' WHERE key LIKE 'meta:%.last_post' OR key LIKE 'meta:%.last_event'"
            )
        self.update_settings(
            lambda s: s.model_copy(update={"simulator": s.simulator.model_copy(update={"playing": False, "cursor": 0})})
        )
