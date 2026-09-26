"""問い合わせ仕分けの質問定義。

架空の勤怠管理SaaS「TimeNote」のサポート窓口を想定。
4問は同じ state に対して並列・独立に評価される。
"""

from __future__ import annotations

from typing import Final, Literal

from typesafe_sdk import Choice, Noul, Score

APP_NAME: Final = "triage"

Department = Literal["billing", "technical", "sales", "other"]

DEPARTMENT_LABELS: Final[dict[str, str]] = {
    "billing": "請求・支払い",
    "technical": "技術サポート",
    "sales": "営業",
    "other": "その他",
}

QUESTIONS: Final = {
    "department": Choice(
        instructions="勤怠管理SaaS『TimeNote』に届いた `inquiry.body` を、最初に対応すべき担当部署に振り分ける",
        criteria={
            "billing": "既存契約の請求額・支払い方法・二重請求・領収書・返金など、お金の請求や支払いに関する問い合わせ",
            "technical": "ログインできない、エラー、データが表示されない、打刻や外部連携の不具合など、製品の動作に関する問い合わせ",
            "sales": "新規導入の検討、見積もり依頼、上位プランや追加ライセンスの相談、デモ依頼など、購入・契約拡大に関する問い合わせ",
            "other": "採用・取材・営業売り込み・意見のみなど、上記のどれにも当てはまらない問い合わせ",
        },
    ),
    "anger": Score(
        instructions="`inquiry.body` の書き手が、TimeNote またはサポートに対してどの程度怒っているか",
        criteria=[
            "冷静。事実や要望を淡々と伝えており、不満の表現はない",
            "不満がある。困っている・残念だといった表現はあるが、言葉づかいは丁寧",
            "強く怒っている。強い非難、解約や法的措置の示唆、乱暴な言葉づかいがある",
        ],
    ),
    "refund": Noul(
        instructions="`inquiry.body` の書き手が、すでに支払った料金の返金を明確に求めている",
    ),
    "urgent": Noul(
        instructions=(
            "`inquiry.body` に、すぐ対応しないと書き手の業務に実害が出る状況が書かれている"
            "（例: 全社員が打刻できない、本日締めの給与計算ができない）"
        ),
    ),
}
