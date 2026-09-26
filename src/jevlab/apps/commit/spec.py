"""コミット種別分類アプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.commit.questions import APP_NAME, QUESTIONS, TYPE_LABELS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="コミット種別の分類",
    description="コミットメッセージと変更要約から、種別・破壊的変更の有無・影響の大きさを判定する",
    subject="commit",
    questions=QUESTIONS,
    display={
        "type": QuestionDisplay("種別", TYPE_LABELS),
        "breaking": QuestionDisplay("破壊的変更", {"true": "あり", "false": "なし"}),
        "impact": QuestionDisplay("影響の大きさ", {"0": "小", "1": "中", "2": "大"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
