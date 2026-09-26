"""送る前の言い方チェック。

文面の目的（依頼・謝罪など）と、伝わり方・分かりやすさ・丁寧さ・謝罪の観点を 1 回の問い合わせでまとめて判定する。
目的に関係しない観点も同じ問い合わせで聞いておき、画面に出すかどうかはコードで決める
（公式の手引きの「関係しそうな質問をまとめて聞き、使う答えだけを選ぶ」形）。
Jev は判定だけで文章は書かないため、書き換え案は生成器（Claude）に任せる。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Annotated, Final, Literal, get_args

from pydantic import BaseModel, Field, StringConstraints
from typesafe_sdk import Choice, Noul, Score

from jevlab.core.client import Question
from jevlab.core.engine import AnswerView
from jevlab.core.generator import ClaudeModel

APP_NAME: Final = "tool-tone"
# none は「指定なし」（一般的なビジネスの相手・場面として判定する）
Recipient = Literal["boss", "colleague", "subordinate", "client", "customer", "friend", "family", "none"]
RECIPIENT_LABELS: Final[dict[Recipient, str]] = {
    "boss": "上司",
    "colleague": "同僚",
    "subordinate": "部下",
    "client": "取引先",
    "customer": "お客様",
    "friend": "友人",
    "family": "家族",
    "none": "指定なし",
}
Medium = Literal["chat", "mail", "none"]
MEDIUM_LABELS: Final[dict[Medium, str]] = {"chat": "チャット", "mail": "メール", "none": "指定なし"}
Purpose = Literal["request", "apology", "thanks", "report", "decline", "casual"]
PURPOSES: Final[tuple[Purpose, ...]] = get_args(Purpose)
PURPOSE_LABELS: Final[dict[Purpose, str]] = {
    "request": "依頼・お願い",
    "apology": "謝罪・お詫び",
    "thanks": "お礼",
    "report": "報告・連絡",
    "decline": "お断り",
    "casual": "雑談・あいさつ",
}


def as_purpose(value: str) -> Purpose:
    for p in PURPOSES:
        if p == value:
            return p
    raise ValueError(f"想定外の目的が返されました: {value!r}")


# 文ごとに判定する上限（長文でも問い合わせが大きくなりすぎないように）
MAX_SENTENCES: Final = 12


def split_sentences(text: str) -> list[str]:
    """句点・感嘆符・疑問符・改行で文に分ける（空の文は捨てる）。"""
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])|\n+", text) if p and p.strip()]
    out: list[str] = []
    for p in parts:
        # 「了解です。」と言われた → 括弧の中の句点で切れた「」と言われた」は前の文につなぐ
        if out and p[0] in _CLOSERS:
            out[-1] += p
        else:
            out.append(p)
    return out


_CLOSERS: Final = "」』）)】"


# ---- 観点 ----

Group = Literal["tone", "politeness", "clarity", "apology"]
GROUP_LABELS: Final[dict[Group, str]] = {
    "tone": "伝わり方",
    "politeness": "丁寧さ",
    "clarity": "分かりやすさ",
    "apology": "謝罪",
}
# 観点が「はい」だと良いのか悪いのか
Polarity = Literal["good", "bad"]


@dataclass(frozen=True)
class Aspect:
    id: str
    group: Group
    title: str
    polarity: Polarity
    question: Question


def _noul(instructions: str, true: str, false: str) -> Noul:
    return Noul(instructions=instructions, criteria={"true": true, "false": false})


ASPECTS: Final[tuple[Aspect, ...]] = (
    Aspect(
        "harsh",
        "tone",
        "きつい・責めている",
        "bad",
        _noul(
            "`message.text` を `message.recipient` が読むと、きつい・威圧的・責められていると感じる",
            "命令口調、詰問、相手の落ち度を強く指摘する表現などがあり、きつく感じる",
            "相手を責める響きはなく、穏やかに読める",
        ),
    ),
    Aspect(
        "sarcasm",
        "tone",
        "皮肉・嫌味",
        "bad",
        _noul(
            "`message.text` に、皮肉や嫌味として受け取られうる表現が含まれている",
            "ほめ言葉や丁寧語の形を借りた当てこすり、遠回しな非難がある",
            "言葉どおりに受け取れる素直な表現だけ",
        ),
    ),
    Aspect(
        "curt",
        "tone",
        "素っ気ない・冷たい",
        "bad",
        _noul(
            "`message.text` は用件だけで、`message.recipient` に素っ気なく冷たい印象を与える",
            "あいさつ・クッション言葉・感謝やねぎらいがなく、事務的で突き放した印象",
            "一言の気づかいや感謝があり、冷たい印象はない",
        ),
    ),
    Aspect(
        "pushy",
        "tone",
        "急かしている",
        "bad",
        _noul(
            "`message.text` は、相手の都合を考えずに必要以上に急かしている",
            "「至急」「今すぐ」「必ず」の重ねがけなど、理由の説明なく強く急かしている",
            "期限を伝えていても、相手の都合への配慮があるか、急かしてはいない",
        ),
    ),
    Aspect(
        "politeness",
        "politeness",
        "相手に合った丁寧さ",
        "good",
        Score(
            instructions="`message.text` の丁寧さは、`message.recipient` との関係と `message.medium` の場面に対してどうか。"
            "「指定なし」なら一般的なビジネスの相手・場面として判断する。"
            "部下など目下の相手でも、命令口調や見下した言い方は「くだけすぎ」とみなす",
            criteria=[
                "くだけすぎ。この相手・場面では失礼、または軽く見えるおそれがある",
                "相手との関係と場面に合った丁寧さ",
                "堅すぎ。敬語や前置きが過剰で、距離を感じさせたり回りくどかったりする",
            ],
        ),
    ),
    Aspect(
        "clear_ask",
        "clarity",
        "してほしいことが明確",
        "good",
        _noul(
            "`message.text` に、相手に何をしてほしいか（依頼・確認してほしい内容）がはっきり書かれている",
            "読み手が次に何をすればよいか、一読で分かる",
            "何をしてほしいのかが書かれていない、またははっきりしない",
        ),
    ),
    Aspect(
        "clear_deadline",
        "clarity",
        "期限・日時が明確",
        "good",
        _noul(
            "`message.text` に、いつまでに・いつ（期限や日時）がはっきり書かれている",
            "日付・時刻、または「本日中」のように具体的な期限がある",
            "期限や日時が書かれていない、または「なるはやで」のように曖昧",
        ),
    ),
    Aspect(
        "ambiguous",
        "clarity",
        "誤解されうる曖昧さ",
        "bad",
        _noul(
            "`message.text` に、指示語や省略のために読み手によって別の意味に取られうる箇所がある",
            "「例の件」「あれ」「適当に」など、前提を知らないと意味が決まらない表現がある",
            "前提を知らない人が読んでも意味が一つに決まる",
        ),
    ),
    Aspect(
        "admits",
        "apology",
        "非を認めて謝っている",
        "good",
        _noul(
            "`message.text` で、書き手は自分（自社）の非を認め、はっきりと謝っている",
            "何について申し訳ないのかを示したうえで、謝罪の言葉がある",
            "謝罪の言葉がない、または形式的で、何を謝っているのか分からない",
        ),
    ),
    Aspect(
        "excuse",
        "apology",
        "言い訳・責任転嫁",
        "bad",
        _noul(
            "`message.text` に、言い訳や責任転嫁（相手・他人・状況のせいにする表現）が含まれている",
            "「〜のせいで」「こちらも〜だったので」など、自分の責任を小さく見せる表現がある",
            "責任を他に向ける表現はない",
        ),
    ),
    Aspect(
        "cause",
        "apology",
        "何が起きたかの説明",
        "good",
        _noul(
            "`message.text` で、何が起きたか・なぜ起きたかを具体的に説明している",
            "起きたことや原因が、相手に分かる具体さで書かれている",
            "何が起きたか・原因の説明がない、または「不手際により」程度で具体性がない",
        ),
    ),
    Aspect(
        "prevention",
        "apology",
        "今後の対応が具体的",
        "good",
        _noul(
            "`message.text` で、今後どうするか（対応や再発防止策）を具体的に書いている",
            "いつまでに何をするか、どう防ぐかが具体的に書かれている",
            "今後の対応がない、または「今後気をつけます」のように具体性がない",
        ),
    ),
)
ASPECT_BY_ID: Final[dict[str, Aspect]] = {a.id: a for a in ASPECTS}

PURPOSE_QUESTION: Final = Choice(
    instructions="`message.text` を書いた主な目的",
    criteria={
        "request": "相手に何かをしてもらうための依頼・お願い・確認の依頼",
        "apology": "自分や自社の落ち度についての謝罪・お詫び",
        "thanks": "感謝・お礼を伝える",
        "report": "進み具合や結果・予定などの報告・連絡",
        "decline": "依頼や誘いを断る",
        "casual": "雑談・あいさつ・近況",
    },
)
IMPRESSION_QUESTION: Final = Choice(
    instructions="`message.text` を読んだ `message.recipient` が受け取る全体の印象",
    criteria={
        "positive": "好意的・前向きに受け取る",
        "neutral": "特に感情は動かず、事務的に受け取る",
        "uneasy": "不安・不満・もやもやを感じる",
        "offended": "怒りや強い不快感を覚える",
    },
)
IMPRESSION_LABELS: Final[dict[str, str]] = {
    "positive": "前向き",
    "neutral": "中立",
    "uneasy": "もやもや",
    "offended": "不快・怒り",
}

# 目的ごとに画面に出すグループ
GROUPS_FOR: Final[dict[Purpose, tuple[Group, ...]]] = {
    "request": ("tone", "politeness", "clarity"),
    "apology": ("tone", "politeness", "apology"),
    "thanks": ("tone", "politeness"),
    "report": ("tone", "politeness", "clarity"),
    "decline": ("tone", "politeness", "clarity"),
    "casual": ("tone", "politeness"),
}


# 目的によっては当てはまらない観点（報告やお断りに「してほしいこと」「期限」を求めない）
EXCLUDED_FOR: Final[dict[Purpose, frozenset[str]]] = {
    "report": frozenset({"clear_ask", "clear_deadline"}),
    "decline": frozenset({"clear_ask", "clear_deadline"}),
}


def aspects_for(purpose: Purpose) -> list[str]:
    excluded = EXCLUDED_FOR.get(purpose, frozenset())
    return [a.id for a in ASPECTS if a.group in GROUPS_FOR[purpose] and a.id not in excluded]


def sentence_id(i: int) -> str:
    return f"s{i}"


def questions(sentences: list[str]) -> dict[str, Question]:
    per_sentence: dict[str, Question] = {
        sentence_id(i): _noul(
            f"`message.sentences[{i}]` は、`message.recipient` にとって、きつい・責めている・冷たいと感じる文である",
            "この文がきつさや冷たさの原因になっている",
            "この文に問題はない",
        )
        for i in range(len(sentences))
    }
    return {
        "purpose": PURPOSE_QUESTION,
        "impression": IMPRESSION_QUESTION,
        **{a.id: a.question for a in ASPECTS},
        **per_sentence,
    }


def state(text: str, recipient: Recipient, medium: Medium, sentences: list[str]) -> dict[str, object]:
    return {
        "message": {
            "text": text,
            "recipient": RECIPIENT_LABELS[recipient],
            "medium": MEDIUM_LABELS[medium],
            "sentences": sentences,
        }
    }


# ---- 結果 ----

Level = Literal["ok", "warn", "bad"]
Verdict = Literal["ok", "review", "caution"]
# 「悪い方」の確率がこれ以上なら注意、WARN 以上なら気になる点として出す
BAD_AT: Final = 0.6
WARN_AT: Final = 0.4


class AspectResult(BaseModel):
    id: str
    group: Group
    title: str
    polarity: Polarity
    # 「はい」の確率（丁寧さは 0〜2 の期待値）
    value: float
    # 悪さ（0〜1）。良い観点は満たしていない確率、丁寧さは「適切」以外の確率
    badness: float
    level: Level
    note: str


class SentenceResult(BaseModel):
    text: str
    score: float
    flagged: bool


class VerdictOut(BaseModel):
    verdict: Verdict
    note: str


class ToneResult(BaseModel):
    # 判定した目的での総合判定
    verdict: Verdict
    verdict_note: str
    # 目的ごとの総合判定（画面で目的を直したときに使う）
    verdicts: dict[str, VerdictOut]
    purpose: Purpose
    purpose_confidence: float
    purpose_probabilities: dict[str, float]
    impression: str
    impression_probabilities: dict[str, float]
    aspects: list[AspectResult]
    groups_for_purpose: dict[str, list[Group]]
    # 目的ごとに見る観点の ID（グループの中でも目的に合わない観点は除く）
    aspects_for_purpose: dict[str, list[str]]
    sentences: list[SentenceResult]
    truncated: bool
    model: str
    latency_ms: float
    cost_usd: float


def _level_noul(value: float, polarity: Polarity) -> Level:
    bad = value if polarity == "bad" else 1 - value
    return "bad" if bad >= BAD_AT else "warn" if bad >= WARN_AT else "ok"


def _politeness(view: AnswerView) -> tuple[float, Level, str]:
    """0: くだけすぎ ／ 1: 適切 ／ 2: 堅すぎ。

    Score の値は段階の期待値なので、「くだけすぎ」と「堅すぎ」に割れると真ん中（適切）に見えてしまう。
    そのため「適切」の確率で良し悪しを決め、ずれの向きは両端の確率を比べて決める。
    """
    probs = view.probabilities
    if not {"0", "1", "2"} <= probs.keys():
        raise ValueError(f"丁寧さの判定に段階ごとの確率がありません: {sorted(probs)}")
    off = 1 - probs["1"]
    level: Level = "bad" if off >= BAD_AT else "warn" if off >= WARN_AT else "ok"
    note = "相手に合っている" if level == "ok" else "くだけすぎ" if probs["0"] >= probs["2"] else "堅すぎ"
    return off, level, note


def aspect_result(a: Aspect, view: AnswerView) -> AspectResult:
    value = view.value or 0.0
    if a.id == "politeness":
        badness, level, note = _politeness(view)
    else:
        badness = value if a.polarity == "bad" else 1 - value
        level = _level_noul(value, a.polarity)
        yes = value >= 0.5
        note = ("できている" if yes else "足りない") if a.polarity == "good" else ("あり" if yes else "なし")
    return AspectResult(
        id=a.id, group=a.group, title=a.title, polarity=a.polarity, value=value, badness=badness, level=level, note=note
    )


def _problem(a: AspectResult) -> str:
    # 見出しでは問題の形で書く（「期限・日時が明確」ではなく「期限・日時が明確（足りない）」）
    if a.id == "politeness":
        return f"{a.title}（{a.note}）"
    return a.title if a.polarity == "bad" else f"{a.title}（足りない）"


def verdict(aspects: list[AspectResult], shown: Collection[str], impression: str) -> tuple[Verdict, str]:
    relevant = [a for a in aspects if a.id in shown]
    bad = [_problem(a) for a in relevant if a.level == "bad"]
    warn = [_problem(a) for a in relevant if a.level == "warn"]
    if impression == "offended" or any(a.id in ("harsh", "sarcasm") and a.level == "bad" for a in relevant):
        head = "相手を不快にさせる表現があります"
        return "caution", f"{head}: {'・'.join(bad)}" if bad else f"{head}（受け取る印象から）"
    if bad or warn:
        return "review", "見直してください: " + "・".join(bad + warn)
    return "ok", "このまま送って大丈夫です"


def build_result(
    answers: Mapping[str, AnswerView],
    sentences: list[str],
    truncated: bool,
    model: str,
    latency_ms: float,
    cost_usd: float,
) -> ToneResult:
    purpose_view = answers["purpose"]
    purpose = as_purpose(str(purpose_view.prediction))
    impression = str(answers["impression"].prediction)
    aspects = [aspect_result(a, answers[a.id]) for a in ASPECTS]
    shown = {p: aspects_for(p) for p in PURPOSES}
    v, note = verdict(aspects, shown[purpose], impression)
    return ToneResult(
        verdict=v,
        verdict_note=note,
        verdicts={
            p: VerdictOut(verdict=pv, note=pn)
            for p, (pv, pn) in ((p, verdict(aspects, ids, impression)) for p, ids in shown.items())
        },
        purpose=purpose,
        purpose_confidence=purpose_view.confidence or 0.0,
        purpose_probabilities=purpose_view.probabilities,
        impression=impression,
        impression_probabilities=answers["impression"].probabilities,
        aspects=aspects,
        groups_for_purpose={p: list(g) for p, g in GROUPS_FOR.items()},
        aspects_for_purpose=shown,
        sentences=[
            SentenceResult(
                text=s,
                score=answers[sentence_id(i)].value or 0.0,
                flagged=(answers[sentence_id(i)].value or 0.0) >= 0.5,
            )
            for i, s in enumerate(sentences)
        ],
        truncated=truncated,
        model=model,
        latency_ms=round(latency_ms, 1),
        cost_usd=cost_usd,
    )


# ---- 書き換え案（Claude） ----

# 前後の空白を除いたうえで 1〜2000 字（空白だけの文面で課金される判定を走らせない）
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

REWRITE_SYSTEM: Final = """あなたは日本語のビジネス文章の編集者です。
依頼者が書いた「送る前の文面」を、指摘された点が直るように書き換えてください。

