"""System One クライアントの生成と、予算ガード付きの呼び出し。

接続先（jevlab.core.target）ごとにクライアントを作る:
- jev: TYPESAFE_API_KEY / TYPESAFE_DEFAULT_MODEL を SDK が環境変数から読む
- custom: KEV_URL / KEV_API_KEY / KEV_MODEL（Kev などの互換サーバ）
- mock: HTTP 層をモックに差し替え、API を一切呼ばない
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score, SystemOneResponse

from jevlab.core import target
from jevlab.core.budget import Ledger, cost_of
from jevlab.core.confidence import choice_confidence, score_confidence

Question = Choice | Score | Noul
# 1リクエストあたりの固定オーバーヘッド実測値（約280トークン）を含めた、予算チェック用の粗い見込み
_OVERHEAD_TOKENS = 300


def _unit(seed: str) -> float:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _mock_answer(state_key: str, qid: str, question: Mapping[str, object]) -> dict[str, object]:
    u = _unit(f"{state_key}:{qid}")
    match question["type"]:
        case "noul":
            # 「はい」に偏らないようにする（緊急・個人情報の取りこぼしなどが半数に出るとデモの流れが詰まる）
            return {"type": "noul", "noul": round(u**3, 2)}
        case "choice":
            keys = list(question["criteria"])  # type: ignore[call-overload]
            picked = keys[int(u * len(keys)) % len(keys)]
            # 最大確率をばらつかせ、閾値による振り分け（自動・確認・人）がすべて起きるようにする
            top = round(0.45 + 0.54 * _unit(f"{state_key}:{qid}:confidence") ** 0.5, 2)
            # 選んだ選択肢がいちばん確率の高いものになるようにする（選択肢が 2 つのとき 0.45 では逆転する）
            top = max(top, round(1 / len(keys) + 0.05, 2))
            rest = (1 - top) / max(len(keys) - 1, 1)
            probs = {k: (top if k == picked else rest) for k in keys}
            # 確信度は本物と同じ式で確率から出す（最大確率そのものではない）
            conf = round(choice_confidence(top, len(keys)), 4)
            return {"type": "choice", "choice": picked, "confidence": conf, "probabilities": probs}
        case "score":
            levels = list(question["criteria"])  # type: ignore[call-overload]
            n = len(levels)
            # 低い段階に寄せる（最上位ばかりだとエスカレーションが多くなりすぎる）
            top = int(u**2 * n) % n
            probs = {str(i): (0.8 if i == top else 0.2 / max(n - 1, 1)) for i in range(n)}
            score = sum(i * p for i, p in enumerate(probs.values()))
            return {
                "type": "score",
                "score": round(score, 2),
                "confidence": round(score_confidence(list(probs.values())), 4),
                "legend": {str(i): lv for i, lv in enumerate(levels)},
                "probabilities": probs,
            }
        case other:
            raise ValueError(f"モック未対応の質問タイプ: {other}")


def _mock_handler(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    state_key = json.dumps(body["state"], ensure_ascii=False, sort_keys=True)
    answers = {qid: _mock_answer(state_key, qid, q) for qid, q in body["questions"].items()}
    usage = {"input_tokens": _OVERHEAD_TOKENS + len(request.content) // 3, "output_tokens": 20 * len(answers)}
    return httpx2.Response(200, json={"model": "mock-jev", "answers": answers, "usage": usage})


def make_client(t: target.Target | None = None) -> AsyncTypeSafeClient:
    """接続先ごとのクライアント。省略時は既定の接続先。"""
    match t or target.current():
        case "mock":
            return AsyncTypeSafeClient(api_key="mock", transport=httpx2.MockTransport(_mock_handler))
        case "custom":
            # Kev は CPU で動くため、質問が多いと SDK の既定（10 秒）では足りない（17 問で約 10 秒を実測）
            return AsyncTypeSafeClient(
                api_key=target.kev_api_key(),
                base_url=target.kev_url(),
                model=os.environ.get("KEV_MODEL") or None,
                timeout=float(os.environ.get("KEV_TIMEOUT_S", "120")),
            )
        case "jev":
            # TYPESAFE_API_KEY は SDK が環境変数から読む（未設定なら TypeSafeError）
            return AsyncTypeSafeClient(base_url=target.JEV_ENDPOINT)


def estimate_tokens(state: object, questions: Mapping[str, Question]) -> int:
    # 日本語はおおむね1文字≒1トークン。安全側に倒した粗い見積もり
    payload = json.dumps(
        {"state": state, "questions": {k: q.model_dump() for k, q in questions.items()}}, ensure_ascii=False
    )
    return _OVERHEAD_TOKENS + len(payload)


@dataclass(frozen=True)
class Judged:
    response: SystemOneResponse
    latency_ms: float
    cost_usd: float


async def judge(
    client: AsyncTypeSafeClient,
    ledger: Ledger,
    *,
    app: str,
    state: object,
    questions: Mapping[str, Question],
) -> Judged:
    ledger.ensure_within(cost_of(estimate_tokens(state, questions)))
    started = time.perf_counter()
    response = await client.system_one(state=state, questions=questions)  # type: ignore[arg-type]
    latency_ms = (time.perf_counter() - started) * 1000
    rec = ledger.record(
        app=app,
        model=response.model,
        input_tokens=response.usage.input_tokens or 0,
        output_tokens=response.usage.output_tokens or 0,
        latency_ms=latency_ms,
    )
    return Judged(response, latency_ms, rec.cost_usd)
