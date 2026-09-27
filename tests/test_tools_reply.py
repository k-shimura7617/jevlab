from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jevlab.core.engine import AnswerView
from jevlab.core.generator import GeneratedText, GenerateRequest, GenerationError
from jevlab.tools import api as tools_api
from jevlab.tools import reply, tone
from jevlab.web import app

INQUIRY = "注文した皿が割れて届きました。交換できますか？電話は 090-1111-2222 です。"
DRAFT = "承知しました。すぐに全額返金いたします。"


@pytest.fixture
def client(mock_env: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _views(apology: dict[str, float] | None = None, **values: float) -> dict[str, AnswerView]:
    """既定は「問題なし」。指定した質問だけ値を変える。"""
    ok = {"answers": 0.9, "overpromise": 0.1, "harsh": 0.1, "curt": 0.1}
    views = {k: AnswerView(type="noul", prediction=v >= 0.5, value=values.get(k, v)) for k, v in ok.items()}
    views[reply.APOLOGY_ID] = AnswerView(
        type="score", prediction=1, value=1.0, probabilities=apology or {"0": 0.1, "1": 0.8, "2": 0.1}
    )
    for k in reply.MISSING:
        v = values.get(reply.missing_id(k), 0.1)
        views[reply.missing_id(k)] = AnswerView(type="noul", prediction=v >= 0.5, value=v)
    return views


def _build(views: dict[str, AnswerView]) -> reply.ReplyResult:
    return reply.build_result(views, sent_inquiry="q", sent_draft="d", model="m", latency_ms=1.0, cost_usd=0.0)


def test_questions_cover_the_checks_and_reuse_tone_aspects() -> None:
    qs = reply.questions()
    assert {"answers", "overpromise", "harsh", "curt", reply.APOLOGY_ID} <= qs.keys()
    assert all(reply.missing_id(k) in qs for k in reply.MISSING)
    assert "`reply.draft`" in str(qs["harsh"].instructions) and "message." not in str(qs["harsh"].instructions)


def test_verdicts() -> None:
    assert _build(_views()).verdict == "ok"
    over = _build(_views(overpromise=0.9))
    assert over.verdict == "caution" and "方針を超えた約束" in over.verdict_note
    lacking = _build(_views(**{reply.missing_id("order_id"): 0.8}))
    assert lacking.verdict == "review" and [m.key for m in lacking.missing] == ["order_id"]
    sorry = _build(_views(apology={"0": 0.7, "1": 0.2, "2": 0.1}))
    apology = next(c for c in sorry.checks if c.id == reply.APOLOGY_ID)
    assert apology.level == "bad" and apology.note == "足りない"


def test_reply_api_sends_free_text_as_typed(client: TestClient) -> None:
    # ツールの自由入力は会社側の文なので、既定では伏せない（ADR 0028。伏せるかは個人情報チェックで本人が決める）
    meta = client.get("/api/tools/reply/meta").json()
    assert meta["default_policy"] == reply.DEFAULT_POLICY and "order_id" in meta["missing"]
    res = client.post("/api/tools/reply?target=mock", json={"inquiry": INQUIRY, "draft": DRAFT})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sent_inquiry"] == INQUIRY
    assert {c["id"] for c in body["checks"]} >= {"answers", "overpromise", "apology"}
    assert client.post("/api/tools/reply?target=mock", json={"inquiry": " ", "draft": DRAFT}).status_code == 422


class FakeGenerator:
    def __init__(self, structured: dict[str, object] | None) -> None:
        self.structured = structured
        self.requests: list[GenerateRequest] = []

    async def generate(self, req: GenerateRequest) -> GeneratedText:
        self.requests.append(req)
        return GeneratedText("", self.structured, "claude-test", 5.0, 0.001, 10, 5)


def test_rewrite_sends_text_and_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGenerator(
        {
            "rewritten": "交換について確認し、【期限を記入】までにご連絡します。",
            "changes": ["返金の確約をやめた"],
            "placeholders": ["期限"],
        }
    )
    monkeypatch.setattr(tools_api, "make_generator", lambda model: fake)
    body = {"inquiry": INQUIRY, "draft": DRAFT, "findings": [{"title": "方針を超えた約束", "detail": "返金を確約"}]}
    res = client.post("/api/tools/reply/rewrite", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["rewritten"].startswith("交換について")
    prompt = fake.requests[0].prompt
    assert "090-1111-2222" in prompt and reply.DEFAULT_POLICY in prompt and "返金を確約" in prompt
    assert fake.requests[0].schema == tone.REWRITE_SCHEMA
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator({"changes": []}))
    assert client.post("/api/tools/reply/rewrite", json=body).status_code == 502


def test_draft_sends_inquiry_and_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGenerator(
        {
            "rewritten": "ご連絡ありがとうございます。\n【期限を記入】までにご連絡します。",
            "changes": ["期日は確約しない"],
        }
    )
    monkeypatch.setattr(tools_api, "make_generator", lambda model: fake)
    res = client.post("/api/tools/reply/draft", json={"inquiry": INQUIRY})
    assert res.status_code == 200, res.text
    assert res.json()["rewritten"].startswith("ご連絡ありがとうございます")
    prompt = fake.requests[0].prompt
    assert "090-1111-2222" in prompt and reply.DEFAULT_POLICY in prompt
    assert fake.requests[0].system == reply.DRAFT_SYSTEM
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator({"changes": []}))
    assert client.post("/api/tools/reply/draft", json={"inquiry": INQUIRY}).status_code == 502


