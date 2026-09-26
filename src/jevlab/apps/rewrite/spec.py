"""書き換えの意味保持チェックアプリの定義。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jevlab.apps.rewrite.questions import APP_NAME, CHANGE_LABELS, QUESTIONS
from jevlab.core.engine import AppSpec, QuestionDisplay

SPEC: Final = AppSpec(
    name=APP_NAME,
    title="書き換えの意味保持チェック",
    description="「原文：」と「書き換え：」の2行を入力し、要約・言い換えで情報の欠落・追加・改変がないかを判定する（公開事例 Lossless Rewrite に着想）",
    subject="pair",
    questions=QUESTIONS,
    display={
        "change": QuestionDisplay("変化の種類", CHANGE_LABELS),
        "severity": QuestionDisplay("影響度", {"0": "影響なし", "1": "軽微", "2": "重大"}),
    },
    dataset_path=Path(__file__).with_name("dataset.jsonl"),
)
