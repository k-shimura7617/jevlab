from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jevlab.core.engine import AnswerView
from jevlab.ops.models import IngestRequest, Settings, SlackSettings, SlaSettings, StaffMember
from jevlab.ops.pipeline import ESCALATION_CHANNEL, INBOUND_CHANNEL, Pipeline
from jevlab.ops.slack import InboundHandle, SdkSlackApi, SlackConnector, SlackSendError, inbound_request
from jevlab.ops.store import Store

C_ESC = "C0ESCALATE1"
C_IN = "C0INBOUND01"


class FakeApi:
    def __init__(self, fail: SlackSendError | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.threads: list[str | None] = []
        self.fail = fail
        # 次の返信（スレッドへの投稿）だけ失敗させる
        self.fail_reply: SlackSendError | None = None
        # 投稿はできたのに応答が返らない（タイムアウト）ことを 1 回だけ起こす
        self.lose_response = False
        self.auth_error: SlackSendError | None = None
        self.auth_calls = 0
        self.reactions: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        # 同じボットが jevlab 以外の用途で投稿したメッセージ（消してはいけない）
        self.foreign: list[tuple[str, str]] = []
        self.undeletable: set[str] = set()
        self.fail_reaction: SlackSendError | None = None

    def auth_test(self) -> str:
        self.auth_calls += 1
        if self.auth_error is not None:
            raise self.auth_error
        return "UBOT"

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        if self.fail is not None:
            raise self.fail
        if thread_ts is not None and self.fail_reply is not None:
            e, self.fail_reply = self.fail_reply, None
            raise e
        self.sent.append((channel, text))
        self.threads.append(thread_ts)
        if self.lose_response:
            self.lose_response = False
            raise SlackSendError("応答なし（テスト）")
        return f"{len(self.sent)}.0"

    def find_message(self, channel: str, marker: str) -> str | None:
        for i, (c, t) in enumerate(self.sent):
            if c == channel and marker in t and self.threads[i] is None:
                return f"{i + 1}.0"
        return None

    def add_reaction(self, channel: str, ts: str, name: str) -> None:
        if self.fail_reaction is not None:
            raise self.fail_reaction
        self.reactions.append((channel, ts, name))

    def own_messages(self, channel: str, bot_user: str) -> list[tuple[str, str | None]]:
        mine = [
            (f"{i + 1}.0", th)
            for i, ((c, _), th) in enumerate(zip(self.sent, self.threads, strict=True))
            if c == channel and f"{i + 1}.0" not in self.deleted
        ]
        mine += [(ts, None) for c, ts in self.foreign if c == channel]
        return [(ts, th) for ts, th in mine if th is not None] + [(ts, th) for ts, th in mine if th is None]

    def delete_message(self, channel: str, ts: str) -> bool:
        if ts in self.undeletable:
            return False
        self.deleted.append(ts)
        return True


class FakeHandle:
    closed = False
    connected = True

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return self.connected


class FakeInbound:
    def __init__(self) -> None:
        self.on_message: Callable[[Mapping[str, object]], None] | None = None
        self.handle = FakeHandle()

    def __call__(self, on_message: Callable[[Mapping[str, object]], None]) -> InboundHandle:
        self.on_message = on_message
        return self.handle


def make(tmp_path: Path, api: FakeApi | None, inbound: FakeInbound | None, **slack: object) -> SlackConnector:
    store = Store(tmp_path / "ops.db")
    base = Settings()
    store.put_settings(
        base.model_copy(
            update={
                "slack": SlackSettings.model_validate(slack),
                "connectors": {**base.connectors, "slack": inbound is not None},
            }
        )
    )
    return SlackConnector(Pipeline(store=store, backends={}), api, inbound)


@pytest.mark.anyio
async def test_mirror_sends_only_new_mapped_posts(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC, INBOUND_CHANNEL: C_ESC})
    store = conn.pipeline.store
    store.add_post(ESCALATION_CHANNEL, "jevlab", "起動前の投稿")
    await conn.tick()  # 最初の周期は「いまの最新」から数える（過去の投稿は流さない）
    store.add_post(ESCALATION_CHANNEL, "jevlab", "T-0001「件名」: 緊急", fields={"order_id": "KM-250101-0001"})
    # お問い合わせ窓口は元の本文なので、割り当てがあっても流さない
    store.add_post(INBOUND_CHANNEL, "ゲスト", "山田です。電話は 090-0000-0000")
    await conn.tick()
    assert len(api.sent) == 1
    channel, text = api.sent[0]
    assert channel == C_ESC and "T-0001" in text and "KM-250101-0001" in text
    assert conn.status().outbound.count == 1


