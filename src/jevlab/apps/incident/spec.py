"""障害報告トリアージアプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.incident.questions import APP_NAME, COMPONENT_LABELS, QUESTIONS, SEVERITY_LABELS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="障害報告トリアージ",
    description="ECサイトの障害第一報を、重大度・原因箇所・顧客影響・一時対応の有無で仕分ける",
    subject="report",
    questions=QUESTIONS,
    display={
        "severity": QuestionDisplay("重大度", SEVERITY_LABELS),
        "component": QuestionDisplay("原因箇所", COMPONENT_LABELS),
        "customer_impact": QuestionDisplay("顧客影響", {"true": "あり", "false": "なし"}),
        "mitigated": QuestionDisplay("一時対応", {"true": "済み", "false": "未対応"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
