"""受付箱で使う質問。

- 個人情報（Kev）: 規則で拾った候補ごとの Noul と、候補以外の個人情報が残っているかの Noul
- 仕分け（Jev）: 既存のメール仕分けの 3 問に、優先度の 2 問と、チケット項目の抽出（候補から選ぶ Choice）を足す

独立した質問は 1 回の問い合わせにまとめる（並列に判定され、往復が 1 回で済む）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from typesafe_sdk import Choice, Noul, Score

from jevlab.apps.mail.questions import QUESTIONS as MAIL_QUESTIONS
from jevlab.core.client import Question
from jevlab.ops.models import CategoryDef, StaffMember
from jevlab.ops.pii import PII_LABELS, Span

NONE_KEY: Final = "none"
# Choice の選択肢は最大 255（どれでもない、の分を残す）
MAX_OPTIONS: Final = 254
LEFTOVER_ID: Final = "pii_leftover"


def candidate_id(i: int) -> str:
    return f"pii_{i}"


def guard_state(text: str, spans: Iterable[Span]) -> dict[str, object]:
    return {
        "mail": {
            "body": text,
            "candidates": [{"text": s.text, "looks_like": PII_LABELS[s.type]} for s in spans],
        }
    }


def guard_questions(spans: list[Span], ask: Iterable[int]) -> dict[str, Question]:
    """ask に挙げた候補（規則で決めきれないもの）だけを判定する。候補一覧自体は全件を state に載せる。"""
    questions: dict[str, Question] = {
        candidate_id(i): Noul(
            instructions=(
                f"`mail.candidates[{i}].text` は、`mail.body` の中で個人（書き手・家族・知人・担当者など）を"
                "特定したり連絡を取ったりできる個人情報として書かれている"
            ),
            criteria={
                "true": "個人の氏名・住所・電話番号・メールアドレス・口座・カード番号・生年月日など、特定の個人に結びつく情報",
                "false": "店・会社の名前や代表連絡先、商品名、注文番号、日付や金額など、個人を特定しない情報",
            },
        )
        for i in ask
    }
    questions[LEFTOVER_ID] = Noul(
        instructions=(
            "`mail.body` に、`mail.candidates` に挙がっていない個人情報"
            "（個人の氏名・住所・電話番号・メールアドレス・口座・カード番号・生年月日）がまだ含まれている"
        ),
        criteria={
            "true": "候補にない個人情報が本文に残っている",
            "false": "個人情報は候補に挙がっているものだけで、ほかには含まれていない",
        },
    )
    return questions


# ---- 仕分け・優先度 ----

PRIORITY_QUESTIONS: Final[dict[str, Question]] = {
    "refund": Score(
        instructions="`mail.body` の書き手が、店に返金・交換・補償をどの程度求めているか",
        criteria=[
            "求めていない。質問・感想・連絡のみ",
            "可能かどうかを尋ねている、または検討している様子がある",
            "返金・交換・補償をはっきり求めている",
        ],
    ),
    # 返金度（Score）には「求めていない」があっても「触れていない」を区別する段階がない。
    # 触れていない件では返金度を優先度に足さないためのゲート
    "refund_mentioned": Noul(
        instructions="`mail.body` に、返金・交換・補償についての記述がある",
        criteria={
            "true": "返金・交換・補償に触れている（求める・尋ねる・断るのいずれでもよい）",
            "false": "返金・交換・補償には触れていない",
        },
    ),
    # Score や Choice には「判断できない」を表す選択肢を置けないため、判断材料の有無を別の問いで聞く
    "insufficient": Noul(
        instructions=(
            "`mail.body` だけでは、問い合わせ・クレーム・お礼・その他のどれに当たるかを決めるための情報が足りない"
        ),
        criteria={
            "true": "本文が短い・用件が書かれていない・前後の文脈がないと分からないなど、種類を決められない",
            "false": "用件が読み取れ、種類を決められる",
        },
    ),
    "publicity": Noul(
        instructions=(
            "`mail.body` の書き手が、SNS への投稿・レビューへの書き込み・消費者センターや弁護士への相談など、"
            "店の外に問題を公にする可能性を示している"
        ),
        criteria={
            "true": "公にする・第三者に相談するとはっきり書いている、または強くほのめかしている",
            "false": "そのような記述はない",
        },
    ),
}


# ---- チケット項目の抽出（候補をコードで拾い、モデルに選ばせる） ----


@dataclass(frozen=True)
class FieldSpec:
    id: str
    title: str
    pattern: re.Pattern[str]
    instructions: str
    none_description: str


_D = "[0-9０-９]"
FIELDS: Final[tuple[FieldSpec, ...]] = (
    FieldSpec(
        id="order_id",
        title="注文番号",
        pattern=re.compile(r"[A-Z]{2,4}-\d{6}-\d{4}"),
        instructions="`mail.body` の用件が対象としている注文の番号を選ぶ",
        none_description="用件の対象となる注文番号が本文に書かれていない、または候補のどれでもない",
    ),
    FieldSpec(
        id="due_date",
        title="希望日・期限",
        pattern=re.compile(
            rf"{_D}{{1,2}}月{_D}{{1,2}}日|(?<!{_D}){_D}{{1,2}}/{_D}{{1,2}}(?![/{_D[1:-1]}])|今日中|本日中|明日|明後日|今週末|来週[月火水木金土日]?曜?日?"
        ),
        instructions=(
            "`mail.body` で書き手が店に対応を求めている期限や、商品が必要な日付（到着希望日・イベントの日など）を選ぶ"
        ),
        none_description="対応の期限や必要な日付は書かれていない（書かれた日付は購入日・到着日など過去の事実だけ）",
    ),
    FieldSpec(
        id="amount",
        title="金額",
        pattern=re.compile(rf"{_D}{{1,3}}(?:[,，]{_D}{{3}})+円|{_D}+円"),
        instructions="`mail.body` の用件の対象になっている金額（返金・請求・二重決済などの額）を選ぶ",
        none_description="用件の対象になる金額は書かれていない",
    ),
)
FIELD_TITLES: Final[dict[str, str]] = {f.id: f.title for f in FIELDS}


def field_candidates(text: str) -> dict[str, list[str]]:
    """項目ごとの候補（出現順・重複なし）。"""
    return {f.id: list(dict.fromkeys(m.group(0) for m in f.pattern.finditer(text))) for f in FIELDS}


def field_questions(candidates: Mapping[str, list[str]]) -> dict[str, Question]:
    """候補の文字列そのものを選択肢にし、どれでもない場合の選択肢を足す（公式の抽出の cookbook と同じ形）。"""
    questions: dict[str, Question] = {}
    for f in FIELDS:
        values = candidates.get(f.id, [])[:MAX_OPTIONS]
        if not values:
            continue
        criteria: dict[str, str | None] = {v: None for v in values}
        criteria[NONE_KEY] = f.none_description
        questions[f.id] = Choice(instructions=f.instructions, criteria=criteria)
    return questions


def classify_state(text: str) -> dict[str, object]:
    # 候補は選択肢として質問側に載せるため、state には本文だけを置く
    return {"mail": {"body": text}}


def category_question(categories: Sequence[CategoryDef]) -> Choice:
    """運用の分類（設定で編集できる）から、仕分けの質問を作る。問いの文はメール仕分けのものを使う。"""
    base = MAIL_QUESTIONS["category"]
    return Choice(instructions=base.instructions, criteria={c.key: c.criteria for c in categories})


def classify_questions(candidates: Mapping[str, list[str]], categories: Sequence[CategoryDef]) -> dict[str, Question]:
    return {
        **MAIL_QUESTIONS,
        "category": category_question(categories),
        **PRIORITY_QUESTIONS,
        **field_questions(candidates),
    }


def picked_value(candidates: Mapping[str, list[str]], field_id: str, key: str) -> str | None:
    """選ばれた候補。候補にない値（どれでもない・想定外の応答）は None。"""
    return key if key != NONE_KEY and key in candidates.get(field_id, []) else None


# ---- 担当者の推定 ----

ASSIGNEE_ID: Final = "assignee"


def assignee_question(staff: Sequence[StaffMember], examples: Mapping[str, list[str]]) -> Choice | None:
    """担当範囲の説明（と最近の対応例）から、誰が対応すべきかを選ばせる。担当者がいなければ None。"""
    if not staff:
        return None

    def describe(s: StaffMember) -> str:
        head = f"{s.name}（{s.role}）" if s.role else s.name
        scope = f": {s.scope}" if s.scope else ""
        shown = examples.get(s.id, [])
        tail = f"。最近の対応例: {'／'.join(f'「{t}」' for t in shown)}" if shown else ""
        return f"{head}{scope}{tail}"

    criteria: dict[str, str | None] = {s.id: describe(s) for s in staff[:MAX_OPTIONS]}
    criteria[NONE_KEY] = "どの担当者の担当範囲にも当てはまらない"
    return Choice(instructions="`mail.body` の用件に対応すべき担当者", criteria=criteria)
