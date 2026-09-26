"""投稿モデレーションアプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.moderation.questions import APP_NAME, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

_FLAG: Final = {"true": "違反", "false": "問題なし"}

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="投稿モデレーション",
    description="地域掲示板への投稿を、誹謗中傷・個人情報・宣伝スパム・危険行為の4観点で個別に判定する",
    subject="post",
    questions=QUESTIONS,
    display={
        "abuse": QuestionDisplay("誹謗中傷", _FLAG),
        "pii": QuestionDisplay("個人情報", _FLAG),
        "spam": QuestionDisplay("宣伝スパム", _FLAG),
        "danger": QuestionDisplay("危険行為", _FLAG),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
