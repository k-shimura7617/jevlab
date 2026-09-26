from __future__ import annotations

from pathlib import Path

import pytest

from jevlab.apps.triage.questions import DEPARTMENT_LABELS
from jevlab.apps.triage.spec import SPEC
from jevlab.core import engine
from jevlab.core.budget import BudgetExceededError, Ledger
from jevlab.core.client import make_client


def _result(**answers: engine.AnswerView) -> engine.JudgeResult:
    base = {
        "department": engine.AnswerView(
            type="choice", prediction="billing", confidence=0.9, probabilities={"billing": 0.9, "technical": 0.1}
        ),
        "anger": engine.AnswerView(
            type="score", prediction=1, value=1.4, confidence=0.7, probabilities={"0": 0.1, "1": 0.4, "2": 0.5}
        ),
        "refund": engine.AnswerView(type="noul", prediction=True, value=0.8),
        "urgent": engine.AnswerView(type="noul", prediction=False, value=0.2),
    }
    return engine.JudgeResult(answers=base | answers, model="m", input_tokens=400, latency_ms=100.0, cost_usd=0.0)


def test_dataset_is_consistent() -> None:
    samples = engine.load_dataset(SPEC)
    assert len(samples) == 30
    assert len({s.id for s in samples}) == 30
    assert all(set(s.labels) == set(SPEC.questions) for s in samples)
    assert {s.labels["department"] for s in samples} <= set(DEPARTMENT_LABELS)
    assert {s.labels["anger"] for s in samples} <= {0, 1, 2}


def test_app_info_lists_options() -> None:
    info = engine.app_info(SPEC)
    options = {q.id: q.options for q in info.questions}
    assert set(options["department"]) == set(DEPARTMENT_LABELS)
    assert set(options["anger"]) == {"0", "1", "2"}
    assert options["refund"] == {"true": "あり", "false": "なし"}
    assert info.sample_count == 30


def test_grade() -> None:
    sample = engine.Sample(
        id="x", body="b", labels={"department": "billing", "anger": 1, "refund": True, "urgent": False}
    )
    assert engine.grade(sample, _result()).ok == {"department": True, "anger": True, "refund": True, "urgent": True}
    wrong = _result(
        department=engine.AnswerView(type="choice", prediction="sales", confidence=0.6, probabilities={"sales": 0.6}),
        anger=engine.AnswerView(type="score", prediction=2, value=1.6, confidence=0.5, probabilities={"2": 0.5}),
        refund=engine.AnswerView(type="noul", prediction=False, value=0.4),
        urgent=engine.AnswerView(type="noul", prediction=True, value=0.5),
    )
    assert not any(engine.grade(sample, wrong).ok.values())


@pytest.mark.anyio
async def test_judge_mock_roundtrip(mock_env: Path) -> None:
    ledger = Ledger.from_env()
    async with make_client() as client:
        r = await engine.run_judge(client, ledger, SPEC, "請求が二重になっています")
    assert r.model == "mock-jev"
    assert r.answers["department"].prediction in DEPARTMENT_LABELS
    assert set(r.answers["anger"].probabilities) == {"0", "1", "2"}
    assert 0.0 <= (r.answers["refund"].value or 0.0) <= 1.0
    assert ledger.total_usd() == pytest.approx(r.cost_usd)


@pytest.mark.anyio
async def test_evaluate_mock(mock_env: Path) -> None:
    ledger = Ledger.from_env()
    async with make_client() as client:
        report = await engine.evaluate(client, ledger, SPEC)
    assert report.n == 30
    metrics = {m.id: m for m in report.questions}
    assert sum(b.count for b in metrics["department"].reliability) == 30
    assert metrics["anger"].mae is not None
    assert metrics["refund"].brier is not None
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 30


@pytest.mark.anyio
async def test_evaluate_stops_before_calling_when_over_budget(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_BUDGET_USD", "0.000001")
    ledger = Ledger.from_env()
    async with make_client() as client:
        with pytest.raises(BudgetExceededError):
            await engine.evaluate(client, ledger, SPEC)
    assert not ledger.path.exists()
