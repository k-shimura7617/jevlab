"""SNS投稿のスロップ検知アプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.slop.questions import APP_NAME, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="SNS投稿のスロップ検知",
    description="ビジネスSNSの投稿を、反応の誘導・誇大表現・具体性の3観点で判定する（公開事例 Slop Filter / Taste-lint に着想）",
    subject="post",
    questions=QUESTIONS,
    display={
        "bait": QuestionDisplay("反応の誘導", {"true": "あり", "false": "なし"}),
        "hype": QuestionDisplay("誇大表現", {"true": "あり", "false": "なし"}),
        "substance": QuestionDisplay("具体性", {"0": "中身なし", "1": "少しある", "2": "十分"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
