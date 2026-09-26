"""返信前チェック。

お客様の問い合わせと返信の下書きから、Jev が次を 1 回の問い合わせでまとめて判定する。
- 質問・依頼に答えているか
- 方針を超えた約束（返金・交換・補償・期日の確約など）をしていないか
- 謝罪が適切か（足りない／適切／過剰）
- 足りない情報（注文番号・期限・手順など）。候補ごとに「必要なのに書かれていない」かを聞く
- 言い方（きつい・素っ気ない）。言い方チェックの観点を流用する
Jev は判定だけで文章は書かないため、直した案は生成器（Claude）に任せ、その案を判定し直して前後を比べる。

問い合わせにも下書きにも個人情報が入りうるので、規則で拾った候補をすべて伏せてから Jev・Claude に送る。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, StringConstraints
from typesafe_sdk import Noul, Score

from jevlab.core.client import Question
from jevlab.core.engine import AnswerView
from jevlab.core.generator import ClaudeModel
from jevlab.ops.pii import detect, mask_all
from jevlab.tools import tone

APP_NAME: Final = "tool-reply"
MAX_CHARS: Final = 4000
DEFAULT_POLICY: Final = (
    "返金・交換・無償での対応・補償・送料の負担・到着日や対応期日の確約は、担当者の確認が済むまで約束しない。\n"
    "確認中であること、次に何をするか、いつまでに連絡するかは伝えてよい。"
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHARS)]
Policy = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Polarity = Literal["good", "bad"]
Level = Literal["ok", "warn", "bad"]
Verdict = Literal["ok", "review", "caution"]


def _noul(instructions: str, true: str, false: str) -> Noul:
    return Noul(instructions=instructions, criteria={"true": true, "false": false})


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    polarity: Polarity
    question: Question


def _from_tone(aspect_id: str) -> Check:
    """言い方チェックの観点を、返信の下書き（相手はお客様）を読む質問にする。"""
    a = tone.ASPECT_BY_ID[aspect_id]
    q = a.question
    if not isinstance(q, Noul) or not isinstance(q.instructions, str):
        raise TypeError(f"言い方チェックの観点 {aspect_id} は Noul ではありません")
    text = re.sub(r"\s*`message\.recipient`\s*", "お客様", q.instructions.replace("`message.text`", "`reply.draft`"))
    return Check(a.id, a.title, a.polarity, q.model_copy(update={"instructions": text}))


CHECKS: Final[tuple[Check, ...]] = (
    Check(
        "answers",
        "質問に答えている",
        "good",
        _noul(
            "`reply.draft` は、`reply.inquiry` の質問・依頼のすべてに答えている（答えられない点は、その理由と次の対応を書いている）",
            "問い合わせの用件ごとに、答えか次の対応が書かれている",
            "答えていない質問・依頼がある、または話がずれている",
        ),
    ),
    Check(
        "overpromise",
        "方針を超えた約束",
        "bad",
        _noul(
            "`reply.draft` に、`reply.policy` で約束してはいけないとされていることを約束・確約する表現がある",
            "方針では確認が要ることを、確定したことのように約束している",
            "方針の範囲の内容だけ、または確認中と伝えている",
        ),
    ),
    _from_tone("harsh"),
    _from_tone("curt"),
)
CHECK_BY_ID: Final[dict[str, Check]] = {c.id: c for c in CHECKS}

APOLOGY_ID: Final = "apology"
APOLOGY_QUESTION: Final = Score(
    instructions="`reply.inquiry` の内容（お客様の不便・不満の有無）に対して、`reply.draft` の謝罪の量はどうか",
    criteria=[
        "足りない。不便や不満を受けているのに、謝罪やお詫びがない・弱い",
        "適切。状況に合った謝罪がある、または謝罪の要らない問い合わせで謝罪していない",
        "過剰。必要以上に謝り、責任を認めすぎている、または謝罪が長く用件が埋もれている",
    ],
)

# 足りないかもしれない情報の候補。候補ごとに「必要なのに書かれていない」かを聞く（複数あてはまりうるため Choice ではなく Noul）
MISSING: Final[dict[str, str]] = {
    "order_id": "注文番号・問い合わせ番号",
    "deadline": "期限・日時（いつまでに・いつ頃）",
    "steps": "お客様がすること・手順",
    "next_action": "こちらが次にすること",
    "contact": "連絡先・問い合わせ窓口",
}


def missing_id(key: str) -> str:
    return f"missing_{key}"


def questions() -> dict[str, Question]:
    missing = {
        missing_id(k): _noul(
            f"`reply.inquiry` への返信として、`reply.draft` には「{label}」を書く必要があるのに、書かれていない",
            "この返信に必要なのに書かれていない",
            "書かれている、またはこの返信には要らない",
        )
        for k, label in MISSING.items()
    }
    return {**{c.id: c.question for c in CHECKS}, APOLOGY_ID: APOLOGY_QUESTION, **missing}


def mask(text: str) -> str:
    """規則で拾った個人情報の候補をすべて伏せる（人が確認しないので安全側）。"""
    return mask_all(text, detect(text))


def state(inquiry: str, draft: str, policy: str) -> dict[str, object]:
    return {"reply": {"inquiry": inquiry, "draft": draft, "policy": policy or DEFAULT_POLICY}}


# ---- 結果 ----

BAD_AT: Final = tone.BAD_AT
WARN_AT: Final = tone.WARN_AT
MISSING_AT: Final = 0.5


class CheckResult(BaseModel):
    id: str
    title: str
    polarity: Polarity
    value: float
    # 悪さ（0〜1）
    badness: float
    level: Level
    note: str


class MissingItem(BaseModel):
    key: str
    label: str
    probability: float


class ReplyResult(BaseModel):
    verdict: Verdict
    verdict_note: str
    checks: list[CheckResult]
    missing: list[MissingItem]
    # 判定に送った本文（個人情報の候補を伏せたもの）
    sent_inquiry: str
    sent_draft: str
    model: str
    latency_ms: float
    cost_usd: float


def _level(bad: float) -> Level:
    return "bad" if bad >= BAD_AT else "warn" if bad >= WARN_AT else "ok"


def _check(c: Check, view: AnswerView) -> CheckResult:
    value = view.value or 0.0
    bad = value if c.polarity == "bad" else 1 - value
    yes = value >= 0.5
    note = ("できている" if yes else "足りない") if c.polarity == "good" else ("あり" if yes else "なし")
    return CheckResult(
        id=c.id, title=c.title, polarity=c.polarity, value=value, badness=bad, level=_level(bad), note=note
    )


def _apology(view: AnswerView) -> CheckResult:
    """真ん中（適切）が良い段階なので、「適切」の確率で良し悪しを決める（言い方チェックの丁寧さと同じ）。"""
    probs = view.probabilities
    if not {"0", "1", "2"} <= probs.keys():
        raise ValueError(f"謝罪の判定に段階ごとの確率がありません: {sorted(probs)}")
    off = 1 - probs["1"]
    level = _level(off)
    note = "適切" if level == "ok" else "足りない" if probs["0"] >= probs["2"] else "過剰"
    return CheckResult(
        id=APOLOGY_ID, title="謝罪", polarity="good", value=view.value or 0.0, badness=off, level=level, note=note
    )


def _problem(c: CheckResult) -> str:
    if c.id == APOLOGY_ID:
        return f"謝罪（{c.note}）"
    return c.title if c.polarity == "bad" else f"{c.title}（{c.note}）"


def build_result(
    views: Mapping[str, AnswerView],
    *,
    sent_inquiry: str,
    sent_draft: str,
    model: str,
    latency_ms: float,
    cost_usd: float,
) -> ReplyResult:
    checks = [_check(c, views[c.id]) for c in CHECKS] + [_apology(views[APOLOGY_ID])]
    missing = [
        MissingItem(key=k, label=label, probability=views[missing_id(k)].value or 0.0)
        for k, label in MISSING.items()
        if (views[missing_id(k)].value or 0.0) >= MISSING_AT
    ]
    bad = [_problem(c) for c in checks if c.level == "bad"]
    warn = [_problem(c) for c in checks if c.level == "warn"]
    lacks = [m.label for m in missing]
    if any(c.id in ("overpromise", "answers") and c.level == "bad" for c in checks):
        verdict: Verdict = "caution"
        note = "このまま送らないでください: " + "・".join(bad)
    elif bad or warn or lacks:
        verdict = "review"
        note = "見直すと良くなる点があります: " + "・".join(bad + warn + [f"{x}がない" for x in lacks])
    else:
        verdict, note = "ok", "このまま送って大丈夫そうです"
    return ReplyResult(
        verdict=verdict,
        verdict_note=note,
        checks=checks,
        missing=missing,
        sent_inquiry=sent_inquiry,
        sent_draft=sent_draft,
        model=model,
        latency_ms=round(latency_ms, 1),
        cost_usd=cost_usd,
    )


# ---- 直した案（Claude） ----

REWRITE_SYSTEM: Final = """あなたはカスタマーサポートの返信を直す編集者です。
お客様の問い合わせと、担当者が書いた返信の下書きを読み、指摘された点が直るように書き換えてください。

