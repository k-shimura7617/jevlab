"""登録済みの全アプリに共通する整合性チェック。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from jevlab.apps import APPS
from jevlab.core import engine
from jevlab.core.budget import Ledger
from jevlab.core.client import make_client

SPECS = list(APPS.values())


def _ids(spec: engine.AppSpec) -> str:
    return spec.name


@pytest.mark.parametrize("spec", SPECS, ids=_ids)
def test_spec_is_consistent(spec: engine.AppSpec) -> None:
    assert set(spec.display) == set(spec.questions)
    assert spec.primary_question in spec.questions
    # 質問文が state のキーを正しく参照しているか
    assert all(f"`{spec.subject}.body`" in str(q.instructions) for q in spec.questions.values())


@pytest.mark.parametrize("spec", SPECS, ids=_ids)
def test_dataset_labels_are_valid(spec: engine.AppSpec) -> None:
    samples = engine.load_dataset(spec)
    assert len(samples) >= 30
    assert len({s.id for s in samples}) == len(samples)
    for s in samples:
        assert set(s.labels) == set(spec.questions), s.id
        for qid, q in spec.questions.items():
            label = s.labels[qid]
            match q.type:
                case "choice":
                    assert label in q.criteria, (s.id, qid, label)
                case "score":
                    assert type(label) is int and 0 <= label < len(q.criteria), (s.id, qid, label)
                case "noul":
                    assert type(label) is bool, (s.id, qid, label)


@pytest.mark.parametrize("spec", SPECS, ids=_ids)
def test_dataset_covers_every_label(spec: engine.AppSpec) -> None:
    """正解が偏って一部の選択肢・段階・真偽が評価されない状態を防ぐ。"""
    samples = engine.load_dataset(spec)
    for q in engine.app_info(spec).questions:
        # options のキーは choice の選択肢キー / score の段階番号 / noul の "true"・"false"
        counts = Counter(str(s.labels[q.id]).lower() for s in samples)
        assert set(counts) == set(q.options), q.id


@pytest.mark.anyio
@pytest.mark.parametrize("spec", SPECS, ids=_ids)
async def test_evaluate_mock(spec: engine.AppSpec, mock_env: Path) -> None:
    ledger = Ledger.from_env()
    async with make_client() as client:
        report = await engine.evaluate(client, ledger, spec)
    assert report.n == len(engine.load_dataset(spec))
    assert {m.id for m in report.questions} == set(spec.questions)
