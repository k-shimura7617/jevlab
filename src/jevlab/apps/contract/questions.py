"""契約条項リスク判定の質問定義。

自社がSaaSを導入する側（利用者）として、ベンダーの利用規約・契約書の条項を1つずつ確認する想定。
法的助言ではなく、法務レビューに回す前の一次スクリーニングを想定している。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Noul, Score

APP_NAME: Final = "contract"

QUESTIONS: Final = {
    "auto_renewal": Noul(
        instructions="SaaS契約の条項 `clause.body` に、期間満了時に申し出がなければ契約が自動的に更新される定めがある",
    ),
    "penalty": Noul(
        instructions=(
            "`clause.body` に、中途解約・契約違反・支払遅延などの際に利用者が違約金・損害賠償の予定額・"
            "残期間分の料金などを支払う定めがある"
        ),
    ),
    "liability_cap": Noul(
        instructions=(
            "`clause.body` に、ベンダー側の損害賠償責任を一定額までに制限する、"
            "または特定の損害について免責する定めがある"
        ),
    ),
    "data_sharing": Noul(
        instructions=(
            "`clause.body` に、利用者のデータや個人情報をベンダーが第三者に提供・共有したり、"
            "自社のサービス改善やAI学習など契約目的以外に利用したりできる定めがある"
        ),
    ),
    "risk": Score(
        instructions="SaaSを導入する利用者の立場から見た、`clause.body` の条項の不利さ（法務確認の優先度）",
        criteria=[
            "低。一般的な内容で、利用者に特段の不利はない",
            "中。利用者にやや不利な点があり、内容の確認や交渉を検討したほうがよい",
            "高。利用者に大きく不利、または一方的で、法務確認なしに合意すべきでない",
        ],
    ),
}
