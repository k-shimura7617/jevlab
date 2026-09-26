"""実際の Slack とのつなぎ込み（Socket Mode）。

アプリは 127.0.0.1 でだけ待ち受けていて、Slack から届く公開 URL がない。
そのため受信は Socket Mode（アプリから Slack へ WebSocket でつなぎに行く方式）で行う。

- 送信: 疑似チャンネルへの投稿のうち、設定で Slack のチャンネル ID を割り当てたものを chat.postMessage で流す。
  投稿は個人情報を伏せた見出しだけなので、そのまま流してよい。お問い合わせ窓口（元の本文）は流さない。
- 受信: 設定したチャンネルに人が書いたメッセージ（スレッドの返信・編集・ボットの投稿を除く）を受付箱に取り込む。

トークンは環境変数 SLACK_BOT_TOKEN（xoxb-）と SLACK_APP_TOKEN（xapp-）から読む。
どちらかがなければ、その方向は「未設定」として何もしない。
SDK は同期版を使う（追加の依存を増やさないため）。送信はスレッドに逃がし、受信はイベントループに戻してから取り込む。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, TypeVar

from pydantic import BaseModel

from jevlab.ops.models import Event, IngestRequest, Item, Post, Settings, StaffMember
from jevlab.ops.pipeline import ESCALATION_CHANNEL, ROUTE_CHANNELS, Pipeline
from jevlab.ops.sla import minutes_left
from jevlab.ops.store import ItemNotFoundError

log = logging.getLogger(__name__)

# 実際の Slack に流してよい疑似チャンネル（個人情報を伏せた見出しだけが投稿されるもの）
MIRRORABLE: Final[tuple[str, ...]] = (*dict.fromkeys([*ROUTE_CHANNELS.values(), "#cs-その他"]), ESCALATION_CHANNEL)
# 受信した本文の上限（受付の上限に合わせる）
MAX_BODY: Final = 4000
_TICK_S: Final = 2.0
# 対応目安の前の知らせを確かめる間隔
_REMIND_EVERY_S: Final = 30.0
# 送信に続けて失敗したときの待ち時間の上限
_MAX_BACKOFF_S: Final = 60.0

State = Literal["unconfigured", "off", "connecting", "on", "error"]


class SlackApi(Protocol):
    """Slack の Web API のうち使うもの（テストでは偽物に差し替える）。"""

    def auth_test(self) -> str:
        """ボット自身のユーザー ID を返す。"""
        ...

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        """投稿して、その投稿の ts（スレッドの親として使う）を返す。"""
        ...

    def find_message(self, channel: str, marker: str) -> str | None:
        """チャンネルの最近の投稿から、marker を含むボットの投稿を探して ts を返す（なければ None）。"""
        ...


class InboundHandle(Protocol):
    def close(self) -> None: ...

    def is_connected(self) -> bool: ...


# 受信の開始: メッセージを受け取る関数を渡すと、接続して閉じるためのハンドルを返す
InboundFactory = Callable[[Callable[[Mapping[str, object]], None]], InboundHandle]


class SlackSendError(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None, permanent: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        # 設定の誤り（チャンネルがない・招待されていない）など、やり直しても直らない失敗
        self.permanent = permanent


class DirectionStatus(BaseModel):
    state: State
    detail: str = ""
    count: int = 0
    last_error: str | None = None


class SlackStatus(BaseModel):
    bot_token: bool
    app_token: bool
    bot_user: str | None
    outbound: DirectionStatus
    inbound: DirectionStatus
    mirrorable: list[str]


# ---- 実際の SDK を使う実装 ----

# やり直しても直らない失敗（設定の見直しが必要）
_PERMANENT_ERRORS: Final = frozenset(
    {
        "channel_not_found",
        "not_in_channel",
        "is_archived",
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "missing_scope",
    }
)

T = TypeVar("T")


class SdkSlackApi:
    def __init__(self, bot_token: str) -> None:
        from slack_sdk import WebClient
        from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

        # 429（混み合い）は SDK が Retry-After に従って待ってやり直す
        self.client = WebClient(token=bot_token, retry_handlers=[RateLimitErrorRetryHandler(max_retry_count=2)])

    @staticmethod
    def _call(what: str, f: Callable[[], T]) -> T:
        """SDK の呼び出し。どんな失敗も SlackSendError にそろえる（通信の失敗・タイムアウトは一時的な失敗）。"""
        from slack_sdk.errors import SlackApiError

        try:
            return f()
        except SlackApiError as e:
            code = str(e.response.get("error"))
            retry = e.response.headers.get("Retry-After") if e.response.headers else None
            raise SlackSendError(
                f"{what}に失敗: {code}",
                retry_after=float(retry) if retry else None,
                permanent=code in _PERMANENT_ERRORS,
            ) from e
        except Exception as e:  # 通信の失敗・タイムアウトなど（投稿できたかどうかは分からない）
            log.warning("Slack の%sで通信に失敗しました: %s", what, e)
            raise SlackSendError(f"{what}に失敗（通信）: {type(e).__name__}: {e}") from e

    def auth_test(self) -> str:
        return self._call("トークンの確認", lambda: str(self.client.auth_test()["user_id"]))

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        return self._call(
            f"{channel} への投稿",
            lambda: str(
                self.client.chat_postMessage(
                    channel=channel, text=text, thread_ts=thread_ts, unfurl_links=False, unfurl_media=False
                )["ts"]
            ),
        )

    def find_message(self, channel: str, marker: str) -> str | None:
        def find() -> str | None:
            res = self.client.conversations_history(channel=channel, limit=100)
            messages = res.get("messages") or []
            for m in messages:
                if isinstance(m, dict) and m.get("bot_id") and marker in str(m.get("text", "")):
                    return str(m["ts"])
            return None

        return self._call(f"{channel} の確認", find)


def sdk_inbound_factory(app_token: str, bot_token: str) -> InboundFactory:
    def start(on_message: Callable[[Mapping[str, object]], None]) -> InboundHandle:
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse

        client = SocketModeClient(app_token=app_token, web_client=WebClient(token=bot_token))

        def listener(c: SocketModeClient, req: SocketModeRequest) -> None:
            # 受け取ったことはすぐ Slack に返す（3 秒以内に返さないと再送される）
            c.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            if req.type != "events_api":
                return
            event = req.payload.get("event")
            if isinstance(event, dict):
                on_message(event)

        client.socket_mode_request_listeners.append(listener)
        try:
            client.connect()
        except BaseException:
            # 接続に失敗したら SDK が起こしたスレッドを残さない
            client.close()
            raise
        return client

    return start


# ---- つなぎ込み本体 ----


def message_text(post: Post) -> str:
    """疑似チャンネルの投稿を Slack 用の文にする（抽出した項目を添える）。"""
    lines = [f"*{post.author}*: {post.text}"]
    lines += [f"• {k}: {v}" for k, v in post.fields.items() if v]
    return "\n".join(lines)


def post_text(post: Post, item: Item | None) -> str:
    """実際の Slack に流す文。ガードレールを通していない件は、件名に個人情報が残りうるので番号だけにする。"""
    if item is not None and item.pii_decision == "skipped":
        return f"*{post.author}*: {item.id}（ガードレール無効のため件名は載せません）"
    return message_text(post)


def left_text(minutes: float) -> str:
    """営業時間の残りを短く書く（1 時間以上は時間、未満は分）。"""
    if minutes >= 60:
        return f"{int(minutes // 60)} 時間（営業時間）"
    return f"{max(1, int(minutes))} 分（営業時間）"


def link_text(item_id: str, settings: Settings) -> str:
    return f"<{settings.slack.app_url.rstrip('/')}/ops/escalations?id={item_id}|画面で開く>"


def link_marker(item_id: str) -> str:
    """親の投稿を Slack 側で探すときの目印（画面へのリンクの一部）。"""
    return f"/ops/escalations?id={item_id}|"


def mention(member: StaffMember | None) -> str:
    """Slack のユーザー ID があればメンション、なければ名前だけ。"""
    if member is None:
        return "（未登録の担当者）"
    return f"<@{member.slack_user_id}>" if member.slack_user_id else member.name


def assign_line(item: Item, settings: Settings) -> str:
    """スレッドに書く、担当についての一言。

    担当が決まっていればその人を、決まっていなければ振り分け担当（当番）をメンションする。
    推定した担当は名前だけ書く（確信度の低い推定で本人を呼び出さないため）。
    """
    staff = {s.id: s for s in settings.staff}
    if item.assignee:
        how = "（自動で割り当て）" if item.assigned_by == "auto" else ""
        return f"{mention(staff.get(item.assignee))} 担当です{how}"
    dispatcher = settings.slack.dispatcher
    who = mention(staff.get(dispatcher)) if dispatcher else "（振り分け担当が未設定）"
    hint = f"（推定: {staff[item.assign_suggestion].name}）" if item.assign_suggestion in staff else ""
    return f"{who} 担当を決めてください{hint}"


# Slack の書式のメンション（<@U123> や <@U123|name>）。ユーザー ID は取り込まない
_MENTION: Final = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")


def inbound_request(event: Mapping[str, object], channels: list[str], bot_user: str | None) -> IngestRequest | None:
    """受付箱に取り込むメッセージなら IngestRequest を返す。対象外なら None。"""
    if event.get("type") != "message" or event.get("subtype") is not None or event.get("bot_id") is not None:
        return None
    channel, user, text = event.get("channel"), event.get("user"), event.get("text")
    if not isinstance(channel, str) or channel not in channels:
        return None
    if not isinstance(user, str) or user == bot_user or not isinstance(text, str) or not text.strip():
        return None
    # スレッドの返信は、元の件への追記として扱う仕組みがまだないため取り込まない
    thread_ts, ts = event.get("thread_ts"), event.get("ts")
    if thread_ts is not None and thread_ts != ts:
        return None
    return IngestRequest(
        channel="slack",
        from_name=f"Slack ユーザー {user}",
        from_address=user,
        subject="",
        body=_MENTION.sub("@ユーザー", text).strip()[:MAX_BODY],
    )


def inbound_key(event: Mapping[str, object]) -> str:
    """同じメッセージの再送を見分けるキー（チャンネルと ts）。"""
    return f"{event.get('channel')}:{event.get('ts')}"


# 転送の位置（どこまで送ったか）の保存先。再起動しても続きから送る
_POST_CURSOR: Final = "slack.last_post"
_EVENT_CURSOR: Final = "slack.last_event"


@dataclass
class SlackConnector:
    pipeline: Pipeline
    api: SlackApi | None
    inbound_factory: InboundFactory | None
    bot_user: str | None = None
    outbound: DirectionStatus = field(default_factory=lambda: DirectionStatus(state="off"))
    inbound: DirectionStatus = field(default_factory=lambda: DirectionStatus(state="off"))
    _next_remind: float = 0.0
    _backoff_until: float = 0.0
    _auth_retry_at: float = 0.0
    _inbound_retry_at: float = 0.0
    _failures: int = 0
    _handle: InboundHandle | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    # 受信するチャンネル（SDK のスレッドから読むため、設定を読むたびに更新しておく）
    _channels: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, pipeline: Pipeline, env: Mapping[str, str] = os.environ) -> SlackConnector:
        bot, app = env.get("SLACK_BOT_TOKEN", ""), env.get("SLACK_APP_TOKEN", "")
        return cls(
            pipeline,
            SdkSlackApi(bot) if bot else None,
            sdk_inbound_factory(app, bot) if app and bot else None,
        )

    def status(self) -> SlackStatus:
        return SlackStatus(
            bot_token=self.api is not None,
            app_token=self.inbound_factory is not None,
            bot_user=self.bot_user,
            outbound=self.outbound
            if self.api
            else DirectionStatus(state="unconfigured", detail="SLACK_BOT_TOKEN が未設定"),
            inbound=self.inbound
            if self.inbound_factory
            else DirectionStatus(state="unconfigured", detail="SLACK_APP_TOKEN と SLACK_BOT_TOKEN の両方が必要"),
            mirrorable=list(MIRRORABLE),
        )

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except Exception:  # 1 回の失敗でつなぎ込み全体を止めない
                    log.exception("Slack とのつなぎ込みで想定外のエラー")
                await asyncio.sleep(_TICK_S)
        finally:
            await self.stop_inbound()

    async def tick(self) -> None:
        self._loop = asyncio.get_running_loop()
        settings = self.pipeline.store.settings()
        await self._sync_inbound(settings.connectors.get("slack", False), settings.slack.inbound_channels)
        await self._mirror(settings)
        await self._follow(settings)
        await self._remind(settings)

    # ---- 送信 ----

    def _enabled(self, settings: Settings) -> bool:
        return settings.slack.outbound and self.api is not None

    def _backing_off(self) -> bool:
        return time.monotonic() < self._backoff_until

    def _failed(self, e: SlackSendError) -> bool:
        """送信の失敗を記録する。一時的な失敗なら True（同じところから、待ってやり直す）。"""
        self.outbound.last_error = str(e)
        if e.permanent:
            log.warning("Slack への投稿を飛ばしました: %s", e)
            return False
        self._failures += 1
        wait = e.retry_after if e.retry_after is not None else min(_MAX_BACKOFF_S, 2.0**self._failures)
        self._backoff_until = time.monotonic() + wait
        self.outbound.state = "error"
        return True

    def _api(self) -> SlackApi:
        if self.api is None:
            raise RuntimeError("SLACK_BOT_TOKEN が未設定です")
        return self.api

    async def _send(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        ts = await asyncio.to_thread(self._api().post_message, channel, text, thread_ts)
        self.outbound.count += 1
        self._failures = 0
        return ts

    def _cursor(self, key: str, latest: int, enabled: bool) -> int | None:
        """保存した転送の位置。無効の間と初めてのときは「いまの最新」に合わせる（過去分をまとめて流さない）。"""
        store = self.pipeline.store
        saved = store.meta(key)
        if saved is None or not enabled:
            if saved != str(latest):
                store.put_meta(key, str(latest))
            return None
        cursor = int(saved)
        # 受付箱を空にすると番号が振り直される
        return 0 if latest < cursor else cursor

    async def _mirror(self, settings: Settings) -> None:
        store = self.pipeline.store
        enabled = self._enabled(settings)
        if self.api is not None:
            self.outbound.state = ("error" if self._backing_off() else "on") if enabled else "off"
        cursor = self._cursor(_POST_CURSOR, store.last_post_id(), enabled)
        if cursor is None or self._backing_off() or not await self._ensure_bot_user():
            return
        for post in store.posts_after(cursor):
            target = settings.slack.channel_map.get(post.channel)
            if target and post.channel in MIRRORABLE:
                item = self._item(post.item_id)
                try:
                    if post.channel == ESCALATION_CHANNEL and item is not None:
                        await self._escalation(post, item, target, settings)
                    elif post.item_id is None or item is not None:
                        await self._send(target, post_text(post, item))
                except SlackSendError as e:
                    if self._failed(e):
                        return
            store.put_meta(_POST_CURSOR, str(post.id))

    def _item(self, item_id: str | None) -> Item | None:
        if item_id is None:
            return None
        try:
            return self.pipeline.store.get(item_id)
        except ItemNotFoundError:  # 受付箱を空にした後など
            return None

    async def _escalation(self, post: Post, item: Item, channel: str, settings: Settings) -> None:
        """エスカレーションは親の投稿（画面へのリンクつき）と、担当についてのスレッドの返信に分ける。

        親と返信は別々に記録し、途中で失敗してやり直しても二重に書かない。
        親の送信が応答なく終わった（投稿できたか分からない）ときは、やり直す前に Slack 側を探す。
        """
        store = self.pipeline.store
        if item.slack_ts is not None and item.slack_notified:
            # 同じ件の 2 回目以降のエスカレーション（やり直しで再び人に回った など）はスレッドに書く
            await self._send(item.slack_channel or channel, post_text(post, item), item.slack_ts)
            return
        if item.slack_ts is None:
            ts = None
            if item.slack_parent_pending:
                ts = await asyncio.to_thread(self._api().find_message, channel, link_marker(item.id))
            if ts is None:
                store.update(item.id, lambda i: i.model_copy(update={"slack_parent_pending": True}))
                ts = await self._send(channel, f"{post_text(post, item)}\n{link_text(item.id, settings)}")
            item = store.update(
                item.id,
                lambda i: i.model_copy(
                    update={"slack_channel": channel, "slack_ts": ts, "slack_parent_pending": False}
                ),
            )
        await self._send(item.slack_channel or channel, assign_line(item, settings), item.slack_ts)
        store.update(item.id, lambda i: i.model_copy(update={"slack_notified": True}))

    async def _follow(self, settings: Settings) -> None:
        """人が担当を変えた・対応を完了した件は、Slack のスレッドに書き足す。"""
        store = self.pipeline.store
        cursor = self._cursor(_EVENT_CURSOR, store.last_event_id(), self._enabled(settings))
        if cursor is None or self._backing_off():
            return
        for event in store.events_after(cursor):
            text = self._follow_text(event, settings)
            item = self._item(event.item_id) if text is not None else None
            if text is not None and item is not None and item.slack_ts and item.slack_channel:
                try:
                    await self._send(item.slack_channel, text, item.slack_ts)
                except SlackSendError as e:
                    if self._failed(e):
                        return
            store.put_meta(_EVENT_CURSOR, str(event.id))

    @staticmethod
    def _follow_text(event: Event, settings: Settings) -> str | None:
        staff = {s.id: s for s in settings.staff}
        # 自動の割り当ては最初の返信に含めているので、人の操作だけを書き足す
        if event.kind == "assign" and event.actor == "human":
            after = event.data.get("after")
            return (
                f"{mention(staff.get(after))} 担当になりました"
                if isinstance(after, str) and after
                else "担当を外しました"
            )
        if event.kind == "close":
            who = event.data.get("assignee")
            name = staff[who].name if isinstance(who, str) and who in staff else "未割り当て"
            return f"対応完了（担当: {name}）"
        return None

    async def _remind(self, settings: Settings) -> None:
        """担当が決まらないまま対応目安が近づいた件を、振り分け担当に 1 回だけ知らせる（繰り返さない）。

        対応目安は営業時間で数える（昼休み・夜間・休日は進まない）。
        """
        s = settings.slack
        if not (self._enabled(settings) and s.reminder and s.dispatcher) or self._backing_off():
            return
        if time.monotonic() < self._next_remind:
            return
        self._next_remind = time.monotonic() + _REMIND_EVERY_S
        staff = {m.id: m for m in settings.staff}
        now = datetime.now(UTC)
        for candidate in self.pipeline.store.items(["escalated"]):
            if candidate.assignee or candidate.slack_reminded or not candidate.slack_ts:
                continue
            if minutes_left(candidate.received_at, now, settings.sla) > s.reminder_before_min:
                continue
            # 一覧を読んでから送るまでの間に担当が決まっていることがあるので、送る直前に読み直す
            item = self._item(candidate.id)
            if item is None or item.status != "escalated" or item.assignee or item.slack_reminded:
                continue
            left = minutes_left(item.received_at, now, settings.sla)
            when = f"対応目安まであと {left_text(left)}です" if left > 0 else "対応目安を過ぎています"
            text = f"{mention(staff.get(s.dispatcher))} {when}。担当が未定です"
            try:
                await self._send(item.slack_channel or "", text, item.slack_ts)
            except SlackSendError as e:
                if self._failed(e):
                    return
            # 送れた（または設定の誤りで送れない）件には印を付け、二度と知らせない
            self.pipeline.store.update(item.id, lambda i: i.model_copy(update={"slack_reminded": True}))

    async def _ensure_bot_user(self) -> bool:
        """ボット自身のユーザー ID を確かめる。確かめられないうちは送らない（トークンの誤りで毎周期呼ばない）。"""
        if self.bot_user is not None:
            return True
        if self.api is None or time.monotonic() < self._auth_retry_at:
            return False
        try:
            self.bot_user = await asyncio.to_thread(self.api.auth_test)
            return True
        except SlackSendError as e:
            self.outbound.last_error = str(e)
            self._auth_retry_at = time.monotonic() + _MAX_BACKOFF_S
            return False

    # ---- 受信 ----

    async def _sync_inbound(self, enabled: bool, channels: list[str]) -> None:
        self._channels = channels
        if self.inbound_factory is None:
            return
        if enabled and self._handle is None:
            if time.monotonic() < self._inbound_retry_at or not await self._ensure_bot_user():
                return
            self.inbound.state = "connecting"
            try:
                self._handle = await asyncio.to_thread(self.inbound_factory, self._on_event)
                self.inbound.last_error = None
            except Exception as e:  # 接続できなくてもアプリは動かし続け、次の周期でやり直す
                log.exception("Slack（Socket Mode）に接続できません")
                # トークンの誤りなどで毎周期つなぎ直さないよう、少し待ってからやり直す
                self._inbound_retry_at = time.monotonic() + _MAX_BACKOFF_S
                self.inbound.state, self.inbound.last_error = "error", f"{type(e).__name__}: {e}"
                return
        elif not enabled and self._handle is not None:
            await self.stop_inbound()
            return
        if self._handle is not None:
            # 切れても SDK がつなぎ直すので、いまつながっているかを表示に出す
            self.inbound.state = "on" if self._handle.is_connected() else "connecting"
            self.inbound.detail = f"受信するチャンネル: {', '.join(channels) or '（未設定）'}"

    async def stop_inbound(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(handle.close)
        if self.inbound_factory is not None:
            self.inbound.state = "off"

    def _on_event(self, event: Mapping[str, object]) -> None:
        """SDK のスレッドから呼ばれる。取り込みはイベントループで行う（受付の処理はループ上で動くため）。"""
        req = inbound_request(event, self._channels, self.bot_user)
        if req is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._ingest, req, inbound_key(event))

    def _ingest(self, req: IngestRequest, key: str) -> None:
        # 同じメッセージが再送されても（接続のやり直しなど）1 件だけ取り込む（Jev の料金も二重にしない）
        if not self.pipeline.store.mark_seen(key):
            return
        try:
            self.pipeline.ingest(req, via="Slack")
            self.inbound.count += 1
        except PermissionError as e:
            self.inbound.last_error = str(e)