@pytest.mark.anyio
async def test_mirror_retries_temporary_failures_and_skips_permanent(tmp_path: Path) -> None:
    api = FakeApi(fail=SlackSendError("混み合い", retry_after=0.0))
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    await conn.tick()
    conn.pipeline.store.add_post(ESCALATION_CHANNEL, "jevlab", "一時的に失敗する投稿")
    await conn.tick()
    assert conn.status().outbound.state == "error" and api.sent == []
    api.fail = None
    await conn.tick()  # 同じ投稿からやり直す
    assert [t for _, t in api.sent] == ["*jevlab*\n一時的に失敗する投稿"]
    api.fail = SlackSendError("招待されていない", permanent=True)
    conn.pipeline.store.add_post(ESCALATION_CHANNEL, "jevlab", "設定の誤りで送れない投稿")
    await conn.tick()
    api.fail = None
    conn.pipeline.store.add_post(ESCALATION_CHANNEL, "jevlab", "次の投稿")
    await conn.tick()
    # 直らない失敗は飛ばして、後の投稿は流れ続ける
    assert [t for _, t in api.sent][-1] == "*jevlab*\n次の投稿"
    assert "招待されていない" in (conn.status().outbound.last_error or "")


def test_inbound_filter() -> None:
    base = {"type": "message", "channel": C_IN, "user": "U1", "text": "注文の件です", "ts": "1.0"}
    assert inbound_request(base, [C_IN], "UBOT") is not None
    assert inbound_request({**base, "channel": "C0OTHER001"}, [C_IN], "UBOT") is None
    assert inbound_request({**base, "subtype": "message_changed"}, [C_IN], "UBOT") is None
    assert inbound_request({**base, "bot_id": "B1"}, [C_IN], "UBOT") is None
    assert inbound_request({**base, "user": "UBOT"}, [C_IN], "UBOT") is None
    assert inbound_request({**base, "thread_ts": "0.5"}, [C_IN], "UBOT") is None
    assert inbound_request({**base, "thread_ts": "1.0"}, [C_IN], "UBOT") is not None
    assert inbound_request({**base, "text": "  "}, [C_IN], "UBOT") is None


@pytest.mark.anyio
async def test_inbound_ingests_messages_and_disconnects_when_turned_off(tmp_path: Path) -> None:
    inbound = FakeInbound()
    conn = make(tmp_path, FakeApi(), inbound, inbound_channels=[C_IN])
    await conn.tick()
    assert conn.status().inbound.state == "on" and inbound.on_message is not None
    inbound.on_message(
        {"type": "message", "channel": C_IN, "user": "U1", "text": "届いた箱が潰れていました", "ts": "1.0"}
    )
    # SDK のスレッドからはイベントループ経由で取り込む。ループを 1 回まわして反映させる
    await asyncio.sleep(0)
    items = conn.pipeline.store.items()
    assert len(items) == 1 and items[0].channel == "slack" and items[0].body == "届いた箱が潰れていました"
    settings = conn.pipeline.store.settings()
    conn.pipeline.store.put_settings(
        settings.model_copy(update={"connectors": {**settings.connectors, "slack": False}})
    )
    await conn.tick()
    assert inbound.handle.closed and conn.status().inbound.state == "off"


def test_status_without_tokens(tmp_path: Path) -> None:
    status = make(tmp_path, None, None).status()
    assert status.outbound.state == "unconfigured" and status.inbound.state == "unconfigured"
    assert not status.bot_token and not status.app_token


def test_invalid_slack_channel_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        SlackSettings.model_validate({"channel_map": {ESCALATION_CHANNEL: "#general"}})


