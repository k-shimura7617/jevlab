"""受付箱の処理の流れ。

受信 → 個人情報のガードレール（Kev）→ 仕分け・抽出・優先度（Jev）→ 振り分け（自動 / 確認待ち / エスカレーション）。
人の操作（個人情報の確認・仕分けの確認・エスカレーション対応）もここで状態を進める。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from typesafe_sdk import AsyncTypeSafeClient, TypeSafeAPIConnectionError, TypeSafeAPITimeoutError

from jevlab.core import target
from jevlab.core.budget import Ledger
from jevlab.core.client import judge
from jevlab.core.engine import AnswerView, answer_views
from jevlab.ops import questions as oq
from jevlab.ops.models import (
    MISS_STATUSES,
    Actor,
    Channel,
    Decider,
    GuardSettings,
    IngestRequest,
    Item,
    KevReference,
    MissReport,
    PiiDecision,
    Settings,
)
from jevlab.ops.pii import (
    PII_LABELS,
    STRUCTURED,
    PiiType,
    Span,
    apply_mask,
    blocked_types,
    detect,
    mask_all,
    validate_span,
)
from jevlab.ops.store import Store, now_iso

log = logging.getLogger(__name__)

ESCALATION_CHANNEL: Final = "#cs-エスカレーション"
INBOUND_CHANNEL: Final = "#お問い合わせ窓口"
# Kev は CPU で動くため 1 本ずつ、Jev は並列にしてもコストは同じ
KEV_CONCURRENCY: Final = 1
# 同時に処理する件の数（ふだん・高速）。Jev の同時の問い合わせは、高速のときの件数まで許す
WORKERS: Final = 3
FAST_WORKERS: Final = 16
_JEV_CONCURRENCY: Final = FAST_WORKERS


class BackendLike(Protocol):
    @property
    def client(self) -> AsyncTypeSafeClient: ...

    @property
    def ledger(self) -> Ledger: ...


class TargetConnectionError(RuntimeError):
    """接続先（Kev・Jev）に接続できない。画面に出す文と、元のエラーを持つ。"""

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


def connection_message(t: target.Target) -> str:
    if t == "custom":
        return f"Kev（{target.kev_url()}）に接続できません。Kev を起動してから「再実行」を押してください"
    if t == "jev":
        return "Jev に接続できません。ネットワークを確かめてから「再実行」を押してください"
    return f"{TARGET_NAMES[t]} に接続できません。「再実行」を押してください"


class TargetUnavailableError(RuntimeError):
    pass


def actor_of(t: target.Target) -> Actor:
    return {"custom": "kev", "jev": "jev", "mock": "mock"}[t]


def decider_of(t: target.Target) -> Decider:
    return {"custom": "kev", "jev": "jev", "mock": "mock"}[t]


TARGET_NAMES: Final[dict[target.Target, str]] = {"custom": "Kev", "jev": "Jev", "mock": "MOCK"}


def category_label(key: str | None, settings: Settings) -> str:
    """分類の表示名（分類は設定で編集できる）。"""
    return settings.category_label(key)


# 担当者の推定に対応例として使ってよい件（個人情報の確認を通り、Jev に送ってよいと判断済み）
EXAMPLE_DECISIONS: Final[frozenset[PiiDecision]] = frozenset({"none", "masked", "allowed"})


# 対応完了の投稿の差出人。Slack ではこの投稿を流さず、元の投稿のスレッドへの返信で知らせる
CLOSE_AUTHOR: Final = "担当者（対応完了）"


def notes_lines(item: Item) -> list[str]:
    """対応のメモを 1 件 1 行で返す（外に出す投稿用）。

    メモは人が自由に書くので、電話番号などが入りうる。規則で見つかる個人情報は伏せてから出す。
    """
    if not item.notes:
        return []
    return ["メモ:", *(f"・{mask_all(n, detect(n))}".replace("\n", " ") for n in item.notes)]


def safe_title(item: Item, limit: int = 30) -> str:
    """チャンネルへの投稿など外に出す見出し。確定済みの個人情報は方針に関係なく伏せる。

    ガードレールを通していない件（無効のとき）は候補がないので、規則で拾った候補をすべて伏せる。
    """
    spans = detect(item.text) if item.pii_decision == "skipped" else item.pii
    masked = mask_all(item.text, spans)
    if item.subject.strip() and masked.startswith("件名: "):
        return masked[len("件名: ") :].split("\n", 1)[0].strip()[:limit]
    return masked.replace("\n", " ")[:limit]


def _types_text(spans: list[Span]) -> str:
    kinds = list(dict.fromkeys(PII_LABELS[s.type] for s in spans))
    return "・".join(kinds) if kinds else "なし"


def _stable_unit(seed: str) -> float:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


Route = Literal["routed", "review", "escalated"]


# 返信が要る確率がこれ未満なら、返信は要らないとみなす（返信の要否を判定する分類だけ）
NO_REPLY_BELOW: Final = 0.5


def no_reply_reason(item: Item, settings: Settings) -> str | None:
    """返信が要らない件なら、その理由（完了の記録に書く）。返信が要る件は None。

    返信不要の分類（お礼など）はいつも要らない。返信の要否を判定する分類（その他など）は、Jev の判定で決める。
    """
    label = category_label(item.category, settings)
    if settings.auto_closes(item.category):
        # お礼などは分類の確信度で決める（閾値以上で振り分けた件はそのまま完了）。不満・緊急の兆しでは止めない
        return f"返信のいらない分類（{label}）"
    if _needs_attention(item, settings):
        # 返信の要否を判定する分類では、強い不満や緊急の兆しがある件を自動で完了にしない
        return None
    if settings.judges_reply(item.category):
        view = item.answers.get(oq.REPLY_ID)
        p = view.value if view is not None else None
        if p is not None and p < NO_REPLY_BELOW:
            return f"{label}のうち返信のいらない件（返信が要る確率 {p:.2f}）"
    return None


def _needs_attention(item: Item, settings: Settings) -> bool:
    """強い不満・緊急の兆しがあるか（エスカレーションの設定をオフにしていても見る）。"""
    frustration = item.answers.get("frustration")
    if frustration is not None:
        p2 = frustration.probabilities.get("2")
        if (p2 is not None and p2 >= settings.classify.strong_frustration_at) or (
            p2 is None and frustration.prediction == 2
        ):
            return True
    urgent = item.answers.get("urgent")
    return urgent is not None and urgent.prediction is True


def decide_route(item: Item, settings: Settings) -> tuple[Route, str]:
    """分類の確信度と業務ルールから、自動振り分け・確認待ち・エスカレーションを決める。"""
    c = settings.classify
    answers = item.answers
    conf = item.confidence or 0.0
    auto = c.label_thresholds.get(item.category or "", c.auto_threshold)
    # 返信のいらない分類（お礼など）は、分類の確信度が閾値以上なら、ほかの判定より先に自動で振り分ける（そのまま完了になる）。
    # 目的がお礼と言い切れるなら、不満・緊急の兆しや担当の推定で人を呼ばない
    if settings.auto_closes(item.category) and conf >= auto:
        return "routed", f"返信のいらない分類で確信度 {conf:.2f} ≥ 自動の閾値 {auto:.2f}"
    frustration = answers.get("frustration")
    urgent = answers.get("urgent")
    if c.escalate_strong_frustration and frustration is not None:
        p2 = frustration.probabilities.get("2")
        if p2 is not None and p2 >= c.strong_frustration_at:
            return "escalated", f"強い不満の確率 {p2:.2f} ≥ {c.strong_frustration_at:.2f}"
        if p2 is None and frustration.prediction == 2:
            return "escalated", "強い不満（不満度 2）"
    if c.escalate_urgent and urgent is not None and urgent.prediction is True:
        return "escalated", f"緊急（{urgent.value or 0:.2f}）"
    if conf >= auto:
        split = split_reason(answers, c.split_margin)
        if split:
            return "review", f"判断が割れている（{split}）"
        lacking = answers.get("insufficient")
        if c.insufficient_gate and lacking is not None and (lacking.value or 0.0) >= c.insufficient_at:
            return "review", f"判断材料が足りない（{lacking.value or 0.0:.2f}）"
        return "routed", f"確信度 {conf:.2f} ≥ 自動の閾値 {auto:.2f}"
    if conf >= c.review_threshold:
        return "review", f"確信度 {conf:.2f}（{c.review_threshold:.2f}〜{auto:.2f}）"
    return "escalated", f"確信度 {conf:.2f} < {c.review_threshold:.2f}"


# Score が「割れている」とみなす確率（離れた 2 つの段階がどちらもこれ以上）
_SPLIT_LEVEL_P: Final = 0.25


def _bimodal(probs: Mapping[str, float]) -> bool:
    """離れた 2 つの段階に確率が分かれ、間の段階の方が低い分布か。"""
    levels = [probs.get(str(i), 0.0) for i in range(len(probs))]
    for i in range(len(levels)):
        for j in range(i + 2, len(levels)):
            between = min(levels[i + 1 : j])
            if levels[i] >= _SPLIT_LEVEL_P and levels[j] >= _SPLIT_LEVEL_P and between < min(levels[i], levels[j]):
                return True
    return False


def split_reason(answers: Mapping[str, AnswerView], margin: float) -> str | None:
    """判断が割れているなら、その理由（短い日本語）。割れていなければ None。"""
    category = answers.get("category")
    if category is not None and len(category.probabilities) >= 2:
        first, second = sorted(category.probabilities.values(), reverse=True)[:2]
        if first - second < margin:
            return f"分類の上位 2 つが僅差 {first:.2f} / {second:.2f}"
    frustration = answers.get("frustration")
    if (
        frustration is not None
        and frustration.type == "score"
        and frustration.probabilities
        and (frustration.confidence == 0 or _bimodal(frustration.probabilities))
    ):
        return "不満度の判定が両端に割れている"
    return None


def priority_parts(answers: Mapping[str, AnswerView]) -> dict[str, float]:
    """優先度の観点ごとの値（0〜1）。"""

    def value(qid: str, scale: float) -> float:
        a = answers.get(qid)
        return (a.value or 0.0) / scale if a is not None else 0.0

    # 返金に触れていない件は、返金度の期待値（0 以外になりうる）を足さない
    mentioned = answers.get("refund_mentioned")
    refund = value("refund", 2) if mentioned is None or (mentioned.value or 0.0) >= 0.5 else 0.0
    return {
        "frustration": value("frustration", 2),
        "urgent": value("urgent", 1),
        "refund": refund,
        "publicity": value("publicity", 1),
    }


def priority_score(answers: Mapping[str, AnswerView], weights: Mapping[str, float]) -> float:
    """優先度（観点ごとの値の重み付き平均）。画面でも同じ式で並べ替える。"""
    parts = priority_parts(answers)
    total = sum(max(w, 0.0) for w in weights.values())
    if total <= 0:
        return 0.0
    return sum(parts.get(k, 0.0) * max(w, 0.0) for k, w in weights.items()) / total


@dataclass
class Pipeline:
    store: Store
    backends: Mapping[target.Target, BackendLike]
    kev_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(KEV_CONCURRENCY))
    jev_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(_JEV_CONCURRENCY))
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    # ---- 受信 ----

    def ingest(self, req: IngestRequest, *, via: str | None = None, connector: Channel | None = None) -> Item:
        """受信する。connector は受け付けるかを決めるコネクタ（ファイルの取り込みでは、経路にかかわらず CSV 取り込み）。"""
        settings = self.store.settings()
        gate = connector or req.channel
        if not settings.connectors.get(gate, False):
            raise PermissionError(f"コネクタ「{gate}」は切断されています（コネクタ画面で接続してください）")
        item = self.store.add_item(req)
        sender = f"{req.from_name} <{req.from_address}>" if req.from_address else req.from_name or "不明"
        self.store.add_event(item.id, "received", "connector", f"{via or req.channel} で受信: {sender}")
        if req.channel == "chat":
            self.store.add_post(INBOUND_CHANNEL, req.from_name or "ゲスト", req.body, item.id)
        self.wake.set()
        return item

    # ---- 判定の呼び出し ----

    def _backend(self, t: target.Target) -> BackendLike:
        backend = self.backends.get(t)
        if backend is None:
            raise TargetUnavailableError(f"接続先 {TARGET_NAMES[t]} は使えません（TYPESAFE_API_KEY が未設定）")
        return backend

    async def _ask(
        self, t: target.Target, app: str, state: object, questions: Mapping[str, oq.Question]
    ) -> tuple[dict[str, AnswerView], float, float, str]:
        backend = self._backend(t)
        slots = self.kev_slots if t == "custom" else self.jev_slots
        try:
            async with slots:
                judged = await judge(backend.client, backend.ledger, app=app, state=state, questions=questions)
        except (TypeSafeAPIConnectionError, TypeSafeAPITimeoutError) as e:
            raise TargetConnectionError(connection_message(t), raw=f"{type(e).__name__}: {e}") from e
        return answer_views(questions, judged.response), judged.latency_ms, judged.cost_usd, judged.response.model

    # ---- 1 件の処理 ----

    async def process(self, item: Item) -> None:
        try:
            settings = self.store.settings()
            current = item if item.pii_decision is not None else await self._guard(item, settings)
            if current is None:
                return
            if current.pii_decision == "blocked":
                if current.backfill:
                    self._close_backfill(current, None, "個人情報のため Jev に送らない（試算の対象外）")
                    return
                await self._handle_blocked(current, settings)
                return
            await self._classify_and_route(current, settings)
        except Exception as e:  # 1 件の失敗で処理全体を止めず、件に記録して人が再実行できるようにする
            # 接続できないときは、何をすれば直るかを書く（元のエラーは経過のデータに残す）
            message = str(e) if isinstance(e, TargetConnectionError) else f"{type(e).__name__}: {e}"
            raw = e.raw if isinstance(e, TargetConnectionError) else message
            log.exception("件 %s の処理に失敗しました", item.id)
            self.store.update(item.id, lambda i: i.model_copy(update={"status": "error", "error": message}))
            self.store.add_event(item.id, "error", "system", f"処理に失敗しました: {message}", {"raw": raw})

    async def _guard(self, item: Item, settings: Settings) -> Item | None:
        g = settings.guard
        if not g.enabled:
            self.store.add_event(item.id, "guard", "system", "ガードレールは無効 → そのまま仕分け")
            return self.store.update(
                item.id, lambda i: i.model_copy(update={"pii_decision": "skipped", "sent_text": i.text})
            )
        spans = detect(item.text)
        if item.backfill:
            # 過去の問い合わせは人が確認しないので、候補をすべて個人情報として伏せる（安全側。Kev も使わない）
            judged = [s.model_copy(update={"confirmed": True}) for s in spans]
            self.store.add_event(
                item.id,
                "guard",
                "system",
                f"過去の問い合わせ（試算用）: 候補 {len(spans)} 件をすべて個人情報として扱う（{_types_text(judged)}）",
                {"candidates": [s.model_dump() for s in judged]},
            )
            item = self.store.update(item.id, lambda i: i.model_copy(update={"pii": judged, "pii_leftover": None}))
            return self._apply_pii(item, judged, settings, force_block=False, actor="system")
        if g.use_model:
            judged, leftover = await self._guard_with_model(item, spans, g)
        else:
            # 規則だけで判定する。氏名などの候補は確かめられないので、見逃さないよう個人情報として扱う
            judged = [s.model_copy(update={"confirmed": True}) for s in spans]
            leftover = None
            found = [s for s in judged if s.confirmed]
            self.store.add_event(
                item.id,
                "guard",
                "system",
                f"規則だけで判定（Kev は使わない）: 候補 {len(spans)} 件をすべて個人情報として扱う"
                f"（{_types_text(found)}）／ 候補外の確認は行わない",
                {"candidates": [s.model_dump() for s in judged]},
            )
            item = self.store.update(item.id, lambda i: i.model_copy(update={"pii": judged, "pii_leftover": None}))
        found = [s for s in judged if s.confirmed]
        suspicious = leftover is not None and leftover >= g.leftover_threshold
        # 規則が拾ったのにモデルが個人情報と言い切らなかった候補（氏名など）も、見逃しを避けるため人に見せる
        unsure = [s for s in judged if not s.confirmed]
        if g.human_check and (found or suspicious or unsure):
            reason = (
                "個人情報を検出"
                if found
                else "個人情報か判断のつかない候補あり"
                if unsure
                else "候補外の個人情報が残っている可能性"
            )
            self.store.add_event(item.id, "pii_review", "system", f"{reason}のため、人の確認待ちにしました")
            self.store.update(item.id, lambda i: i.model_copy(update={"status": "pii_review", "reason": reason}))
            return None
        return self._apply_pii(item, judged, settings, force_block=False, actor="system")

    async def check_pii(self, text: str) -> tuple[list[Span], target.Target | None]:
        """ツール向けのローカルの個人情報チェック。件は作らず、Jev にも送らない。

        規則で候補を拾い、設定で Kev を使うときだけ、氏名などの候補を Kev に聞く。
        Kev に届かないときは規則だけで返す（候補はすべて個人情報として扱う）。
        戻り値は（候補、聞いた接続先。規則だけなら None）。
        """
        g = self.store.settings().guard
        spans = detect(text)
        ask = [i for i, s in enumerate(spans) if s.type not in STRUCTURED]
        rules_only = [s.model_copy(update={"confirmed": True}) for s in spans]
        # 接続先は Kev か MOCK に限られる（GuardSettings.target）。Jev には送らない
        if not (g.use_model and ask):
            return rules_only, None
        try:
            views, _, _, _ = await self._ask(
                g.target, "tools-pii-check", oq.guard_state(text, spans), oq.guard_questions(spans, ask)
            )
        except (TargetConnectionError, TargetUnavailableError) as e:
            log.warning("個人情報チェックで Kev に届かないため規則だけで判定します: %s", e)
            return rules_only, None

        def judged(i: int, s: Span) -> Span:
            if i not in ask:
                return s.model_copy(update={"confirmed": True})
            score = views[oq.candidate_id(i)].value or 0.0
            return s.model_copy(update={"score": score, "confirmed": score >= g.candidate_threshold})

        return [judged(i, s) for i, s in enumerate(spans)], g.target

    async def _guard_with_model(self, item: Item, spans: list[Span], g: GuardSettings) -> tuple[list[Span], float]:
        """形で決まらない候補（氏名など）と、候補外の残りを Kev に聞く。"""
        ask = [i for i, s in enumerate(spans) if s.type not in STRUCTURED]
        views, latency, cost, model = await self._ask(
            g.target, "ops-guard", oq.guard_state(item.text, spans), oq.guard_questions(spans, ask)
        )

        def judged_span(i: int, s: Span) -> Span:
            if i not in ask:
                return s.model_copy(update={"confirmed": True})
            score = views[oq.candidate_id(i)].value or 0.0
            return s.model_copy(update={"score": score, "confirmed": score >= g.candidate_threshold})

        judged = [judged_span(i, s) for i, s in enumerate(spans)]
        leftover = views[oq.LEFTOVER_ID].value or 0.0
        found = [s for s in judged if s.confirmed]
        self.store.add_event(
            item.id,
            "guard",
            actor_of(g.target),
            f"候補 {len(spans)} 件（規則で確定 {len(spans) - len(ask)}・モデルが判定 {len(ask)}）→ 個人情報 {len(found)} 件"
            f"（{_types_text(found)}）／ 候補外に残っている可能性 {leftover:.2f}",
            {"model": model, "latency_ms": round(latency, 1), "candidates": [s.model_dump() for s in judged]},
        )
        self.store.update(
            item.id,
            lambda i: i.model_copy(update={"pii": judged, "pii_leftover": leftover, "cost_usd": i.cost_usd + cost}),
        )
        return judged, leftover

    def _apply_pii(self, item: Item, spans: list[Span], settings: Settings, *, force_block: bool, actor: Actor) -> Item:
        policy = settings.guard.policy
        blocked = blocked_types(spans, policy)
        found = [s for s in spans if s.confirmed]
        if force_block or blocked:
            why = (
                "人の判断でブロック" if force_block else f"{'・'.join(PII_LABELS[t] for t in blocked)}はブロックの方針"
            )
            self.store.add_event(item.id, "guard", actor, f"外部（Jev）には送りません: {why}")
            return self.store.update(
                item.id,
                lambda i: i.model_copy(
                    update={"pii": spans, "pii_decision": "blocked", "sent_text": None, "reason": why}
                ),
            )
        masked = apply_mask(item.text, spans, policy)
        decision = "masked" if masked != item.text else ("allowed" if found else "none")
        message = {
            "masked": f"マスクして仕分け（{_types_text([s for s in found if policy.get(s.type) == 'mask'])}）",
            "allowed": "そのまま仕分け（個人情報は検出のみの方針）",
            "none": "そのまま仕分け（個人情報なし）",
        }[decision]
        self.store.add_event(item.id, "guard", actor, message)
        return self.store.update(
            item.id, lambda i: i.model_copy(update={"pii": spans, "pii_decision": decision, "sent_text": masked})
        )

    async def _handle_blocked(self, item: Item, settings: Settings) -> None:
        if settings.guard.blocked_route == "kev":
            # Kev はローカルで動くため、元の本文のまま判定してよい
            await self._classify_and_route(item, settings, force_target="custom", text=item.text)
            return
        reason = item.reason or "個人情報のため外部に送れない"
        self.store.update(
            item.id,
            lambda i: i.model_copy(
                update={
                    "status": "escalated",
                    "reason": reason,
                    "first_route": "escalated",
                }
            ),
        )
        self.store.add_event(item.id, "escalate", "system", "外部に送れないため、人に回しました")
        # 人への知らせ（Slack への転送もここから）を先に出す。参考の判定は Kev が遅い・落ちていても待たせない
        self.store.add_post(
            ESCALATION_CHANNEL,
            "jevlab",
            f"{item.id}「{safe_title(item)}」\n理由: 個人情報（Jev に送らない）",
            item.id,
        )
        await self._kev_reference(item, settings)

    async def _kev_reference(self, item: Item, settings: Settings) -> None:
        """ブロックした件を、ローカルの Kev で参考に仕分ける（元の本文を読んでよいのはローカルだから）。

        結果は人が仕分けるときの手がかりとして残すだけで、自動の振り分け・割り当てには使わない。
        Jev・Slack には何も送らない。
        """
        g = settings.guard
        if not g.use_model:
            self.store.add_event(item.id, "classify", "system", "参考の判定はしません（Kev で判定しない設定）")
            return
        text = item.text
        questions = oq.classify_questions(oq.field_candidates(text), settings.active_categories())
        assignee_q = oq.assignee_question(settings.on_duty(), self._assign_examples(settings))
        if assignee_q is not None:
            questions = {**questions, oq.ASSIGNEE_ID: assignee_q}
        try:
            views, latency, cost, model = await self._ask(
                g.target, "ops-kev-reference", oq.classify_state(text), questions
            )
        except Exception as e:  # Kev が止まっていても、人に回すことは変わらない
            log.exception("件 %s の参考の判定に失敗しました", item.id)
            self.store.add_event(
                item.id,
                "classify",
                "system",
                f"参考の判定はしません（{e if isinstance(e, TargetConnectionError) else type(e).__name__}）",
            )
            return
        category = views["category"]
        suggestion, probability = self._suggestion(views, settings)
        ref = KevReference(
            category=str(category.prediction),
            confidence=category.confidence,
            assign_suggestion=suggestion,
            assign_probability=probability,
            model=model,
        )
        self.store.update(item.id, lambda i: i.model_copy(update={"kev_reference": ref, "cost_usd": i.cost_usd + cost}))
        self.store.add_event(
            item.id,
            "classify",
            actor_of(g.target),
            f"参考（{TARGET_NAMES[g.target]}）: {category_label(ref.category, settings)}（{ref.confidence or 0:.2f}）／ 担当の推定: "
            f"{self.staff_name(suggestion, settings) if suggestion else 'なし'}。自動では振り分けません",
            {"latency_ms": round(latency, 1)},
        )

    async def _classify_and_route(
        self, item: Item, settings: Settings, *, force_target: target.Target | None = None, text: str | None = None
    ) -> None:
        if text is None and item.sent_text is None:
            raise RuntimeError(f"{item.id} は外部に送れる本文がありません（個人情報の処理が済んでいません）")
        body = text if text is not None else item.sent_text or ""
        candidates = oq.field_candidates(body)
        state = oq.classify_state(body)
        questions = oq.classify_questions(candidates, settings.active_categories())
        # 担当者の推定も同じ問い合わせで聞いておく（エスカレーションになったときだけ使う）
        assignee_q = oq.assignee_question(settings.on_duty(), self._assign_examples(settings))
        if assignee_q is not None:
            questions = {**questions, oq.ASSIGNEE_ID: assignee_q}
        chosen = force_target or settings.classify.target
        kf = settings.kev_first
        result: tuple[dict[str, AnswerView], float, float, str] | None = None
        decided: target.Target = chosen
        total_cost = 0.0
        if force_target is None and kf.enabled and chosen != "custom":
            views, latency, cost, model = await self._ask("custom", "ops-classify", state, questions)
            total_cost += cost
            conf = views["category"].confidence or 0.0
            if conf >= kf.threshold:
                result, decided = (views, latency, cost, model), "custom"
                self.store.add_event(
                    item.id,
                    "classify",
                    "kev",
                    f"Kev で確定: {category_label(str(views['category'].prediction), settings)}（{conf:.2f} ≥ {kf.threshold:.2f}）。{TARGET_NAMES[chosen]} は呼びません",
                    {"latency_ms": round(latency, 1)},
                )
            else:
                self.store.add_event(
                    item.id,
                    "classify",
                    "kev",
                    f"Kev の確信度 {conf:.2f} < {kf.threshold:.2f} のため {TARGET_NAMES[chosen]} に回します",
                    {"latency_ms": round(latency, 1)},
                )
        if result is None:
            result = await self._ask(chosen, "ops-classify", state, questions)
            total_cost += result[2]
        views, latency, _, model = result
        category = str(views["category"].prediction)
        suggestion, assign_conf = self._suggestion(views, settings)
        fields = {
            f.id: oq.picked_value(candidates, f.id, str(views[f.id].prediction)) if f.id in views else None
            for f in oq.FIELDS
        }
        self.store.add_event(
            item.id,
            "classify",
            actor_of(decided),
            f"{TARGET_NAMES[decided]} が仕分け: {category_label(category, settings)}（確信度 {views['category'].confidence or 0:.2f}）"
            + "".join(f" ／ {oq.FIELD_TITLES[k]} {v}" for k, v in fields.items() if v),
            {"model": model, "latency_ms": round(latency, 1), "candidates": candidates},
        )
        item = self.store.update(
            item.id,
            lambda i: i.model_copy(
                update={
                    "answers": views,
                    "category": category,
                    "category_version": settings.categories_version(),
                    "confidence": views["category"].confidence,
                    "decided_by": decider_of(decided),
                    "fields": fields,
                    "cost_usd": i.cost_usd + total_cost,
                    "assign_suggestion": suggestion,
                    "assign_confidence": assign_conf,
                }
            ),
        )
        self._route(item, settings)

    def _close_backfill(self, item: Item, route: Route | None, reason: str) -> None:
        """過去の問い合わせ（試算用）を、振り分けの結果だけ残して完了にする。投稿・割り当ては行わない。"""
        self.store.update(
            item.id,
            lambda i: i.model_copy(
                update={"status": "closed", "first_route": route, "reason": reason, "closed_at": now_iso()}
            ),
        )
        self.store.add_event(item.id, "close", "system", f"試算: {reason}")

    def _route(self, item: Item, settings: Settings) -> None:
        route, reason = decide_route(item, settings)
        if item.backfill:
            label = {"routed": "自動で振り分け", "review": "分類の確認", "escalated": "エスカレーション"}[route]
            self._close_backfill(item, route, f"{label}になる（{reason}）")
            return
        audit = route == "routed" and _stable_unit(f"audit:{item.id}") < settings.audit_rate
        self.store.update(
            item.id,
            lambda i: i.model_copy(update={"status": route, "reason": reason, "audit": audit, "first_route": route}),
        )
        match route:
            case "routed":
                self.store.add_event(
                    item.id,
                    "route",
                    "system",
                    f"自動で {settings.route_channel(item.category)} へ（{reason}）",
                )
                no_reply = no_reply_reason(item, settings)
                closes = no_reply is not None and not audit
                if no_reply is None:
                    # 返信の要る件は、担当を割り当てて（届かなければ仮で）Slack でメンションする
                    self._auto_assign(item, settings)
                self._post_routed(item, by="jevlab（自動）")
                if audit:
                    self.store.add_event(
                        item.id, "audit", "system", f"抜き取り確認の対象に選ばれました（{settings.audit_rate:.0%}）"
                    )
                elif no_reply is not None and closes:
                    # 抜き取り確認に選ばれた件は、人が見るまで完了にしない
                    self._auto_close(item, no_reply)
            case "review":
                self.store.add_event(item.id, "review", "system", f"確認待ちへ（{reason}）")
            case "escalated":
                self.store.add_event(item.id, "escalate", "system", f"エスカレーション（{reason}）")
                self._auto_assign(item, settings)
                self.store.add_post(
                    ESCALATION_CHANNEL,
                    "jevlab",
                    f"{item.id}「{safe_title(item)}」\n分類: {category_label(item.category, settings)}\n理由: {reason}",
                    item.id,
                    item.fields,
                )

    def _auto_close(self, item: Item, reason: str) -> None:
        """返信のいらない件（お礼など）を、振り分けたまま完了にする。担当は割り当てない。"""
        self.store.update(
            item.id,
            lambda i: i.model_copy(update={"status": "closed", "auto_closed": True, "closed_at": now_iso()}),
        )
        # auto: Slack では担当を呼ばず、スレッドに完了を書いて ✅ を付ける
        self.store.add_event(item.id, "close", "system", f"{reason}のため自動で対応完了", {"auto": True})

    # ---- 担当者の推定と割り当て ----

    def _assign_examples(self, settings: Settings) -> dict[str, list[str]]:
        """担当者ごとの最近の対応例（対応完了した件の見出し。個人情報は伏せたもの）。

        例は Jev に送るため、個人情報の確認を通って送ってよいと判断済みの件だけを使う
        （ブロックした件・ガードを通していない件は使わない）。
        自動で割り当てたままの件は推定の答えそのものなので、人が割り当てた件だけを使う。
        """
        a = settings.assign
        if not a.use_examples or a.max_examples == 0:
            return {}
        out: dict[str, list[str]] = {}
        for item in self.store.items(["closed"], limit=300):
            if item.pii_decision not in EXAMPLE_DECISIONS or item.assigned_by != "human":
                continue
            if item.assignee and len(out.setdefault(item.assignee, [])) < a.max_examples:
                out[item.assignee].append(safe_title(item, limit=24))
        return out

    @staticmethod
    def _suggestion(views: Mapping[str, AnswerView], settings: Settings) -> tuple[str | None, float | None]:
        view = views.get(oq.ASSIGNEE_ID)
        if view is None:
            return None, None
        known = [s.id for s in settings.on_duty()]
        # 確信度は選択肢の数（担当者の人数）で意味が変わるため、推定した担当の確率で判断する。
        # 「該当なし」が一番でも、担当者のうち確率が一番高い人を推定にする（仮で割り当てるため）
        scored = [(k, view.probabilities[k]) for k in known if k in view.probabilities]
        if scored:
            return max(scored, key=lambda kp: kp[1])
        # 確率がないときは、答えの担当者（1 人だけならその人）を確率なしで返す
        key = str(view.prediction)
        if key in known:
            return key, None
        return (known[0], None) if len(known) == 1 else (None, None)

    def staff_name(self, staff_id: str | None, settings: Settings | None = None) -> str:
        if staff_id is None:
            return "未割り当て"
        staff = (settings or self.store.settings()).staff
        member = next((s for s in staff if s.id == staff_id), None)
        return member.name if member else staff_id

    def _auto_assign(self, item: Item, settings: Settings) -> None:
        # 仕分けを待っている間に担当者が変わっていることがあるので、最新の設定で確かめる
        settings = self.store.settings()
        a = settings.assign
        if item.assign_suggestion is not None and item.assign_suggestion not in {s.id for s in settings.on_duty()}:
            item = self.store.update(item.id, lambda i: i.model_copy(update={"assign_suggestion": None}))
        name = self.staff_name(item.assign_suggestion, settings)
        conf = item.assign_confidence or 0.0
        if not settings.on_duty():
            self.store.add_event(item.id, "assign", "system", "担当中の担当者がいないため、担当は推定していません")
        elif item.assign_suggestion is None:
            self.store.add_event(
                item.id, "assign", "system", "担当の推定: 該当する担当者なし（手動で割り当ててください）"
            )
        elif a.auto:
            # 閾値に届かなくても、誰にも割り当てないと誰も手を付けないので、一番確率の高い人に仮で割り当てる
            provisional = conf < a.threshold
            self.store.update(
                item.id,
                lambda i: i.model_copy(
                    update={
                        "assignee": i.assign_suggestion,
                        "assigned_by": "auto",
                        "auto_assigned": True,
                        "assign_provisional": provisional,
                    }
                ),
            )
            how = (
                f"仮で割り当て: {name}（確率 {conf:.2f} < {a.threshold:.2f}）"
                if provisional
                else (f"自動で割り当て: {name}（確率 {conf:.2f} ≥ {a.threshold:.2f}）")
            )
            self.store.add_event(
                item.id,
                "assign",
                "system",
                f"担当を{how}",
                {"suggestion": item.assign_suggestion, "confidence": conf, "provisional": provisional},
            )
        else:
            self.store.add_event(
                item.id,
                "assign",
                "system",
                f"担当の推定: {name}（確率 {conf:.2f}）。人が決めます",
                {"suggestion": item.assign_suggestion, "confidence": conf},
            )

    def _post_routed(self, item: Item, *, by: str) -> None:
        channel = self.store.settings().route_channel(item.category)
        # 差出人の名前は個人情報なので投稿に載せない（受付箱の詳細で確認する）
        self.store.add_post(channel, by, f"{item.id}「{safe_title(item)}」", item.id, item.fields)

    # ---- 人の操作 ----

    def submit_pii(
        self, item_id: str, spans: list[Span], action: Literal["continue", "block"], *, bulk: bool = False
    ) -> Item:
        item = self.store.get(item_id)
        if item.status != "pii_review":
            raise ValueError(f"{item_id} は個人情報の確認待ちではありません（状態: {item.status}）")
        submitted = [validate_span(item.text, s) for s in spans]
        # 形で決まる個人情報（規則で確定したもの）は、画面から外されても戻す（送られた内容をそのまま信じない）
        kept = [
            s
            for s in item.pii
            if s.confirmed
            and s.type in STRUCTURED
            and not any(x.start == s.start and x.end == s.end and x.confirmed for x in submitted)
        ]
        checked = [x for x in submitted if not any(k.start == x.start and k.end == x.end for k in kept)] + kept
        added = [s for s in checked if s.source == "human"]
        removed = sum(1 for s in item.pii if s.confirmed) - sum(
            1 for s in checked if s.confirmed and s.source == "rule"
        )
        self.store.add_event(
            item_id,
            "pii_review",
            "human",
            f"{'一括で確認' if bulk else '人が確認'}: 追加 {len(added)} 件・除外 {max(removed, 0)} 件"
            + (" → 人の判断でブロック" if action == "block" else "")
            + (f"（規則で確定した {len(kept)} 件は外せないため戻しました）" if kept else ""),
            # reviewed: 人が本文を見て判断したか（一括で編集なしに流した件は、候補をすべて個人情報とみなしただけ）
            {"added": [s.model_dump() for s in added], "reviewed": not bulk or item.pii_draft is not None},
        )
        updated = self._apply_pii(item, checked, self.store.settings(), force_block=action == "block", actor="human")
        # 仕分けはワーカーに任せる（Jev の呼び出しを画面の操作から切り離す）
        updated = self.store.update(item_id, lambda i: i.model_copy(update={"status": "queued", "pii_draft": None}))
        self.wake.set()
        return updated

    def save_pii_draft(self, item_id: str, spans: list[Span] | None) -> Item:
        """個人情報の確認で編集中の内容を保存する（None で取り消し）。確定はしない。"""
        item = self.store.get(item_id)
        if item.status != "pii_review":
            raise ValueError(f"{item_id} は個人情報の確認待ちではありません（状態: {item.status}）")
        checked = None if spans is None else [validate_span(item.text, s) for s in spans]
        return self.store.update(item_id, lambda i: i.model_copy(update={"pii_draft": checked}))

    def report_miss(self, item_id: str, type_: PiiType, start: int, end: int) -> MissReport:
        """Jev に送った後で見つかった個人情報（ガードレールの見逃し）を記録する。送信は取り消せない。"""
        item = self.store.get(item_id)
        if item.status not in MISS_STATUSES or item.sent_text is None:
            raise ValueError(f"{item_id} は Jev に送った件ではありません（状態: {item.status}）")
        if not 0 <= start < end <= len(item.text):
            raise ValueError(f"範囲 {start}〜{end} が本文（{len(item.text)} 文字）の外です")
        overlaps = [s for s in item.pii if s.confirmed and s.start < end and start < s.end]
        if any(s.source == "human" and (s.start, s.end) == (start, end) for s in overlaps):
            raise PermissionError("この範囲はすでに報告済みです")
        if overlaps:
            raise ValueError("この範囲は個人情報として検知済みです")
        miss = self.store.add_miss(item_id, type_, start, end, item.pii_leftover)
        # 以後の見出し（Slack への投稿・担当の推定の例として Jev に送る分）で伏せるよう、確定した個人情報に加える。
        # 重なる未確定の候補は、人の報告で置き換える
        span = Span(start=start, end=end, type=type_, text=item.text[start:end], source="human", confirmed=True)

        def add_span(i: Item) -> Item:
            kept = [s for s in i.pii if s.end <= start or end <= s.start]
            return i.model_copy(update={"pii": sorted([*kept, span], key=lambda s: s.start)})

        self.store.update(item_id, add_span)
        self.store.add_event(
            item_id,
            "miss",
            "human",
            f"検知漏れの報告: {PII_LABELS[type_]}（{miss.length} 文字）",
            miss.model_dump(include={"type", "start", "end", "length", "leftover"}),
        )
        return miss

    def decide(self, item_id: str, category: str, *, note: str | None = None) -> Item:
        """確認待ち・抜き取りの件を人が確定する。"""
        settings = self.store.settings()
        active = [c.key for c in settings.active_categories()]
        if category not in active:
            raise ValueError(f"分類 {category!r} は使えません（{' / '.join(active)}）")
        item = self.store.get(item_id)
        if item.status == "routed" and not (item.audit and item.audit_result is None):
            raise ValueError(f"{item_id} は振り分け済みで、抜き取り確認の対象でもありません")
        if item.status not in ("review", "routed"):
            raise ValueError(f"{item_id} は確認できる状態ではありません（状態: {item.status}）")
        fixed = category != item.category
        audit_result = ("fixed" if fixed else "ok") if item.audit else None
        updated = self.store.update(
            item_id,
            lambda i: i.model_copy(
                update={
                    "category": category,
                    "decided_by": "human" if fixed else i.decided_by,
                    "status": "routed",
                    "reason": "人が確認して確定",
                    "audit_result": audit_result,
                    "notes": [*i.notes, note] if note else i.notes,
                }
            ),
        )
        verb = (
            f"{category_label(item.category, settings)} → {category_label(category, settings)} に修正"
            if fixed
            else f"{category_label(category, settings)} で承認"
        )
        kind = "audit" if item.audit and item.status == "routed" else "review"
        self.store.add_event(item_id, kind, "human", f"人が確認: {verb}", {"before": item.category, "after": category})
        no_reply = no_reply_reason(updated, settings)
        if item.status == "review" and updated.assignee is None and no_reply is None:
            # 確認で振り分けた件も、返信の要る件なら担当を割り当ててメンションする
            self._auto_assign(updated, settings)
            updated = self.store.get(item_id)
        if item.status == "review" or fixed:
            self._post_routed(updated, by="担当者（確認済み）")
        if no_reply is not None:
            # 返信のいらない件（お礼など）は、確認・抜き取りで確定したら、投稿して完了にする
            self._auto_close(updated, no_reply)
            updated = self.store.get(item_id)
        return updated

    def assign(self, item_id: str, assignee: str, *, bulk: bool = False) -> Item:
        settings = self.store.settings()
        if assignee and assignee not in {s.id for s in settings.staff}:
            raise ValueError(f"担当者 {assignee!r} は登録されていません")
        if assignee and assignee not in {s.id for s in settings.on_duty()}:
            raise ValueError(f"{self.staff_name(assignee, settings)} は担当がオフです")
        item = self.store.get(item_id)
        if item.status not in ("escalated", "routed"):
            raise ValueError(f"{item_id} はエスカレーション中でも振り分け済みでもありません（状態: {item.status}）")
        updated = self.store.update(
            item_id,
            lambda i: i.model_copy(
                update={
                    "assignee": assignee or None,
                    "assigned_by": "human" if assignee else None,
                    "assign_provisional": False,
                }
            ),
        )
        how = ("一括で担当を外す" if not assignee else "一括で割り当て") if bulk else "担当者"
        self.store.add_event(
            item_id,
            "assign",
            "human",
            f"{how}: {self.staff_name(assignee or None, settings)}",
            {"before": item.assignee, "after": assignee or None},
        )
        return updated

    def add_note(self, item_id: str, note: str) -> Item:
        item = self.store.update(item_id, lambda i: i.model_copy(update={"notes": [*i.notes, note]}))
        self.store.add_event(item_id, "note", "human", f"メモ: {note}")
        return item

    def reopen(self, item_id: str) -> Item:
        """自動で完了にした件（お礼など）を、振り分け済みに戻す。分類の修正は完了にするときに行う。"""
        item = self.store.get(item_id)
        if item.status != "closed" or not item.auto_closed:
            raise ValueError(f"{item_id} は自動で完了にした件ではありません（状態: {item.status}）")
        updated = self.store.update(
            item_id,
            lambda i: i.model_copy(update={"status": "routed", "auto_closed": False, "closed_at": None}),
        )
        self.store.add_event(item_id, "reopen", "human", "自動の完了を取り消して、振り分け済みに戻す")
        return updated

    def close(self, item_id: str, category: str | None) -> Item:
        """エスカレーション・振り分け済みの件を、人が対応して完了にする。

        最終の分類を変えなければ「分類は合っていた」、変えれば「修正した」として記録する（閾値の調整の正解になる）。
        """
        item = self.store.get(item_id)
        if item.status not in ("escalated", "routed"):
            raise ValueError(f"{item_id} はエスカレーション中でも振り分け済みでもありません（状態: {item.status}）")
        settings = self.store.settings()
        if category is not None and category not in {c.key for c in settings.active_categories()}:
            raise ValueError(f"分類 {category!r} は使えません")
        final = category or item.category
        fixed = final is not None and final != item.category
        # 抜き取り確認を待っている件は、完了の判断を抜き取りの結果にもする
        audit_result = ("fixed" if fixed else "ok") if item.audit and item.audit_result is None else item.audit_result
        updated = self.store.update(
            item_id,
            lambda i: i.model_copy(
                update={
                    "status": "closed",
                    "category": final,
                    "decided_by": "human" if fixed or i.category is None else i.decided_by,
                    "audit_result": audit_result,
                    "closed_at": now_iso(),
                }
            ),
        )
        # エスカレーションの完了は、エスカレーションのチャンネルにだけ知らせる（Slack では元の投稿のスレッド）。
        # 分類のチャンネルには元の投稿がないため、完了だけ届いても何の件か分からず、通知が増えるだけになる。
        # 振り分け済みの件は、そのチャンネルに投稿済みなので、分類を直したときだけ新しいチャンネルに投稿する
        if item.status == "escalated" or fixed:
            channel = ESCALATION_CHANNEL if item.status == "escalated" else settings.route_channel(final)
            lines = [f"{item_id}「{safe_title(updated)}」", "対応完了"]
            if updated.assignee:
                lines.append(f"担当: {self.staff_name(updated.assignee)}")
            if fixed:
                lines.append(
                    f"分類を {category_label(item.category, settings)} から {category_label(final, settings)} に修正"
                )
            lines.extend(notes_lines(updated))
            self.store.add_post(channel, CLOSE_AUTHOR, "\n".join(lines), item_id, updated.fields)
        self.store.add_event(
            item_id,
            "close",
            "human",
            f"対応完了（{category_label(final, settings)}）"
            + (f"。分類を {category_label(item.category, settings)} から修正" if fixed and item.category else ""),
            {
                "before": item.category,
                "after": final,
                "assign_suggestion": item.assign_suggestion,
                "assignee": item.assignee,
            },
        )
        return updated

    def retry(self, item_id: str) -> Item:
        item = self.store.get(item_id)
        if item.status != "error":
            raise ValueError(f"{item_id} はエラーではありません（状態: {item.status}）")
        updated = self.store.update(item_id, lambda i: i.model_copy(update={"status": "queued", "error": None}))
        self.store.add_event(item_id, "retry", "human", "再実行")
        self.wake.set()
        return updated


# ---- 常駐処理 ----


def capacity(pipeline: Pipeline) -> int:
    """同時に処理する件の数。受信シミュレータが高速のときは増やす。"""
    return FAST_WORKERS if pipeline.store.settings().simulator.fast else WORKERS


async def run_worker(pipeline: Pipeline) -> None:
    """処理待ちの件を順に取り出して処理する。起動時に処理中のまま残った件は処理待ちに戻す。"""
    recovered = pipeline.store.recover()
    if recovered:
        log.info("処理中のまま残っていた %d 件を処理待ちに戻しました", recovered)
    running: set[asyncio.Task[None]] = set()

    async def idle() -> None:
        pipeline.wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(pipeline.wake.wait(), timeout=1.0)

    def done(task: asyncio.Task[None]) -> None:
        running.discard(task)
        # 空きができたので、待っている件を取りに行く
        pipeline.wake.set()
        if not task.cancelled() and task.exception() is not None:
            # process の中でも記録できなかった失敗（保存先のエラーなど）。件は次回起動時に処理待ちへ戻る
            log.error("件の処理が異常終了しました", exc_info=task.exception())

    try:
        while True:
            try:
                full = len(running) >= capacity(pipeline)
                item = None if full else pipeline.store.claim_next()
            except Exception:
                log.exception("処理待ちの件を取り出せませんでした。1 秒後に再試行します")
                await asyncio.sleep(1.0)
                continue
            if item is None:
                await idle()
                continue
            task = asyncio.create_task(pipeline.process(item))
            running.add(task)
            task.add_done_callback(done)
    finally:
        # 停止時は処理中の件も止める（保存先を閉じた後に書き込まないように）。止めた件は次回起動時に処理待ちへ戻る
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
