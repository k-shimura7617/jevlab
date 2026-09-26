"""障害報告トリアージの質問定義。

架空のEC事業者「ミナト商店」の社内障害チャンネルに投稿された第一報を想定。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Choice, Noul, Score

APP_NAME: Final = "incident"

COMPONENT_LABELS: Final[dict[str, str]] = {
    "db": "DB",
    "network": "ネットワーク",
    "app": "アプリ",
    "saas": "外部SaaS",
}

SEVERITY_LABELS: Final[dict[str, str]] = {"0": "軽微", "1": "中", "2": "高", "3": "致命的"}

QUESTIONS: Final = {
    "severity": Score(
        instructions="ECサイト『ミナト商店』の障害報告 `report.body` に書かれた障害の重大度",
        criteria=[
            "軽微。見た目の崩れや一部の管理画面の不便など、業務や売上への影響がほぼない",
            "中。一部の機能や一部の利用者に障害があるが、回避策があるか主要な購入導線は動いている",
            "高。購入・決済・ログインなど主要機能が多くの利用者で使えない、または大幅に遅い",
            "致命的。サイト全体の停止、データ消失・破損、個人情報漏えいなどのセキュリティ事故",
        ],
    ),
    "component": Choice(
        instructions="`report.body` の障害の原因または発生箇所として最も疑わしいもの",
        criteria={
            "db": "データベース。クエリの遅延、コネクション枯渇、レプリケーション遅延、ディスク容量、ロック待ちなど",
            "network": "ネットワーク。DNS、ロードバランサ、CDN、証明書、疎通不可、パケットロスなど",
            "app": "自社アプリケーション。デプロイ後の不具合、例外、メモリリーク、設定ミス、バッチの異常など",
            "saas": "外部SaaS。決済代行、メール配信、認証基盤、配送APIなど他社サービス側の障害",
        },
    ),
    "customer_impact": Noul(
        instructions="`report.body` の障害が、社外の顧客（サイトの購入者や取引先）に実際に影響している",
    ),
    "mitigated": Noul(
        instructions=(
            "`report.body` の時点で、切り戻し・再起動・切り替えなどの一時対応により症状が止まっている"
            "（恒久対策は未完了でもよい）"
        ),
    ),
}
