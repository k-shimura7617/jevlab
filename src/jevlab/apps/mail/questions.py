"""メール仕分けの質問定義。

架空のオンライン雑貨店「こもれび雑貨店」の共有受信箱を想定。
主役は分類（問い合わせ／クレーム／お礼／その他）で、不満度と緊急度を同時に付ける。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Choice, Noul, Score

APP_NAME: Final = "mail"

CATEGORY_LABELS: Final[dict[str, str]] = {
    "inquiry": "問い合わせ",
    "complaint": "クレーム",
    "thanks": "お礼",
    "other": "その他",
}

QUESTIONS: Final = {
    "category": Choice(
        instructions="雑貨店『こもれび雑貨店』の受信箱に届いた `mail.body` を、内容に応じて1つに仕分ける",
        criteria={
            "inquiry": "商品・在庫・注文・配送・支払い・サービスについての質問や手続きの依頼。不満の表明が主目的ではないもの",
            "complaint": "商品の不具合・破損・誤配送・請求の誤り・接客など、店に対する不満や苦情を伝え、是正を求めるもの",
            "thanks": "商品や対応への感謝・感想を伝えることが主目的のもの",
            "other": "営業の売り込み・取材・採用・アンケート依頼・迷惑メール・事務連絡など、上記のどれにも当てはまらないもの",
        },
    ),
    "frustration": Score(
        instructions="`mail.body` の書き手が、店に対してどの程度不満を持っているか",
        criteria=[
            "不満はない。平静、または好意的",
            "不満がある。困っている・残念だといった表現はあるが、言葉づかいは丁寧",
            "強い不満。強い非難、返金や法的措置・公的機関への相談の示唆、乱暴な言葉づかいがある",
        ],
    ),
    "urgent": Noul(
        instructions=(
            "`mail.body` に、店がすぐ対応しないと書き手に実害が出る状況が書かれている"
            "（例: 今日・明日の予定に商品が間に合わない、二重決済、発送前の修正依頼、安全上の問題）"
        ),
    ),
}
