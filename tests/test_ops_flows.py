"""使う流れに沿ったテスト。受信 → 個人情報のガード → 仕分け → 振り分け → Slack を通しで流す。

Jev の答えだけ台本にし（本文ごとに決める）、ほかは本物の処理を通す。Slack は偽物（FakeApi）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_ops_slack import FakeApi, make, with_staff

from jevlab.core.engine import AnswerView
from jevlab.ops import questions as oq
from jevlab.ops.models import IngestRequest, Item
from jevlab.ops.pipeline import ESCALATION_CHANNEL
from jevlab.ops.slack import AUTO_CLOSE_TEXT, SlackConnector

CHANNELS = {
    ESCALATION_CHANNEL: "C0ESCALATE1",
    "#cs-問い合わせ": "C0INQUIRY1",
    "#cs-クレーム": "C0COMPLAIN",
    "#cs-お礼": "C0THANKS01",
    "#cs-その他": "C0OTHER001",
}


@dataclass(frozen=True)
class Script:
    """Jev の答え。既定は「確信度 0.99・不満や緊急なし・返信が要る・田村の確率 0.3」。"""

    category: str
    confidence: float = 0.99
    strong_frustration: float = 0.0
    urgent: bool = False
    needs_reply: float = 0.9
    assignee: str = "tamura"
    assignee_p: float = 0.3


def answers(s: Script, questions: Mapping[str, oq.Question]) -> dict[str, AnswerView]:
    rest = sorted({"inquiry", "complaint", "thanks", "other"} - {s.category})
    probs = {s.category: 0.95, **{k: 0.05 / len(rest) for k in rest}}
    frustration = {"0": 0.0, "1": 1 - s.strong_frustration, "2": s.strong_frustration}
    views = {
        "category": AnswerView(type="choice", prediction=s.category, confidence=s.confidence, probabilities=probs),
        "frustration": AnswerView(
            type="score", prediction=1, value=1 + s.strong_frustration, confidence=0.9, probabilities=frustration
        ),
        "urgent": AnswerView(type="noul", prediction=s.urgent, value=0.9 if s.urgent else 0.1),
        "insufficient": AnswerView(type="noul", prediction=False, value=0.1),
        oq.REPLY_ID: AnswerView(type="noul", prediction=s.needs_reply >= 0.5, value=s.needs_reply),
        oq.ASSIGNEE_ID: AnswerView(
            type="choice",
            prediction=s.assignee,
            confidence=0.2,
            probabilities={s.assignee: s.assignee_p, "none": 1 - s.assignee_p},
        ),
    }
    # 聞かれた問いにだけ答える（本物と同じく、聞いていない問いの答えは返らない）
    return {k: v for k, v in views.items() if k in questions}


def connect(tmp_path: Path, scripts: dict[str, Script]) -> tuple[SlackConnector, FakeApi]:
    """Slack を送信だけつなぎ、担当者 3 人（当番は佐藤）を置く。個人情報のガードは規則だけにする。"""
    api = FakeApi()
    conn = make(tmp_path, api, None, outbound=True, channel_map=CHANNELS)
    with_staff(conn)
    store = conn.pipeline.store
    s = store.settings()
    store.put_settings(
        s.model_copy(update={"audit_rate": 0.0, "guard": s.guard.model_copy(update={"use_model": False})})
    )

    async def ask(
        t: object, app: str, state: object, questions: Mapping[str, oq.Question]
    ) -> tuple[dict[str, AnswerView], float, float, str]:
        text = str(state)
        script = next((v for k, v in scripts.items() if k in text), None)
        if script is None:
            raise AssertionError(f"台本にない本文です: {text[:60]}")
        return answers(script, questions), 1.0, 0.0, "script"

    conn.pipeline._ask = ask  # type: ignore[method-assign]
    return conn, api


async def receive(conn: SlackConnector, body: str, subject: str = "") -> Item:
    item = conn.pipeline.ingest(IngestRequest(channel="mail", from_name="テスト", subject=subject, body=body))
    await conn.pipeline.process(item)
    return conn.pipeline.store.get(item.id)


def sent_to(api: FakeApi, channel_id: str) -> list[tuple[str, str | None]]:
    return [(t, th) for (c, t), th in zip(api.sent, api.threads, strict=True) if c == channel_id]


@pytest.mark.anyio
async def test_thanks_is_posted_and_closed_without_calling_anyone(tmp_path: Path) -> None:
    # お礼に「急ぎ」の言葉があっても、担当の確率が低くても、誰も呼ばずに完了にする
    conn, api = connect(tmp_path, {"ありがとう": Script("thanks", urgent=True, strong_frustration=0.5)})
    await conn.tick()
    item = await receive(conn, "昨日届きました。急ぎで頼んだのに間に合って、ありがとうございました。")
    assert item.status == "closed" and item.auto_closed and item.assignee is None
    await conn.tick()
    posts = sent_to(api, CHANNELS["#cs-お礼"])
    assert len(posts) == 2 and posts[0][1] is None
    assert posts[1] == (AUTO_CLOSE_TEXT, "1.0")
    assert api.reactions == [(CHANNELS["#cs-お礼"], "1.0", "white_check_mark")]
    assert not any("<@" in t for _, t in api.sent)


@pytest.mark.anyio
async def test_other_is_closed_or_assigned_by_the_reply_judgment(tmp_path: Path) -> None:
    conn, api = connect(
        tmp_path,
        {
            "お知らせ": Script("other", needs_reply=0.1),
            "教えて": Script("other", needs_reply=0.9),
        },
    )
    await conn.tick()
    quiet = await receive(conn, "営業日のお知らせを受け取りました。")
    ask = await receive(conn, "手入れの方法を教えてください。")
    assert quiet.status == "closed" and quiet.assignee is None
    assert ask.status == "routed" and ask.assignee == "tamura" and ask.assign_provisional
    await conn.tick()
    texts = [t for t, th in sent_to(api, CHANNELS["#cs-その他"]) if th is not None]
    # 送る順番は問わない
    assert sorted(texts) == sorted([AUTO_CLOSE_TEXT, "<@U0TAMURA1>\n対応お願いします。\n担当確信度 0.30"])


@pytest.mark.anyio
async def test_escalation_is_mentioned_and_closed_with_notes_in_its_thread(tmp_path: Path) -> None:
    conn, api = connect(tmp_path, {"割れて": Script("complaint", strong_frustration=0.8, assignee_p=0.9)})
    await conn.tick()
    item = await receive(conn, "マグカップが割れて届きました。どうなっているんですか。")
    assert item.status == "escalated" and item.assignee == "tamura" and not item.assign_provisional
    await conn.tick()
    esc = sent_to(api, CHANNELS[ESCALATION_CHANNEL])
    assert esc[0][1] is None and esc[1] == ("<@U0TAMURA1>\n対応お願いします。\n担当確信度 0.90", "1.0")
    before = len(api.sent)
    conn.pipeline.add_note(item.id, "代品を本日発送")
    conn.pipeline.close(item.id, "complaint")
    await conn.tick()
    new = list(zip(api.sent[before:], api.threads[before:], strict=True))
    # 完了はエスカレーションのスレッドにだけ返す（クレームのチャンネルには流さない）
    assert [(c, th) for (c, _), th in new] == [(CHANNELS[ESCALATION_CHANNEL], "1.0")]
    assert new[0][0][1].splitlines()[-2:] == ["メモ:", "・代品を本日発送"]
    assert not sent_to(api, CHANNELS["#cs-クレーム"])


@pytest.mark.anyio
async def test_low_confidence_thanks_is_reviewed_then_closed_without_mention(tmp_path: Path) -> None:
    conn, api = connect(tmp_path, {"ありがとう": Script("thanks", confidence=0.6)})
    await conn.tick()
    item = await receive(conn, "ありがとうございました。")
    assert item.status == "review"
    decided = conn.pipeline.decide(item.id, "thanks")
    assert decided.status == "closed" and decided.assignee is None
    await conn.tick()
    assert [t for t, th in sent_to(api, CHANNELS["#cs-お礼"]) if th is not None] == [AUTO_CLOSE_TEXT]
    assert not any("<@" in t for _, t in api.sent)


@pytest.mark.anyio
async def test_slack_keeps_working_after_the_inbox_is_reset(tmp_path: Path) -> None:
    conn, api = connect(
        tmp_path,
        {"ありがとう": Script("thanks"), "在庫": Script("inquiry", assignee_p=0.9)},
    )
    await conn.tick()
    await receive(conn, "ありがとうございました。")
    await receive(conn, "在庫はありますか。")
    await conn.tick()
    first = len([th for th in api.threads if th is None])
    assert first == 2
    # 受付箱を空にして、同じ流れをもう一度流す（投稿の番号は 1 から振り直される）
    conn.pipeline.store.reset()
    await receive(conn, "ありがとうございました。")
    await receive(conn, "在庫はありますか。")
    await conn.tick()
    assert len([th for th in api.threads if th is None]) == 4
    assert len(api.reactions) == 2
