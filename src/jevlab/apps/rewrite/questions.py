"""書き換えの意味保持チェックの質問定義。

公開事例（AIによる書き換えが元の意味を保っているかを検証する Lossless Rewrite、字幕翻訳の意味欠落検出）に着想を得たもの。
本文は「原文：…」と「書き換え：…」の2行で与える。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Choice, Score

APP_NAME: Final = "rewrite"

CHANGE_LABELS: Final[dict[str, str]] = {
    "none": "問題なし",
    "omission": "欠落",
    "addition": "追加",
    "distortion": "改変",
}

QUESTIONS: Final = {
    "change": Choice(
        instructions=(
            "`pair.body` の「原文」を「書き換え」にしたときに起きた、意味上の最も重大な変化"
            "（言い回し・敬語・語順の違いは変化とみなさない）"
        ),
        criteria={
            "none": "原文の情報がすべて保たれ、原文にない情報も加わっていない",
            "omission": "原文にあった条件・期限・対象・注意事項などの情報が書き換えで抜け落ちている",
            "addition": "原文にない事実・約束・条件・主張が書き換えで付け加えられている",
            "distortion": "数値・日付・否定・主語・因果関係などが変わり、原文と異なる意味になっている",
        },
    ),
    "severity": Score(
        instructions="`pair.body` の書き換えによる意味の変化が、読み手の判断や行動を誤らせる度合い",
        criteria=[
            "影響なし。意味は実質的に同じ",
            "軽微。細部が変わっているが、読み手の判断や行動はほぼ変わらない",
            "重大。読み手が誤った判断・行動をとるおそれがある",
        ],
    ),
}
