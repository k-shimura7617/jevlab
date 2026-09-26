"""コミット種別分類の質問定義。

架空のWebサービスのリポジトリで、コミットメッセージと変更ファイルの要約を1つの本文として与える想定。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Choice, Noul, Score

APP_NAME: Final = "commit"

TYPE_LABELS: Final[dict[str, str]] = {
    "feat": "feat（機能追加）",
    "fix": "fix（不具合修正）",
    "refactor": "refactor（リファクタリング）",
    "docs": "docs（ドキュメント）",
    "test": "test（テスト）",
    "chore": "chore（雑務・ビルド・依存）",
}

QUESTIONS: Final = {
    "type": Choice(
        instructions="コミット `commit.body`（メッセージと変更ファイルの要約）の Conventional Commits の種別",
        criteria={
            "feat": "利用者から見える新しい機能・画面・APIエンドポイント・オプションを追加する",
            "fix": "誤った挙動・例外・表示崩れなど、既存機能の不具合を直す",
            "refactor": "外から見える挙動を変えずに、コードの構造・命名・分割を整理する",
            "docs": "README、コメント、APIドキュメントなど文書だけを変更する",
            "test": "テストコードの追加・修正だけを行い、本体コードは変えない",
            "chore": "依存ライブラリ更新、CI設定、ビルド設定、lint設定など、本体の挙動に関わらない作業",
        },
    ),
    "breaking": Noul(
        instructions=(
            "`commit.body` の変更が、既存の利用者やクライアントのコード・設定の修正を必要とする破壊的変更である"
            "（APIの削除・名前変更・必須パラメータの追加・設定形式の変更など）"
        ),
    ),
    "impact": Score(
        instructions="`commit.body` の変更がシステム全体に与える影響の大きさ（レビューで注意すべき度合い）",
        criteria=[
            "小。1〜2ファイルの局所的な変更で、影響範囲が明確",
            "中。複数のモジュールにまたがるが、影響範囲は把握できる",
            "大。認証・課金・データ構造・共通基盤など広範囲に波及しうる",
        ],
    ),
}
