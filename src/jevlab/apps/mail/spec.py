"""メール仕分けアプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.mail.questions import APP_NAME, CATEGORY_LABELS, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="メール仕分け",
    description="雑貨店『こもれび雑貨店』に届いたメール100通を、問い合わせ・クレーム・お礼・その他に仕分け、不満度と緊急度も付ける",
    subject="mail",
    questions=QUESTIONS,
    display={
        "category": QuestionDisplay("分類", CATEGORY_LABELS),
        "frustration": QuestionDisplay("不満度", {"0": "なし", "1": "不満", "2": "強い不満"}),
        "urgent": QuestionDisplay("緊急", {"true": "緊急", "false": "通常"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
    primary="category",
)
