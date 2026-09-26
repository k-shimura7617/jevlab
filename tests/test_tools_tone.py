from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jevlab.core import generator
from jevlab.core.engine import AnswerView
from jevlab.core.generator import GeneratedText, GenerateRequest, GenerationError, parse_envelope
from jevlab.tools import api as tools_api
from jevlab.tools import tone
from jevlab.web import app


def test_split_sentences() -> None:
    assert tone.split_sentences("了解です。資料見ときます！\n\nよろしく") == [
        "了解です。",
        "資料見ときます！",
        "よろしく",
    ]
    assert tone.split_sentences("   ") == []
    # 括弧の中の句点では切らない
    assert tone.split_sentences("「了解です。」と言われた。") == ["「了解です。」と言われた。"]


def _noul(v: float) -> AnswerView:
    return AnswerView(type="noul", prediction=v >= 0.5, value=v)


def _score(p0: float, p1: float, p2: float) -> AnswerView:
    return AnswerView(
        type="score",
        prediction=1,
        value=p1 + 2 * p2,
        confidence=max(p0, p1, p2),
        probabilities={"0": p0, "1": p1, "2": p2},
    )


def _answers(
    purpose: str, politeness: tuple[float, float, float] = (0.05, 0.9, 0.05), **values: float
) -> dict[str, AnswerView]:
    out: dict[str, AnswerView] = {
        "purpose": AnswerView(type="choice", prediction=purpose, confidence=0.9, probabilities={purpose: 0.9}),
        "impression": AnswerView(type="choice", prediction="neutral", confidence=0.8, probabilities={"neutral": 0.8}),
        "politeness": _score(*politeness),
    }
    for a in tone.ASPECTS:
        if a.id != "politeness":
            default = 0.9 if a.polarity == "good" else 0.1
            out[a.id] = _noul(values.get(a.id, default))
    out["s0"] = _noul(values.get("s0", 0.1))
    return out


def test_build_result_ok_and_caution() -> None:
    ok = tone.build_result(_answers("thanks"), ["ありがとう。"], False, "m", 10.0, 0.0)
    assert ok.verdict == "ok"
    harsh = tone.build_result(_answers("request", harsh=0.9, s0=0.8), ["早くして。"], False, "m", 10.0, 0.0)
    assert harsh.verdict == "caution"
    assert harsh.sentences[0].flagged is True


def test_apology_aspects_only_count_for_apology() -> None:
    # 謝罪の観点が悪くても、目的がお礼なら総合判定に入れない
    thanks = tone.build_result(_answers("thanks", prevention=0.1), ["ありがとう。"], False, "m", 1.0, 0.0)
    assert thanks.verdict == "ok"
    apology = tone.build_result(_answers("apology", prevention=0.1), ["すみません。"], False, "m", 1.0, 0.0)
    assert apology.verdict == "review"
    assert "今後の対応が具体的" in apology.verdict_note


def test_politeness_levels() -> None:
    casual = tone.build_result(_answers("request", politeness=(0.8, 0.15, 0.05)), ["x"], False, "m", 1.0, 0.0)
    p = next(a for a in casual.aspects if a.id == "politeness")
    assert (p.level, p.note) == ("bad", "くだけすぎ")
    # 両端に割れた場合、期待値は 1（真ん中）でも「適切」とは判定しない
    split = tone.build_result(_answers("request", politeness=(0.45, 0.1, 0.45)), ["x"], False, "m", 1.0, 0.0)
    p = next(a for a in split.aspects if a.id == "politeness")
    assert p.value == pytest.approx(1.0) and p.level == "bad"


def test_verdicts_per_purpose() -> None:
    # 謝罪の観点が悪い文面でも、目的を「報告」に直せば総合判定は謝罪の観点を含まない
    r = tone.build_result(_answers("apology", excuse=0.9), ["x"], False, "m", 1.0, 0.0)
    assert r.verdict == "review" and "言い訳" in r.verdict_note
    assert r.verdicts["report"].verdict == "ok"
    # 良い観点は問題の形で見出しに出す
    clarity = tone.build_result(_answers("request", clear_deadline=0.1), ["x"], False, "m", 1.0, 0.0)
    assert "（足りない）" in clarity.verdict_note


def test_rewrite_prompt_includes_findings_and_text() -> None:
    req = tone.RewriteRequest(
        text="早くして。",
        recipient="colleague",
        medium="chat",
        purpose="request",
        findings=[tone.Finding(title="きつい・責めている", detail="あり")],
        flagged_sentences=["早くして。"],
    )
    prompt = tone.rewrite_prompt(req)
    assert "同僚" in prompt and "きつい・責めている" in prompt and "<<<\n早くして。\n>>>" in prompt


def test_parse_envelope() -> None:
    ok = parse_envelope(
        '{"subtype":"success","is_error":false,"result":"{}","structured_output":{"rewritten":"x"},'
        '"modelUsage":{"claude-sonnet-5":{}},"usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.001}',
        1234.5,
        "sonnet",
    )
    assert ok.structured == {"rewritten": "x"} and ok.model == "claude-sonnet-5" and ok.input_tokens == 10
    with pytest.raises(GenerationError, match="JSON ではありません"):
        parse_envelope("oops", 1.0, "sonnet")
    with pytest.raises(GenerationError, match="エラーを返しました"):
        parse_envelope('{"subtype":"error_max_turns","is_error":true}', 1.0, "sonnet")