def with_staff(conn: SlackConnector, **slack: object) -> None:
    """佐藤（Slack ID あり）を振り分け担当にし、鈴木（Slack ID なし）を置く。"""
    store = conn.pipeline.store
    s = store.settings()
    staff = [
        StaffMember(id="sato", name="佐藤", slack_user_id="U0SATO001"),
        StaffMember(id="suzuki", name="鈴木"),
        StaffMember(id="tamura", name="田村", slack_user_id="U0TAMURA1"),
    ]
    # 対応目安は、いつでも営業時間・30 分にしておく（テストの時刻に左右されないように）
    sla = SlaSettings(hours=0.5, days=[0, 1, 2, 3, 4, 5, 6], start="00:00", end="23:59")
    store.put_settings(
        s.model_copy(
            update={
                "staff": staff,
                "sla": sla,
                "slack": s.slack.model_copy(update={"dispatcher": "sato", "reminder_before_min": 10, **slack}),
            }
        )
    )


def escalate(conn: SlackConnector, **update: object) -> str:
    store = conn.pipeline.store
    item = store.add_item(IngestRequest(channel="mail", subject="箱が潰れていました", body="本文"))
    store.update(item.id, lambda i: i.model_copy(update={"status": "escalated", **update}))
    store.add_post(ESCALATION_CHANNEL, "jevlab", f"{item.id}「箱が潰れていました」: 緊急", item.id)
    return item.id


@pytest.mark.anyio
async def test_escalation_thread_mentions_dispatcher_when_unassigned(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn, app_url="http://127.0.0.1:8000")
    await conn.tick()
    item_id = escalate(conn, assign_suggestion="tamura")
    await conn.tick()
    (_, parent), (_, reply) = api.sent
    assert f"<http://127.0.0.1:8000/ops/escalations?id={item_id}|画面で開く>" in parent
    assert api.threads == [None, "1.0"]
    # 推定した担当（田村）は名前だけ。呼び出すのは振り分け担当（佐藤）
    assert "<@U0SATO001>" in reply and "推定: 田村" in reply and "U0TAMURA1" not in reply
    assert conn.pipeline.store.get(item_id).slack_ts == "1.0"