守ること:
- 伝える事実・依頼内容・約束・数字・日付は変えない。新しい事実や約束を作らない。
- 足りない情報（期限、原因、今後の対応など）があれば、作らずに【期限を記入】のような空欄を置く。
- 相手との関係と場面（チャット・メール）に合った丁寧さにする。チャットなら短く保つ。「指定なし」なら一般的なビジネスの丁寧さにする。
- 部下など目下の相手でも、命令口調や見下した言い方にしない（敬語を重ねる必要はない）。
- 宛名・署名・会社名・件名など、元の文面にない定型は足さない（本文だけを直す）。
- 空欄は本当に必要なものだけにする（多くても 3 つ）。
- 問題のない部分は、書き手の言い回しをなるべく残す。
- 出力は指定の JSON だけ。"""

REWRITE_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "rewritten": {"type": "string", "description": "書き換えた文面"},
        "changes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
            "description": "何をどう直したか（1 項目 40 字程度）",
        },
        "placeholders": {
            "type": "array",
            "items": {"type": "string"},
            "description": "書き手が埋める必要のある空欄（なければ空）",
        },
    },
    "required": ["rewritten", "changes", "placeholders"],
    "additionalProperties": False,
}


class Finding(BaseModel):
    title: str = Field(max_length=60)
    detail: str = Field(max_length=200)


class RewriteRequest(BaseModel):
    text: NonBlank
    recipient: Recipient
    medium: Medium
    purpose: Purpose
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    flagged_sentences: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=MAX_SENTENCES
    )
    model: ClaudeModel = "sonnet"


def rewrite_prompt(req: RewriteRequest) -> str:
    findings = "\n".join(f"- {f.title}: {f.detail}" for f in req.findings) or "- 特になし（より伝わりやすく整える）"
    flagged = "\n".join(f"- 「{s}」" for s in req.flagged_sentences) or "- なし"
    return (
        f"相手: {RECIPIENT_LABELS[req.recipient]}\n"
        f"場面: {MEDIUM_LABELS[req.medium]}\n"
        f"目的: {PURPOSE_LABELS[req.purpose]}\n\n"
        f"判定で指摘された点:\n{findings}\n\n"
        f"特に気になると判定された文:\n{flagged}\n\n"
        f"元の文面:\n<<<\n{req.text}\n>>>"
    )


class RewriteResult(BaseModel):
    rewritten: str
    changes: list[str]
    placeholders: list[str]
    model: str
    latency_ms: float
    reported_cost_usd: float | None
