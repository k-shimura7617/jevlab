"""質問定義と評価データから判定・評価を行う汎用エンジン。

各アプリは AppSpec（質問・表示名・評価データの場所）を定義するだけでよい。
評価データは JSONL で、1行が {"id", "body", <質問ID>: 正解, ...} の形。
正解の型は質問タイプに対応する: choice は選択肢キー、score は段階の番号、noul は true/false。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Literal

from pydantic import BaseModel
from typesafe_sdk import AsyncTypeSafeClient, ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from jevlab.core import metrics
from jevlab.core.budget import Ledger, cost_of
from jevlab.core.client import Question, estimate_tokens, judge

# レート制限（1,200 req/min）に対して十分小さい同時実行数
_CONCURRENCY = 5
NOUL_THRESHOLD = 0.5

QuestionType = Literal["choice", "score", "noul"]
Label = bool | int | str


@dataclass(frozen=True)
class QuestionDisplay:
    """画面表示用の短い名前。labels は choice の選択肢キー / score の段階番号 / noul の "true"・"false" に対応する。"""

    title: str
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AppSpec:
    name: str
    title: str
    description: str
    # state のトップレベルのキー。質問文からは `<subject>.body` として参照する
    subject: str
    questions: Mapping[str, Question]
    display: Mapping[str, QuestionDisplay]
    dataset_path: Path
    # ライブ評価で主役として大きく表示する質問。省略時は最初の質問
    primary: str | None = None

    @property
    def primary_question(self) -> str:
        return self.primary or next(iter(self.questions))

    def state(self, body: str) -> dict[str, dict[str, str]]:
        return {self.subject: {"body": body}}


class QuestionInfo(BaseModel):
    id: str
    type: QuestionType
    title: str
    instructions: str
    options: dict[str, str]


class AppInfo(BaseModel):
    name: str
    title: str
    description: str
    questions: list[QuestionInfo]
    primary: str
    sample_count: int


class Sample(BaseModel):
    id: str
    body: str
    labels: dict[str, Label]


class AnswerView(BaseModel):
    type: QuestionType
    # choice は選択肢キー、score は四捨五入した段階、noul は閾値判定の結果
    prediction: Label
    # score の連続値、または noul の値
    value: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = {}


class JudgeResult(BaseModel):
    answers: dict[str, AnswerView]
    model: str
    input_tokens: int
    latency_ms: float
    cost_usd: float


class CaseResult(BaseModel):
    sample: Sample
    result: JudgeResult
    ok: dict[str, bool]


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float


class QuestionMetrics(BaseModel):
    id: str
    type: QuestionType
    accuracy: float
    ece: float | None = None
    reliability: list[CalibrationBin] = []
    mae: float | None = None
    brier: float | None = None


class EvalReport(BaseModel):
    app: str
    n: int
    questions: list[QuestionMetrics]
    mean_latency_ms: float
    total_cost_usd: float
    cases: list[CaseResult]


def _options(q: Question, display: QuestionDisplay) -> dict[str, str]:
    match q.type:
        case "choice":
            keys = list(q.criteria)
        case "score":
            keys = [str(i) for i in range(len(q.criteria))]
        case "noul":
            keys = ["true", "false"]
    defaults = {"true": "該当", "false": "非該当"}
    return {k: display.labels.get(k, defaults.get(k, k)) for k in keys}


def app_info(spec: AppSpec) -> AppInfo:
    return AppInfo(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        questions=[
            QuestionInfo(
                id=qid,
                type=q.type,
                title=spec.display[qid].title,
                instructions=str(q.instructions),
                options=_options(q, spec.display[qid]),
            )
            for qid, q in spec.questions.items()
        ],
        primary=spec.primary_question,
        sample_count=len(load_dataset(spec)),
    )


def load_dataset(spec: AppSpec) -> list[Sample]:
    def parse(line: str) -> Sample:
        row = json.loads(line)
        return Sample(id=row["id"], body=row["body"], labels={k: row[k] for k in spec.questions if k in row})

    return [parse(line) for line in spec.dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _view(qid: str, question: Question, answer: object) -> AnswerView:
    match answer:
        case ChoiceAnswer():
            view = AnswerView(
                type="choice",
                prediction=answer.choice,
                confidence=answer.confidence,
                probabilities=dict(answer.probabilities),
            )
        case ScoreAnswer():
            view = AnswerView(
                type="score",
                prediction=round(answer.score),
                value=answer.score,
                confidence=answer.confidence,
                probabilities={str(k): v for k, v in answer.probabilities.items()},
            )
        case NoulAnswer():
            view = AnswerView(type="noul", prediction=answer.noul >= NOUL_THRESHOLD, value=answer.noul)
        case _:
            raise TypeError(f"質問 {qid} の回答型が未対応です: {type(answer).__name__}")
    if view.type != question.type:
        raise TypeError(f"質問 {qid} の回答型が想定外です: {view.type}（期待: {question.type}）")
    return view


def answer_views(questions: Mapping[str, Question], response: SystemOneResponse) -> dict[str, AnswerView]:
    """応答を画面・集計用の形にする。質問をその場で組み立てる運用（受付箱）からも使う。"""
    missing = set(questions) - set(response.answers)
    if missing:
        raise KeyError(f"応答に回答が含まれていない質問があります: {sorted(missing)}")
    return {qid: _view(qid, q, response.answers[qid]) for qid, q in questions.items()}


def _answers(spec: AppSpec, response: SystemOneResponse) -> dict[str, AnswerView]:
    return answer_views(spec.questions, response)


async def run_judge(client: AsyncTypeSafeClient, ledger: Ledger, spec: AppSpec, body: str) -> JudgeResult:
    judged = await judge(client, ledger, app=spec.name, state=spec.state(body), questions=spec.questions)
    r = judged.response
    return JudgeResult(
        answers=_answers(spec, r),
        model=r.model,
        input_tokens=r.usage.input_tokens or 0,
        latency_ms=round(judged.latency_ms, 1),
        cost_usd=judged.cost_usd,
    )


def _is_correct(view: AnswerView, label: Label) -> bool:
    match view.type:
        case "choice":
            return view.prediction == str(label)
        case "score":
            return view.prediction == int(label)
        case "noul":
            return view.prediction == bool(label)


def grade(sample: Sample, result: JudgeResult) -> CaseResult:
    ok = {qid: _is_correct(result.answers[qid], label) for qid, label in sample.labels.items()}
    return CaseResult(sample=sample, result=result, ok=ok)


def _question_metrics(qid: str, qtype: QuestionType, cases: list[CaseResult]) -> QuestionMetrics:
    graded = [c for c in cases if qid in c.ok]
    oks = [c.ok[qid] for c in graded]
    views = [c.result.answers[qid] for c in graded]
    labels = [c.sample.labels[qid] for c in graded]
    base = QuestionMetrics(id=qid, type=qtype, accuracy=metrics.accuracy(oks))
    match qtype:
        case "choice" | "score":
            bins = metrics.reliability([v.confidence or 0.0 for v in views], oks)
            return base.model_copy(
                update={
                    "ece": metrics.ece(bins),
                    "reliability": [CalibrationBin(**b.__dict__) for b in bins],
                    "mae": metrics.mae([v.value or 0.0 for v in views], [float(y) for y in labels])
                    if qtype == "score"
                    else None,
                }
            )
        case "noul":
            return base.model_copy(
                update={"brier": metrics.brier([v.value or 0.0 for v in views], [bool(y) for y in labels])}
            )


def summarize(spec: AppSpec, cases: list[CaseResult]) -> EvalReport:
    return EvalReport(
        app=spec.name,
        n=len(cases),
        questions=[_question_metrics(qid, q.type, cases) for qid, q in spec.questions.items()],
        mean_latency_ms=fmean(c.result.latency_ms for c in cases) if cases else 0.0,
        total_cost_usd=sum(c.result.cost_usd for c in cases),
        cases=cases,
    )


async def evaluate(client: AsyncTypeSafeClient, ledger: Ledger, spec: AppSpec) -> EvalReport:
    samples = load_dataset(spec)
    # 並列実行中は個別チェックが同時に通りうるため、一括分の見込みを先に確認する
    ledger.ensure_within(sum(cost_of(estimate_tokens(spec.state(s.body), spec.questions)) for s in samples))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(sample: Sample) -> CaseResult:
        async with semaphore:
            return grade(sample, await run_judge(client, ledger, spec, sample.body))

    return summarize(spec, list(await asyncio.gather(*(run(s) for s in samples))))
