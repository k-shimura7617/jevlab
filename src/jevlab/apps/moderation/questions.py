"""投稿モデレーションの質問定義。

架空の地域コミュニティ掲示板「まちノート」への投稿を想定。
4つの違反種別をそれぞれ独立した Noul で判定するため、複数の違反が同時に立ちうる。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Noul

APP_NAME: Final = "moderation"

QUESTIONS: Final = {
    "abuse": Noul(
        instructions=(
            "地域掲示板『まちノート』の投稿 `post.body` が、特定の個人・店舗・集団を侮辱したり、"
            "根拠なく貶めたりする誹謗中傷を含む（正当な批判や感想は含まない）"
        ),
    ),
    "pii": Noul(
        instructions=(
            "`post.body` が、本人の同意なく第三者を特定できる個人情報"
            "（氏名と住所・電話番号・勤務先・車のナンバーなどの組み合わせ）を晒している"
        ),
    ),
    "spam": Noul(
        instructions=(
            "`post.body` が、地域の話題と無関係な商品・サービス・副業・投資などへの宣伝や勧誘、"
            "外部サイトへの誘導を目的としている"
        ),
    ),
    "danger": Noul(
        instructions=(
            "`post.body` が、けがや事故・違法行為につながる危険な行為を勧めたり、"
            "やり方を広めたりしている（危険を注意喚起する投稿は含まない）"
        ),
    ),
}