@pytest.mark.anyio
async def test_escalation_thread_mentions_auto_assignee_and_follows_changes(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    item_id = escalate(conn, assignee="tamura", assigned_by="auto", assign_suggestion="tamura")
    await conn.tick()
    assert api.sent[1][1].startswith("<@U0TAMURA1>\n対応お願いします。")
    # 人が担当を変えた・外した・完了にした → 同じスレッドに書き足す
    conn.pipeline.assign(item_id, "suzuki")
    conn.pipeline.assign(item_id, "")
    conn.pipeline.assign(item_id, "sato")
    conn.pipeline.close(item_id, "complaint")
    await conn.tick()
    follow = [t for _, t in api.sent[2:]]
    assert follow[:3] == ["鈴木 担当になりました", "担当を外しました", "<@U0SATO001> 担当になりました"]
    assert follow[-1] == "対応完了（担当: 佐藤）"
    assert all(ts == "1.0" for ts in api.threads[1:])


@pytest.mark.anyio
async def test_reminder_is_sent_only_once_before_sla(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn, reminder_before_min=10)
    await conn.tick()
    fresh = escalate(conn)
    late = escalate(conn)
    await conn.tick()
    store = conn.pipeline.store
    # 受信から 21 分たった件だけが、対応目安（30 分）の 10 分前を過ぎている
    old = (datetime.now(UTC) - timedelta(minutes=21)).isoformat()
    store.update(late, lambda i: i.model_copy(update={"received_at": old}))
    for _ in range(3):
        conn._next_remind = 0.0
        await conn.tick()
    reminders = [t for _, t in api.sent if "対応目安" in t]
    assert len(reminders) == 1 and "<@U0SATO001>" in reminders[0]
    assert store.get(late).slack_reminded and not store.get(fresh).slack_reminded


@pytest.mark.anyio
async def test_no_reminder_when_assigned_or_turned_off(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn, reminder=False)
    await conn.tick()
    item_id = escalate(conn)
    await conn.tick()
    old = (datetime.now(UTC) - timedelta(minutes=29)).isoformat()
    conn.pipeline.store.update(item_id, lambda i: i.model_copy(update={"received_at": old}))
    conn._next_remind = 0.0
    await conn.tick()
    assert not [t for _, t in api.sent if "対応目安" in t]


# ---- レビューの指摘への対応 ----


@pytest.mark.anyio
async def test_items_without_guard_are_posted_with_title(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    item_id = escalate(conn, pii_decision="skipped")
    await conn.tick()
    parent = api.sent[0][1]
    # ガードレールが無効でも件名は載せる（個人情報の候補は投稿を作るときに規則で伏せる → safe_title のテスト）
    assert "箱が潰れていました" in parent and item_id in parent and "画面で開く" in parent


@pytest.mark.anyio
async def test_lost_response_does_not_duplicate_the_parent(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    api.lose_response = True  # 親は投稿されたが、応答が返らない
    item_id = escalate(conn)
    await conn.tick()
    status = conn.status().outbound
    assert status.state == "error" and "応答なし" in (status.last_error or "")
    conn._backoff_until = 0.0
    await conn.tick()
    parents = [t for (_, t), th in zip(api.sent, api.threads, strict=True) if th is None]
    # やり直しでは Slack 側で親を見つけて使い、二重に投稿しない
    assert len(parents) == 1
    item = conn.pipeline.store.get(item_id)
    assert item.slack_ts == "1.0" and item.slack_notified and not item.slack_parent_pending


@pytest.mark.anyio
async def test_failed_reply_is_retried_without_rewriting_the_body(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    api.fail_reply = SlackSendError("混み合い", retry_after=0.0)
    escalate(conn)
    await conn.tick()
    await conn.tick()
    texts = [t for _, t in api.sent]
    assert len(texts) == 2 and "担当を決めてください" in texts[1]


@pytest.mark.anyio
async def test_cursor_survives_restart(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    await conn.tick()
    # 止まっている間に投稿された分も、起動し直した後に送る
    conn.pipeline.store.add_post(ESCALATION_CHANNEL, "jevlab", "止まっている間の投稿")
    restarted = SlackConnector(conn.pipeline, api, None)
    await restarted.tick()
    assert [t for _, t in api.sent] == ["*jevlab*\n止まっている間の投稿"]


@pytest.mark.anyio
async def test_bad_token_backs_off(tmp_path: Path) -> None:
    api = FakeApi()
    api.auth_error = SlackSendError("invalid_auth", permanent=True)
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    for _ in range(3):
        await conn.tick()
    assert api.auth_calls == 1 and "invalid_auth" in (conn.status().outbound.last_error or "")


@pytest.mark.anyio
async def test_duplicate_inbound_events_are_ingested_once(tmp_path: Path) -> None:
    inbound = FakeInbound()
    conn = make(tmp_path, FakeApi(), inbound, inbound_channels=[C_IN])
    await conn.tick()
    assert inbound.on_message is not None
    event = {"type": "message", "channel": C_IN, "user": "U1", "text": "<@U0SATO001> 箱が潰れていました", "ts": "5.0"}
    inbound.on_message(event)
    inbound.on_message(event)  # 再送
    await asyncio.sleep(0)
    items = conn.pipeline.store.items()
    assert len(items) == 1 and items[0].body == "@ユーザー 箱が潰れていました"


@pytest.mark.anyio
async def test_inbound_state_follows_connection(tmp_path: Path) -> None:
    inbound = FakeInbound()
    conn = make(tmp_path, FakeApi(), inbound, inbound_channels=[C_IN])
    await conn.tick()
    assert conn.status().inbound.state == "on"
    inbound.handle.connected = False
    await conn.tick()
    assert conn.status().inbound.state == "connecting"


@pytest.mark.anyio
async def test_reminder_after_sla_and_skips_items_assigned_meanwhile(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    late = escalate(conn)
    assigned = escalate(conn)
    await conn.tick()
    store = conn.pipeline.store
    old = (datetime.now(UTC) - timedelta(minutes=35)).isoformat()
    for i in (late, assigned):
        store.update(i, lambda it: it.model_copy(update={"received_at": old}))
    conn.pipeline.assign(assigned, "suzuki")
    conn._next_remind = 0.0
    await conn.tick()
    reminders = [t for _, t in api.sent if "対応目安" in t]
    assert reminders == ["<@U0SATO001> 対応目安を過ぎています。担当が未定です"]


# ---- 振り分けた件の親の投稿と、対応完了 ----

C_COMPLAINT = "C0COMPLAIN1"


def route(conn: SlackConnector, category: str = "complaint", **update: object) -> str:
    store = conn.pipeline.store
    item = store.add_item(IngestRequest(channel="mail", subject="箱が潰れていました", body="本文"))
    routed = store.update(item.id, lambda i: i.model_copy(update={"status": "routed", "category": category, **update}))
    conn.pipeline._post_routed(routed, by="jevlab（Jev）")
    return item.id


@pytest.mark.anyio
async def test_routed_post_links_to_item_and_close_replies_with_reaction(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT})
    with_staff(conn, app_url="http://127.0.0.1:8000")
    await conn.tick()
    item_id = route(conn)
    await conn.tick()
    (channel, parent), (_, mention_line) = api.sent
    # 振り分けの投稿にも件の詳細へのリンクを付け、親として記録する
    assert channel == C_COMPLAINT and f"<http://127.0.0.1:8000/ops/inbox?id={item_id}|画面で開く>" in parent
    assert conn.pipeline.store.get(item_id).slack_ts == "1.0"
    # 返信の要る件は、スレッドで担当（決まっていなければ振り分け担当）をメンションする
    assert "<@U0SATO001>" in mention_line and api.threads[1] == "1.0"
    # 分類を変えずに完了 → スレッドに返信し、親に ✅ を付ける。新しい投稿は増えない
    closed = conn.pipeline.close(item_id, None)
    assert closed.status == "closed" and closed.category == "complaint"
    await conn.tick()
    assert api.sent[2:] == [(C_COMPLAINT, "対応完了（担当: 未割り当て）")] and api.threads[2:] == ["1.0"]
    assert api.reactions == [(C_COMPLAINT, "1.0", "white_check_mark")]


@pytest.mark.anyio
async def test_close_with_fixed_category_notes_the_fix(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT})
    with_staff(conn)
    await conn.tick()
    item_id = route(conn)
    await conn.tick()
    conn.pipeline.close(item_id, "inquiry")
    await conn.tick()
    reply = [t for (_, t), th in zip(api.sent, api.threads, strict=True) if th == "1.0" and "対応完了" in t]
    assert reply == ["対応完了（担当: 未割り当て）。分類を クレーム から 問い合わせ に修正"]
    item = conn.pipeline.store.get(item_id)
    assert item.category == "inquiry" and item.decided_by == "human"


@pytest.mark.anyio
async def test_failed_reaction_is_not_retried_and_does_not_duplicate_the_reply(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT})
    with_staff(conn)
    await conn.tick()
    item_id = route(conn)
    await conn.tick()
    api.fail_reaction = SlackSendError("リアクションに失敗: missing_scope", permanent=True)
    conn.pipeline.close(item_id, None)
    await conn.tick()
    await conn.tick()
    assert len([t for t in api.sent if "対応完了" in t[1]]) == 1
    assert "reactions:write" in (conn.status().outbound.last_error or "")


@pytest.mark.anyio
async def test_lost_response_of_routed_parent_is_not_duplicated(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT})
    with_staff(conn)
    await conn.tick()
    api.lose_response = True
    item_id = route(conn)
    await conn.tick()
    conn._backoff_until = 0.0
    await conn.tick()
    # 親は 1 回だけ（2 つ目はスレッドのメンション）
    assert len([th for th in api.threads if th is None]) == 1
    assert conn.pipeline.store.get(item_id).slack_ts == "1.0"


@pytest.mark.anyio
async def test_off_duty_dispatcher_is_not_called(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    store = conn.pipeline.store
    s = store.settings()
    # 佐藤（振り分け担当）の担当をオフにすると、呼び出さない
    staff = [m.model_copy(update={"active": m.id != "sato"}) for m in s.staff]
    store.put_settings(s.model_copy(update={"staff": staff}))
    await conn.tick()
    escalate(conn)
    await conn.tick()
    reply = api.sent[1][1]
    assert "U0SATO001" not in reply and "振り分け担当が未設定" in reply


@pytest.mark.anyio
async def test_escalation_close_replies_only_in_the_thread_with_masked_notes(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(
        tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC, "#cs-クレーム": C_COMPLAINT}
    )
    with_staff(conn)
    await conn.tick()
    item_id = escalate(conn, category="complaint")
    await conn.tick()
    parent_ts = conn.pipeline.store.get(item_id).slack_ts
    sent_before = len(api.sent)
    conn.pipeline.add_note(item_id, "お客様に電話済み 090-1234-5678")
    conn.pipeline.add_note(item_id, "代品を本日発送")
    conn.pipeline.close(item_id, "complaint")
    await conn.tick()
    new = list(zip(api.sent[sent_before:], api.threads[sent_before:], strict=True))
    # 分類のチャンネル（クレーム）には流さず、エスカレーションのスレッドに 1 回だけ返す
    assert [(ch, th) for (ch, _), th in new] == [(C_ESC, parent_ts)]
    reply = new[0][0][1]
    assert reply.splitlines()[1:] == ["メモ:", "・お客様に電話済み 【電話番号】", "・代品を本日発送"]
    assert "090-1234-5678" not in reply


@pytest.mark.anyio
async def test_provisional_assignee_is_mentioned(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    escalate(conn, assignee="tamura", assigned_by="auto", assign_provisional=True, assign_confidence=0.42)
    await conn.tick()
    reply = api.sent[1][1]
    # カッコ書き（仮で割り当て など）は付けず、担当確信度を 1 行で添える
    assert reply == "<@U0TAMURA1>\n対応お願いします。\n担当確信度 0.42"


@pytest.mark.anyio
async def test_close_stamps_every_post_of_the_item(tmp_path: Path) -> None:
    api = FakeApi()
    c_inq = "C0INQUIRY01"
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT, "#cs-問い合わせ": c_inq})
    with_staff(conn)
    await conn.tick()
    item_id = route(conn)
    await conn.tick()
    # 分類を直して完了すると、新しい分類のチャンネルにも投稿する → 元の投稿とその投稿の両方に ✅
    conn.pipeline.close(item_id, "inquiry")
    await conn.tick()
    stamped = {(c, ts) for c, ts, _ in api.reactions}
    assert (C_COMPLAINT, "1.0") in stamped and any(c == c_inq for c, _ in stamped)


@pytest.mark.anyio
async def test_purge_deletes_own_posts_and_forgets_refs(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    item_id = escalate(conn)
    await conn.tick()
    assert len(api.sent) == 2  # 親とスレッドの返信
    conn.start_purge()
    assert conn._purge_task is not None
    await conn._purge_task
    st = conn.status().purge
    assert not st.running and st.deleted == st.total == 2 and st.error is None
    # 返信を先に消す
    assert api.deleted == ["2.0", "1.0"]
    item = conn.pipeline.store.get(item_id)
    assert item.slack_ts is None and item.slack_channel is None and not item.slack_notified


@pytest.mark.anyio
async def test_purge_skips_messages_it_cannot_delete(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    escalate(conn)
    await conn.tick()
    api.undeletable = {"2.0"}
    conn.start_purge()
    assert conn._purge_task is not None
    await conn._purge_task
    st = conn.status().purge
    assert not st.running and st.error is None
    assert (st.deleted, st.skipped, st.total) == (1, 1, 2)
    assert api.deleted == ["1.0"]


def test_own_messages_ignores_join_notices() -> None:
    class Client:
        def conversations_history(self, **_: object) -> dict[str, object]:
            return {
                "messages": [
                    {"ts": "1.0", "user": "UBOT", "subtype": "channel_join"},
                    {"ts": "2.0", "user": "UBOT", "bot_id": "B1"},
                    {"ts": "3.0", "user": "UHUMAN"},
                ]
            }

    api = SdkSlackApi.__new__(SdkSlackApi)
    api.client = Client()  # type: ignore[assignment]
    assert api.own_messages("C1", "UBOT") == [("2.0", None)]


def test_purge_needs_mapped_channels(tmp_path: Path) -> None:
    conn = make(tmp_path, FakeApi(), None, outbound=True, channel_map={})
    with pytest.raises(ValueError, match="チャンネル ID"):
        conn.start_purge()


def _route_as(conn: SlackConnector, category: str) -> str:
    """確信度 0.99 で自動で振り分けになる件を作る（抜き取り確認はしない）。"""
    store = conn.pipeline.store
    settings = store.settings().model_copy(update={"audit_rate": 0.0})
    store.put_settings(settings)
    item = store.add_item(IngestRequest(channel="mail", subject="ありがとうございました", body="本文"))
    item = store.update(item.id, lambda i: i.model_copy(update={"category": category, "confidence": 0.99}))
    conn.pipeline._route(item, settings)
    return item.id


@pytest.mark.anyio
async def test_no_reply_category_is_closed_with_a_check_mark(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-お礼": "C0THANKS01"})
    await conn.tick()  # つないだ後の投稿だけを流す
    item_id = _route_as(conn, "thanks")
    item = conn.pipeline.store.get(item_id)
    assert item.status == "closed" and item.auto_closed and item.assignee is None
    await conn.tick()
    # 内容を投稿し、スレッドには書き足さずに ✅ だけ付ける
    # 内容を投稿し、担当は呼ばずにスレッドへ完了を書いて ✅ を付ける
    assert [c for c, _ in api.sent] == ["C0THANKS01", "C0THANKS01"] and "ありがとうございました" in api.sent[0][1]
    assert api.sent[1][1] == "対応不要のため完了にしました。" and api.threads[1] == "1.0"
    assert api.reactions == [("C0THANKS01", "1.0", "white_check_mark")]


@pytest.mark.anyio
async def test_categories_needing_a_reply_stay_open(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-問い合わせ": "C0INQUIRY1"})
    await conn.tick()
    item_id = _route_as(conn, "inquiry")
    assert conn.pipeline.store.get(item_id).status == "routed"
    await conn.tick()
    # 投稿とスレッドのメンション。✅ は付けない
    assert [c for c, _ in api.sent] == ["C0INQUIRY1", "C0INQUIRY1"] and api.threads[1] == "1.0" and not api.reactions


@pytest.mark.anyio
async def test_routed_item_needing_a_reply_is_assigned_and_mentioned(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-問い合わせ": "C0INQUIRY1"})
    with_staff(conn)
    await conn.tick()
    store = conn.pipeline.store
    store.put_settings(store.settings().model_copy(update={"audit_rate": 0.0}))
    item = store.add_item(IngestRequest(channel="mail", subject="在庫について", body="本文"))
    item = store.update(
        item.id,
        lambda i: i.model_copy(
            update={"category": "inquiry", "confidence": 0.99, "assign_suggestion": "tamura", "assign_confidence": 0.3}
        ),
    )
    conn.pipeline._route(item, store.settings())
    routed = store.get(item.id)
    # 確率が閾値に届かなくても、仮で割り当てる
    assert routed.status == "routed" and routed.assignee == "tamura" and routed.assign_provisional
    await conn.tick()
    assert api.sent[1][1] == "<@U0TAMURA1>\n対応お願いします。\n担当確信度 0.30" and api.threads[1] == "1.0"
    # 振り分け済みの件も、人が担当を変えられる
    assert conn.pipeline.assign(item.id, "sato").assignee == "sato"


@pytest.mark.anyio
async def test_thanks_decided_in_review_is_closed_without_mention(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-お礼": "C0THANKS01"})
    with_staff(conn)
    await conn.tick()
    store = conn.pipeline.store
    item = store.add_item(IngestRequest(channel="mail", subject="ありがとうございました", body="本文"))
    store.update(item.id, lambda i: i.model_copy(update={"status": "review", "category": "inquiry"}))
    decided = conn.pipeline.decide(item.id, "thanks")
    # お礼は返信がいらないので、担当は割り当てず完了にする
    assert decided.status == "closed" and decided.auto_closed and decided.assignee is None
    await conn.tick()
    # 完了を書いて ✅ を付けるだけ。メンションはしない
    assert [t for _, t in api.sent][1:] == ["対応不要のため完了にしました。"]
    assert api.reactions == [("C0THANKS01", "1.0", "white_check_mark")]


def _route_other(conn: SlackConnector, reply_p: float) -> str:
    """その他（返信の要否を判定する分類）を、返信が要る確率を決めて自動で振り分ける。"""
    store = conn.pipeline.store
    store.put_settings(store.settings().model_copy(update={"audit_rate": 0.0}))
    item = store.add_item(IngestRequest(channel="mail", subject="ご案内", body="本文"))
    view = AnswerView(type="noul", prediction=reply_p >= 0.5, value=reply_p)
    item = store.update(
        item.id,
        lambda i: i.model_copy(update={"category": "other", "confidence": 0.99, "answers": {"needs_reply": view}}),
    )
    conn.pipeline._route(item, store.settings())
    return item.id


@pytest.mark.anyio
async def test_other_is_closed_only_when_no_reply_is_needed(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-その他": "C0OTHER001"})
    with_staff(conn)
    await conn.tick()
    # 宣伝など返信の要らない件は、お礼と同じく完了を書いて ✅
    quiet = conn.pipeline.store.get(_route_other(conn, 0.1))
    assert quiet.status == "closed" and quiet.auto_closed and quiet.assignee is None
    await conn.tick()
    assert api.sent[1][1] == "対応不要のため完了にしました。" and len(api.reactions) == 1
    # 仕入れの提案など返信の要る件は、担当をメンションする
    reply = conn.pipeline.store.get(_route_other(conn, 0.9))
    assert reply.status == "routed" and not reply.auto_closed
    await conn.tick()
    assert "<@U0SATO001>" in api.sent[-1][1] and len(api.reactions) == 1


@pytest.mark.anyio
async def test_purge_leaves_other_posts_of_the_same_bot(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    escalate(conn)
    await conn.tick()
    # 同じボットで jevlab 以外の用途に投稿したもの
    api.foreign = [(C_ESC, "99.0")]
    conn.start_purge()
    assert conn._purge_task is not None
    await conn._purge_task
    assert "99.0" not in api.deleted and sorted(api.deleted) == ["1.0", "2.0"]


class FailingDeleteApi(FakeApi):
    def delete_message(self, channel: str, ts: str) -> bool:
        if ts == "1.0":
            raise SlackSendError("削除に失敗（テスト）")
        return super().delete_message(channel, ts)


@pytest.mark.anyio
async def test_purge_failure_forgets_only_deleted_posts(tmp_path: Path) -> None:
    api = FailingDeleteApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    item_id = escalate(conn)
    await conn.tick()
    conn.start_purge()
    assert conn._purge_task is not None
    await conn._purge_task
    st = conn.status().purge
    # 返信（2.0）は消せ、親（1.0）で失敗。親の控えは残す
    assert st.error is not None and api.deleted == ["2.0"]
    assert conn.pipeline.store.get(item_id).slack_ts == "1.0"
    assert (C_ESC, "2.0") not in conn.pipeline.store.slack_posts()


@pytest.mark.anyio
async def test_nothing_is_mirrored_while_purging(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={ESCALATION_CHANNEL: C_ESC})
    with_staff(conn)
    await conn.tick()
    conn.purge_status.running = True
    escalate(conn)
    await conn.tick()
    assert api.sent == []
    conn.purge_status.running = False
    await conn.tick()
    assert len(api.sent) == 2


def test_own_messages_pages_through_thread_replies() -> None:
    class Client:
        def conversations_history(self, **_: object) -> dict[str, object]:
            return {"messages": [{"ts": "1.0", "user": "UBOT", "bot_id": "B1", "reply_count": 3}]}

        def conversations_replies(self, *, cursor: str | None = None, **_: object) -> dict[str, object]:
            if cursor is None:
                msgs = [{"ts": "1.0", "user": "UBOT", "bot_id": "B1"}, {"ts": "1.1", "user": "UBOT", "bot_id": "B1"}]
                return {"messages": msgs, "response_metadata": {"next_cursor": "p2"}}
            return {"messages": [{"ts": "1.2", "user": "UBOT", "bot_id": "B1"}]}

    api = SdkSlackApi.__new__(SdkSlackApi)
    api.client = Client()  # type: ignore[assignment]
    assert api.own_messages("C1", "UBOT") == [("1.1", "1.0"), ("1.2", "1.0"), ("1.0", None)]


@pytest.mark.anyio
async def test_posts_after_inbox_reset_are_sent_even_when_ids_repeat(tmp_path: Path) -> None:
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map={"#cs-クレーム": C_COMPLAINT})
    with_staff(conn)
    await conn.tick()
    for _ in range(3):
        route(conn)
    await conn.tick()
    parents = [t for (c, t), th in zip(api.sent, api.threads, strict=True) if th is None]
    assert len(parents) == 3
    # 受付箱を空にすると投稿の番号は 1 から振り直される。前と同じ件数でも、新しい投稿を送る
    conn.pipeline.store.reset()
    await conn.tick()
    for _ in range(3):
        route(conn)
    await conn.tick()
    parents = [t for (c, t), th in zip(api.sent, api.threads, strict=True) if th is None]
    assert len(parents) == 6
