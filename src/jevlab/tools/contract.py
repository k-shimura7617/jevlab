"""契約・規約チェック。

本文を条項に分け、条項ごとに評価アプリ「契約条項のリスク判定」と同じ質問（自動更新・違約金・責任制限・
第三者提供の Noul と、不利さの Score）を 1 回の問い合わせでまとめて判定する。
Jev は判定だけで文章は書かないため、やさしい言葉での説明は生成器（Claude）に任せる。
法的助言ではなく、法務に回す前の一次チェックの目安。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, StringConstraints
from typesafe_sdk import Noul, Score

from jevlab.apps.contract.questions import QUESTIONS
from jevlab.apps.contract.spec import SPEC
from jevlab.core.client import Question
from jevlab.core.engine import AnswerView
from jevlab.core.generator import ClaudeModel

APP_NAME: Final = "tool-contract"
MAX_CHARS: Final = 20000
MAX_CLAUSES: Final = 20
MAX_CLAUSE_CHARS: Final = 1500
DISCLAIMER: Final = "法的助言ではありません。法務に回す前の一次チェックの目安です。"

# 評価アプリの Noul（有無）の項目と表示名。不利さ（risk）は Score
FLAGS: Final[tuple[str, ...]] = ("auto_renewal", "penalty", "liability_cap", "data_sharing")
FLAG_LABELS: Final[dict[str, str]] = {q: SPEC.display[q].title for q in FLAGS}
RISK_LABELS: Final[dict[str, str]] = {"0": "低", "1": "中", "2": "高"}

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHARS)]

# 条項の見出し（第1条・第１条・第十条・1.・１．・(1) など）の行頭
_HEAD: Final = re.compile(r"^\s*(第[0-9０-９一二三四五六七八九十百]+条|[0-9０-９]+[.．、)]|[（(][0-9０-９]+[)）])")


def split_clauses(text: str) -> list[str]:
    """見出し（第N条・番号）で条項に分ける。見出しがなければ空行で分ける。"""
    lines = text.replace("\r\n", "\n").split("\n")
    clauses: list[list[str]] = []
    if any(_HEAD.match(line) for line in lines):
        for line in lines:
            if _HEAD.match(line) or not clauses:
                clauses.append([line])
            else:
                clauses[-1].append(line)
    else:
        for block in re.split(r"\n\s*\n", text):
            clauses.append([block])
    return [c for c in ("\n".join(x).strip() for x in clauses) if c]


def clause_id(i: int, q: str) -> str:
    return f"c{i}_{q}"


def _per_clause(q: Question, i: int) -> Question:
    """評価アプリの質問（`clause.body` を読む）を、i 番目の条項を読む質問にする。"""
    path = f"`contract.clauses[{i}]`"
    if isinstance(q, Noul | Score) and isinstance(q.instructions, str):
        return q.model_copy(update={"instructions": q.instructions.replace("`clause.body`", path)})
    raise TypeError(f"想定外の質問の形です: {type(q).__name__}")


def questions(clauses: list[str]) -> dict[str, Question]:
    return {clause_id(i, qid): _per_clause(q, i) for i in range(len(clauses)) for qid, q in QUESTIONS.items()}


def state(clauses: list[str]) -> dict[str, object]:
    return {"contract": {"clauses": clauses}}


# ---- 結果 ----

Level = Literal["low", "mid", "high"]
# 不利さ（0〜2 の期待値）の区切り
MID_AT: Final = 0.67
HIGH_AT: Final = 1.34
# 有無の Noul を「あり」とみなす確率
FLAG_AT: Final = 0.5


class ClauseResult(BaseModel):
    index: int
    text: str
    level: Level
    # 不利さ（0〜2 の期待値）と、段階ごとの確率
    risk: float
    risk_probs: dict[str, float]
    # 当てはまる項目（自動更新など）と、その確率
    flags: list[str]
    flag_probs: dict[str, float]


class ContractResult(BaseModel):
    clauses: list[ClauseResult]
    truncated: bool
    counts: dict[Level, int]
    model: str
    latency_ms: float
    cost_usd: float
    disclaimer: str = DISCLAIMER


def level_of(risk: float) -> Level:
    return "high" if risk >= HIGH_AT else "mid" if risk >= MID_AT else "low"


def build_result(
    views: Mapping[str, AnswerView],
    clauses: list[str],
    *,
    truncated: bool,
    model: str,
    latency_ms: float,
    cost_usd: float,
) -> ContractResult:
    out: list[ClauseResult] = []
    for i, text in enumerate(clauses):
        risk = views[clause_id(i, "risk")]
        value = risk.value if risk.value is not None else float(str(risk.prediction))
        probs = {f: views[clause_id(i, f)].value or 0.0 for f in FLAGS}
        out.append(
            ClauseResult(
                index=i,
                text=text,
                level=level_of(value),
                risk=round(value, 3),
                risk_probs=risk.probabilities,
                flags=[f for f in FLAGS if probs[f] >= FLAG_AT],
                flag_probs=probs,
            )
        )
    counts: dict[Level, int] = {"high": 0, "mid": 0, "low": 0}
    for c in out:
        counts[c.level] += 1
    return ContractResult(
        clauses=out, truncated=truncated, counts=counts, model=model, latency_ms=latency_ms, cost_usd=cost_usd
    )


# ---- やさしい説明（Claude） ----

EXPLAIN_SYSTEM: Final = """あなたは、契約書を読み慣れていない会社員に説明する役です。
SaaS などを導入する側（利用者）の立場で、指定された条項が何を定めているかを、やさしい日本語で説明してください。

守ること:
- 法的な判断や助言はしない。「無効です」「違法です」などと断定しない。
- 条項に書かれていないことを足さない。
- 1 条項につき、説明は 2〜3 文。専門用語には短い言い換えを添える。
- 確認や交渉で聞くとよいことを、多くても 3 つ挙げる。
- 出力は指定の JSON だけ。"""

EXPLAIN_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "条項の番号（入力のまま）"},
                    "summary": {"type": "string", "description": "何を定めているか（やさしい言葉で 2〜3 文）"},
                    "ask": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                        "description": "確認・交渉で聞くとよいこと",
                    },
                },
                "required": ["index", "summary", "ask"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class ExplainClause(BaseModel):
    index: int = Field(ge=0, lt=MAX_CLAUSES)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CLAUSE_CHARS)]
    level: Level
    flags: list[str] = Field(default_factory=list, max_length=len(FLAGS))


class ExplainRequest(BaseModel):
    clauses: list[ExplainClause] = Field(min_length=1, max_length=MAX_CLAUSES)
    model: ClaudeModel = "sonnet"


def explain_prompt(req: ExplainRequest) -> str:
    parts = []
    for c in req.clauses:
        flags = "・".join(FLAG_LABELS.get(f, f) for f in c.flags) or "なし"
        parts.append(
            f"[{c.index}] 不利さ: {RISK_LABELS[{'low': '0', 'mid': '1', 'high': '2'}[c.level]]}／当てはまる項目: {flags}\n{c.text}"
        )
    return "次の条項を説明してください。\n\n" + "\n\n".join(parts)


class Explanation(BaseModel):
    index: int
    summary: str
    ask: list[str]


class ExplainResult(BaseModel):
    items: list[Explanation]
    model: str
    latency_ms: float
    reported_cost_usd: float | None = None