class FakeStream:
    def __init__(self, parts: list[str], fail: bool = False) -> None:
        self.parts = parts
        self.fail = fail
        self.requests: list[GenerateRequest] = []

    async def stream(self, req: GenerateRequest) -> AsyncIterator[str | GeneratedText]:
        self.requests.append(req)
        for p in self.parts:
            yield p
        if self.fail:
            raise GenerationError("止まりました")
        yield GeneratedText("".join(self.parts), None, "claude-test", 5.0, 0.001, 10, 5)


def _lines(res: Any) -> list[dict[str, Any]]:
    return [json.loads(x) for x in res.text.splitlines() if x]


def test_suggest_stream_sends_text_as_it_is_written(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeStream(["お問い合わせ", "ありがとうございます。"])
    monkeypatch.setattr(tools_api, "make_stream_generator", lambda model: fake)
    res = client.post("/api/tools/reply/suggest/stream", json={"inquiry": INQUIRY})
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-ndjson")
    lines = _lines(res)
    assert [x["text"] for x in lines if x["type"] == "text"] == ["お問い合わせ", "ありがとうございます。"]
    assert lines[-1]["type"] == "done" and lines[-1]["model"] == "claude-test"
    # 下書きがなければ問い合わせから書く
    req = fake.requests[0]
    assert req.system == reply.DRAFT_STREAM_SYSTEM and INQUIRY in req.prompt and req.schema is None
    client.post("/api/tools/reply/suggest/stream", json={"inquiry": INQUIRY, "draft": DRAFT})
    assert fake.requests[1].system == reply.REWRITE_STREAM_SYSTEM and "JSON" not in fake.requests[1].system


def test_suggest_stream_reports_errors_in_the_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_api, "make_stream_generator", lambda model: FakeStream(["途中"], fail=True))
    lines = _lines(client.post("/api/tools/reply/suggest/stream", json={"inquiry": INQUIRY}))
    assert lines[0] == {"type": "text", "text": "途中"} and lines[-1]["type"] == "error"
    assert "止まりました" in lines[-1]["message"]


def _item_with_pii(client: TestClient) -> str:
    """電話番号を含む件を受け付け、人が「担当」を氏名として足して確定する。"""
    res = client.post(
        "/api/ops/ingest",
        json={
            "channel": "mail",
            "from_name": "テスト",
            "subject": "",
            "body": "担当の件です。電話は 090-3333-4444 です",
        },
    )
    item_id = res.json()["id"]
    for _ in range(100):
        item = client.get(f"/api/ops/items/{item_id}").json()["item"]
        if item["status"] == "pii_review":
            break
        time.sleep(0.05)
    start = item["text"].index("担当")
    added = {"start": start, "end": start + 2, "type": "person_name", "text": "担当", "source": "human"}
    res = client.post(f"/api/ops/items/{item_id}/pii", json={"spans": [*item["pii"], added], "action": "continue"})
    assert res.status_code == 200, res.text
    return item_id


def test_item_inquiry_is_built_and_masked_on_the_server(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    item_id = _item_with_pii(client)
    loaded = client.get(f"/api/tools/reply/item/{item_id}").json()["inquiry"]
    assert "090-3333-4444" not in loaded and "【電話番号】" in loaded and "【氏名】" in loaded
    # 画面から別の問い合わせが来ても、件の本文（伏せ字済み）を使う
    res = client.post(
        "/api/tools/reply?target=mock",
        json={"inquiry": "偽の問い合わせ 090-0000-0000", "item_id": item_id, "draft": DRAFT},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sent_inquiry"] == loaded
    fake = FakeStream(["案"])
    monkeypatch.setattr(tools_api, "make_stream_generator", lambda model: fake)
    client.post("/api/tools/reply/suggest/stream", json={"inquiry": "偽", "item_id": item_id})
    assert loaded in fake.requests[0].prompt and "090-3333-4444" not in fake.requests[0].prompt
    assert client.get("/api/tools/reply/item/T-9999").status_code == 404


def test_pii_check_is_local_and_can_be_rules_only(client: TestClient) -> None:
    text = "山田様、明日 090-5555-6666 にお電話します。"
    settings = client.get("/api/ops/settings").json()
    settings["guard"]["use_model"] = False
    assert client.put("/api/ops/settings", json=settings).status_code == 200
    body = client.post("/api/tools/pii-check", json={"text": text}).json()
    assert body["used_model"] is False
    assert {s["type"] for s in body["spans"]} >= {"phone", "person_name"}
    assert "090-5555-6666" not in body["masked_text"] and "【電話番号】" in body["masked_text"]
    assert body["labels"]["phone"] == "電話番号"
    # Kev（ここでは MOCK）を使う設定なら、氏名などの候補をモデルに聞く
    settings["guard"]["use_model"] = True
    client.put("/api/ops/settings", json=settings)
    assert client.post("/api/tools/pii-check", json={"text": text}).json()["used_model"] is True
    assert client.post("/api/tools/pii-check", json={"text": ""}).status_code == 422
