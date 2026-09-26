"""担当範囲の案を、担当者が実際に対応を完了した件から Claude に書かせる。

Jev は判定しかしないため、文章を作る部分は生成器（claude -p）に任せる。
渡すのは、伏せ字にした見出しと分類だけ（本文や個人情報は渡さない）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from pydantic import BaseModel

from jevlab.ops.models import Item, StaffMember

# 案に使う件の上限（新しい順）。多すぎると要点がぼやけ、生成も遅くなる
MAX_ITEMS: Final = 30
MAX_SCOPE: Final = 300

SYSTEM: Final = """あなたは問い合わせ窓口の運用担当です。
担当者が実際に対応を完了した件の見出しと分類から、その担当者の「担当範囲」の説明を日本語で書きます。
この説明は、問い合わせを誰に割り当てるかをモデルが判断するために読みます。

守ること:
- 見出しから読み取れる業務だけを書く。見出しにない業務を足さない。
- 個人名・連絡先などの個人情報は書かない（見出しの【】は伏せ字）。
- ほかの担当者の範囲と区別できる、具体的な言葉で書く（例:「配送の遅れ・誤配送・在庫の確認」）。
- 120 字以内。読点で区切り、箇条書きにしない。
- 出力は指定の JSON だけ。"""

SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "description": "担当範囲の説明（120 字以内）"},
        "notes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "description": "今の説明から変えた点（1 項目 40 字程度）",
        },
    },
    "required": ["scope"],
    "additionalProperties": False,
}


class ScopeDraftInput(BaseModel):
    staff: StaffMember
    others: list[StaffMember]
    # 伏せ字にした見出しと分類名の組（新しい順）
    handled: list[tuple[str, str]]


class ScopeDraft(BaseModel):
    staff_id: str
    scope: str
    notes: list[str]
    based_on: int
    model: str
    latency_ms: float


def build_prompt(data: ScopeDraftInput) -> str:
    s = data.staff
    counts = Counter(category for _, category in data.handled)
    lines = [
        f"担当者: {s.name}（{s.role or '所属なし'}）",
        f"今の担当範囲: {s.scope or '（未記入）'}",
        "",
        "ほかの担当者の範囲（重ならないようにする）:",
        *(f"- {o.name}: {o.scope or '（未記入）'}" for o in data.others),
        "",
        f"対応を完了した件（{len(data.handled)} 件・新しい順）:",
        *(f"- [{category}] {title}" for title, category in data.handled),
        "",
        "分類の内訳: " + "・".join(f"{c} {n} 件" for c, n in counts.most_common()),
    ]
    return "\n".join(lines)


def handled_items(items: Iterable[Item], staff_id: str, allowed: Sequence[str]) -> list[Item]:
    """案の材料にしてよい件（人が割り当てて完了し、個人情報の確認を通った件）。"""
    return [
        i
        for i in items
        if i.status == "closed" and i.assignee == staff_id and i.assigned_by == "human" and i.pii_decision in allowed
    ]
