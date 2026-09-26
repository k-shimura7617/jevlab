"""問い合わせ仕分けアプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.triage.questions import APP_NAME, DEPARTMENT_LABELS, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="問い合わせ仕分け",
    description="勤怠SaaS『TimeNote』への問い合わせを、担当部署・怒り度・返金要求・緊急度で仕分ける",
    subject="inquiry",
    questions=QUESTIONS,
    display={
        "department": QuestionDisplay("担当部署", DEPARTMENT_LABELS),
        "anger": QuestionDisplay("怒り度", {"0": "冷静", "1": "不満・丁寧", "2": "強く怒っている"}),
        "refund": QuestionDisplay("返金要求", {"true": "あり", "false": "なし"}),
        "urgent": QuestionDisplay("緊急度", {"true": "緊急", "false": "通常"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
