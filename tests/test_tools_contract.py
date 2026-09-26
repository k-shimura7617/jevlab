from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jevlab.core.engine import AnswerView
from jevlab.core.generator import GeneratedText, GenerateRequest, GenerationError
from jevlab.tools import api as tools_api
from jevlab.tools import contract
from jevlab.web import app

SAMPLE = """第1条（目的）
本規約は、本サービスの利用条件を定める。
第2条（契約期間）
本契約の有効期間は1年間とし、期間満了の1か月前までに申し出がないときは同一条件で更新される。
第3条（データの利用）
当社は、利用者のデータを機械学習モデルの学習に利用できる。"""


@pytest.fixture
def client(mock_env: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_split_clauses_by_heading_and_blank_line() -> None:
    clauses = contract.split_clauses(SAMPLE)
    assert len(clauses) == 3 and clauses[1].startswith("第2条") and "更新される" in clauses[1]
    assert contract.split_clauses("1. 甲は…\n続き\n2. 乙は…") == ["1. 甲は…\n続き", "2. 乙は…"]
    assert contract.split_clauses("前文です。\n\n本文の段落です。") == ["前文です。", "本文の段落です。"]


def test_questions_reuse_the_eval_app_per_clause() -> None:
    qs = contract.questions(["a", "b"])
    assert len(qs) == 2 * 5
    assert "`contract.clauses[1]`" in str(qs["c1_auto_renewal"].instructions)
    assert "clause.body" not in str(qs["c0_risk"].instructions)
    assert qs["c0_risk"].type == "score"


def _views(risk: float, renewal: float) -> dict[str, AnswerView]:
    views = {contract.clause_id(0, f): AnswerView(type="noul", prediction=False, value=0.1) for f in contract.FLAGS}
    views[contract.clause_id(0, "auto_renewal")] = AnswerView(type="noul", prediction=renewal >= 0.5, value=renewal)
    views[contract.clause_id(0, "risk")] = AnswerView(type="score", prediction=round(risk), value=risk)
    return views


def test_build_result_levels_and_flags() -> None:
    r = contract.build_result(_views(1.5, 0.8), ["第1条"], truncated=False, model="m", latency_ms=1.0, cost_usd=0.0)
    (c,) = r.clauses
    assert c.level == "high" and c.flags == ["auto_renewal"] and r.counts == {"high": 1, "mid": 0, "low": 0}
    low = contract.build_result(_views(0.2, 0.1), ["x"], truncated=False, model="m", latency_ms=1.0, cost_usd=0.0)
    assert low.clauses[0].level == "low" and low.clauses[0].flags == []


def test_contract_api_with_mock(client: TestClient) -> None:
    meta = client.get("/api/tools/contract/meta").json()
    assert "法的助言ではありません" in meta["disclaimer"] and meta["flags"]["auto_renewal"] == "自動更新"
    res = client.post("/api/tools/contract?target=mock", json={"text": SAMPLE})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [c["index"] for c in body["clauses"]] == [0, 1, 2]
    assert sum(body["counts"].values()) == 3 and body["disclaimer"] == contract.DISCLAIMER
    assert client.post("/api/tools/contract?target=mock", json={"text": "   "}).status_code == 422


class FakeGenerator:
    def __init__(self, structured: dict[str, object] | None, fail: bool = False) -> None:
        self.structured = structured
        self.fail = fail
        self.requests: list[GenerateRequest] = []

    async def generate(self, req: GenerateRequest) -> GeneratedText:
        self.requests.append(req)
        if self.fail:
            raise GenerationError("claude -p が失敗しました（テスト）")
        return GeneratedText("", self.structured, "claude-test", 5.0, 0.001, 10, 5)


EXPLAIN_BODY = {"clauses": [{"index": 1, "text": "自動で更新される。", "level": "mid", "flags": ["auto_renewal"]}]}


def test_explain_uses_generator_and_drops_unknown_clauses(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGenerator(
        {
            "items": [
                {"index": 1, "summary": "黙っていると更新されます。", "ask": ["解約の期限"]},
                {"index": 9, "summary": "x", "ask": []},
            ]
        }
    )
    monkeypatch.setattr(tools_api, "make_generator", lambda model: fake)
    res = client.post("/api/tools/contract/explain", json=EXPLAIN_BODY)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == [{"index": 1, "summary": "黙っていると更新されます。", "ask": ["解約の期限"]}]
    assert "自動更新" in fake.requests[0].prompt and fake.requests[0].schema == contract.EXPLAIN_SCHEMA


def test_explain_reports_generator_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator(None, fail=True))
    assert client.post("/api/tools/contract/explain", json=EXPLAIN_BODY).status_code == 502
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator({"nope": 1}))
    assert client.post("/api/tools/contract/explain", json=EXPLAIN_BODY).status_code == 502
