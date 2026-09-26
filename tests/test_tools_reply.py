from __future__ import annotations

import json
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


def test_reply_api_masks_pii_before_judging(client: TestClient) -> None:
    meta = client.get("/api/tools/reply/meta").json()
    assert meta["default_policy"] == reply.DEFAULT_POLICY and "order_id" in meta["missing"]
    res = client.post("/api/tools/reply?target=mock", json={"inquiry": INQUIRY, "draft": DRAFT})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "090-1111-2222" not in body["sent_inquiry"] and "【電話番号】" in body["sent_inquiry"]
    assert {c["id"] for c in body["checks"]} >= {"answers", "overpromise", "apology"}
    assert client.post("/api/tools/reply?target=mock", json={"inquiry": " ", "draft": DRAFT}).status_code == 422


class FakeGenerator:
    def __init__(self, structured: dict[str, object] | None) -> None:
        self.structured = structured
        self.requests: list[GenerateRequest] = []

    async def generate(self, req: GenerateRequest) -> GeneratedText:
        self.requests.append(req)
        return GeneratedText("", self.structured, "claude-test", 5.0, 0.001, 10, 5)


def test_rewrite_sends_masked_text_and_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "090-1111-2222" not in prompt and reply.DEFAULT_POLICY in prompt and "返金を確約" in prompt
    assert fake.requests[0].schema == tone.REWRITE_SCHEMA
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator({"changes": []}))
    assert client.post("/api/tools/reply/rewrite", json=body).status_code == 502


def test_draft_sends_masked_inquiry_and_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "090-1111-2222" not in prompt and reply.DEFAULT_POLICY in prompt
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
    # 下書きがなければ問い合わせから書く。個人情報の候補は伏せて送る
    req = fake.requests[0]
    assert req.system == reply.DRAFT_STREAM_SYSTEM and "090-1111-2222" not in req.prompt and req.schema is None
    client.post("/api/tools/reply/suggest/stream", json={"inquiry": INQUIRY, "draft": DRAFT})
    assert fake.requests[1].system == reply.REWRITE_STREAM_SYSTEM and "JSON" not in fake.requests[1].system


def test_suggest_stream_reports_errors_in_the_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_api, "make_stream_generator", lambda model: FakeStream(["途中"], fail=True))
    lines = _lines(client.post("/api/tools/reply/suggest/stream", json={"inquiry": INQUIRY}))
    assert lines[0] == {"type": "text", "text": "途中"} and lines[-1]["type"] == "error"
    assert "止まりました" in lines[-1]["message"]