守ること:
- 会社の方針を超える約束（返金・交換・補償・期日の確約など）はしない。確認中であること、次に何をするか、いつまでに連絡するかを書く。
- 問い合わせの質問・依頼のすべてに答えるか、答えられない理由と次の対応を書く。
- 謝罪は状況に合った量にする。
- 足りない情報は作らずに【注文番号を記入】のような空欄を置く（多くても 3 つ）。
- 【氏名】【電話番号】のような伏せ字はそのまま残す。
- 問題のない部分は、担当者の言い回しをなるべく残す。
- 出力は指定の JSON だけ。"""


DRAFT_SYSTEM: Final = """あなたはカスタマーサポートの担当者です。
お客様の問い合わせを読み、送る前の返信の案を書いてください。

守ること:
- 会社の方針を超える約束（返金・交換・補償・期日の確約など）はしない。確認中であること、次に何をするか、いつまでに連絡するかを書く。
- 問い合わせの質問・依頼のすべてに答えるか、答えられない理由と次の対応を書く。
- 謝罪は状況に合った量にする。
- 足りない情報は作らずに【注文番号を記入】のような空欄を置く（多くても 3 つ）。
- 【氏名】【電話番号】のような伏せ字はそのまま残す。
- 読みやすいよう、句点（。）のあとで改行する。
- changes には、案で気を付けた点を書く。
- 出力は指定の JSON だけ。"""


class DraftRequest(BaseModel):
    inquiry: Text
    policy: Policy = ""
    model: ClaudeModel = "sonnet"


def draft_prompt(req: DraftRequest) -> str:
    return f"会社の方針:\n{req.policy or DEFAULT_POLICY}\n\nお客様の問い合わせ:\n<<<\n{mask(req.inquiry)}\n>>>"


class Finding(BaseModel):
    title: str = Field(max_length=60)
    detail: str = Field(max_length=200)


class RewriteRequest(BaseModel):
    inquiry: Text
    draft: Text
    policy: Policy = ""
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    model: ClaudeModel = "sonnet"


def rewrite_prompt(req: RewriteRequest) -> str:
    findings = "\n".join(f"- {f.title}: {f.detail}" for f in req.findings) or "- 特になし（より伝わりやすく整える）"
    return (
        f"会社の方針:\n{req.policy or DEFAULT_POLICY}\n\n"
        f"判定で指摘された点:\n{findings}\n\n"
        f"お客様の問い合わせ:\n<<<\n{mask(req.inquiry)}\n>>>\n\n"
        f"返信の下書き:\n<<<\n{mask(req.draft)}\n>>>"
    )
