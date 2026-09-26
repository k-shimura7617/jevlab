"""契約条項リスク判定アプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.contract.questions import APP_NAME, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

_HAS: Final = {"true": "あり", "false": "なし"}

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="契約条項のリスク判定",
    description="SaaS利用規約の条項を、自動更新・違約金・責任制限・データ第三者提供の有無と不利さで一次スクリーニングする",
    subject="clause",
    questions=QUESTIONS,
    display={
        "auto_renewal": QuestionDisplay("自動更新", _HAS),
        "penalty": QuestionDisplay("違約金", _HAS),
        "liability_cap": QuestionDisplay("責任制限", _HAS),
        "data_sharing": QuestionDisplay("データ第三者提供", _HAS),
        "risk": QuestionDisplay("不利さ", {"0": "低", "1": "中", "2": "高"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
