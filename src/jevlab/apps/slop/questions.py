"""SNS投稿のスロップ検知の質問定義。

公開事例（LinkedIn向けの Slop Filter、UI文言の Taste-lint）に着想を得たもの。
ビジネスSNSに流れてくる投稿が、反応目当てで中身の薄い「スロップ」かを判定する。
"""

from __future__ import annotations

from typing import Final

from typesafe_sdk import Noul, Score

APP_NAME: Final = "slop"

QUESTIONS: Final = {
    "bait": Noul(
        instructions=(
            "ビジネスSNSの投稿 `post.body` が、「いいね・コメント・保存・シェアしてください」「〇〇とコメントした人に送ります」など、"
            "反応を直接促してエンゲージメントを稼ごうとしている"
        ),
    ),
    "hype": Noul(
        instructions=(
            "`post.body` が、「人生が変わる」「9割が知らない」「完全に終わった」など、"
            "根拠のない誇大表現や煽りで注意を引こうとしている"
        ),
    ),
    "substance": Score(
        instructions="`post.body` に含まれる、読み手が持ち帰れる具体的な中身（固有の事実・数値・手順・経験）の量",
        criteria=[
            "中身がない。一般論・精神論・定型句だけで、誰が書いても同じ内容",
            "少しある。具体例や経験が一部あるが、大半は一般論",
            "十分ある。固有の事実・数値・手順・具体的な経験が中心で、読み手が実際に使える",
        ],
    ),
}