@pytest.fixture
def client(mock_env: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_tone_api_with_mock(client: TestClient) -> None:
    meta = client.get("/api/tools/tone/meta").json()
    assert meta["default_model"] in meta["models"]
    res = client.post(
        "/api/tools/tone?target=mock",
        json={"text": "例の件、まだですか？至急お願いします。", "recipient": "boss", "medium": "chat"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verdict"] in {"ok", "review", "caution"}
    assert len(body["sentences"]) == 2
    assert {a["id"] for a in body["aspects"]} == {a.id for a in tone.ASPECTS}
    assert client.post("/api/tools/tone?target=mock", json={"text": ""}).status_code == 422
    assert client.post("/api/tools/tone?target=jev", json={"text": "x"}).status_code == 400


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


REWRITE_BODY = {
    "text": "早くして。",
    "recipient": "colleague",
    "medium": "chat",
    "purpose": "request",
    "model": "opus",
}


def test_rewrite_api_uses_generator(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGenerator(
        {"rewritten": "お手すきの際にお願いします。", "changes": ["語気を和らげた"], "placeholders": []}
    )
    monkeypatch.setattr(tools_api, "make_generator", lambda model: fake)
    res = client.post("/api/tools/tone/rewrite", json=REWRITE_BODY)
    assert res.status_code == 200, res.text
    assert res.json()["rewritten"] == "お手すきの際にお願いします。"
    assert fake.requests[0].schema == tone.REWRITE_SCHEMA


def test_rewrite_api_reports_generator_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator(None, fail=True))
    res = client.post("/api/tools/tone/rewrite", json=REWRITE_BODY)
    assert res.status_code == 502 and "失敗しました" in res.json()["detail"]
    monkeypatch.setattr(tools_api, "make_generator", lambda model: FakeGenerator({"changes": []}))
    assert client.post("/api/tools/tone/rewrite", json=REWRITE_BODY).status_code == 502


def test_unknown_generator_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_GENERATOR", "nope")
    with pytest.raises(GenerationError, match="未対応"):
        generator.make_generator("sonnet")


def test_report_does_not_require_ask_or_deadline() -> None:
    r = tone.build_result(_answers("report", clear_deadline=0.1, clear_ask=0.1), ["x"], False, "m", 1.0, 0.0)
    assert r.verdict == "ok"
    assert "clear_deadline" not in r.aspects_for_purpose["report"]
    assert r.verdicts["request"].verdict == "review"


def test_blank_text_is_rejected(client: TestClient) -> None:
    assert client.post("/api/tools/tone?target=mock", json={"text": "  \n "}).status_code == 422
    assert client.post("/api/tools/tone/rewrite", json={**REWRITE_BODY, "text": "   "}).status_code == 422


def test_unknown_generator_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_GENERATOR", "nope")
    assert client.post("/api/tools/tone/rewrite", json=REWRITE_BODY).status_code == 502


def test_child_env_drops_secrets() -> None:
    env = {"PATH": "/bin", "TYPESAFE_API_KEY": "x", "KEV_URL": "y", "ANTHROPIC_API_KEY": "z"}
    assert generator.child_env(env) == {"PATH": "/bin"}
    assert "ANTHROPIC_API_KEY" in generator.child_env({**env, "JEVLAB_CLAUDE_USE_API_KEY": "1"})


def test_parse_envelope_tolerates_null_usage() -> None:
    out = parse_envelope(
        '{"subtype":"success","is_error":false,"result":"","usage":{"input_tokens":null},"total_cost_usd":"?"}',
        1.0,
        "sonnet",
    )
    assert out.input_tokens == 0 and out.reported_cost_usd is None


def test_subordinate_recipient(client: TestClient) -> None:
    recipients = client.get("/api/tools/tone/meta").json()["recipients"]
    # 同僚と取引先の間に並ぶ（画面の選択肢はこの順）
    assert list(recipients)[1:4] == ["colleague", "subordinate", "client"]
    assert recipients["subordinate"] == "部下"
    res = client.post(
        "/api/tools/tone?target=mock",
        json={"text": "これ明日までにやっておいて。", "recipient": "subordinate", "medium": "chat"},
    )
    assert res.status_code == 200, res.text
    req = tone.RewriteRequest(
        text="やっておいて。",
        recipient="subordinate",
        medium="chat",
        purpose="request",
        findings=[],
        flagged_sentences=[],
    )
    prompt = tone.rewrite_prompt(req)
    assert "相手: 部下" in prompt and "目下の相手でも" in tone.REWRITE_SYSTEM


def test_recipient_and_medium_default_to_unspecified(client: TestClient) -> None:
    meta = client.get("/api/tools/tone/meta").json()
    assert meta["recipients"]["none"] == "指定なし" and meta["mediums"]["none"] == "指定なし"
    # 相手・場面を送らなければ「指定なし」として判定する
    res = client.post("/api/tools/tone?target=mock", json={"text": "資料を確認しておいてください。"})
    assert res.status_code == 200, res.text
    st = tone.state("本文", "none", "none", ["本文"])
    assert st["message"] == {"text": "本文", "recipient": "指定なし", "medium": "指定なし", "sentences": ["本文"]}
