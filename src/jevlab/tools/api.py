"""ツール（言い方チェック）の API。判定は選んだ接続先（?target=）、書き換え案は生成器（Claude）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints
from typesafe_sdk import TypeSafeError

from jevlab.core import target
from jevlab.core.budget import BudgetExceededError
from jevlab.core.client import judge
from jevlab.core.engine import answer_views
from jevlab.core.generator import (
    CLAUDE_MODELS,
    ClaudeModel,
    GeneratedText,
    GenerateRequest,
    GenerationError,
    make_generator,
    make_stream_generator,
)
from jevlab.ops.pii import PII_LABELS, PII_TYPES, Span, apply_mask
from jevlab.ops.pipeline import TARGET_NAMES, BackendLike, Pipeline
from jevlab.ops.store import ItemNotFoundError
from jevlab.tools import contract, reply, tone

router = APIRouter(prefix="/api/tools", tags=["tools"])
# claude -p は重いので同時に走らせる数を絞る（サブスクの枠も守る）
REWRITE_SLOTS: Final = 2
DEFAULT_MODEL: Final = "sonnet"


def _backend(
    request: Request,
    selected: Annotated[target.Target | None, Query(alias="target", description="接続先。省略時は既定")] = None,
) -> BackendLike:
    t = selected or request.app.state.default_target
    backend: BackendLike | None = request.app.state.backends.get(t)
    if backend is None:
        raise HTTPException(status_code=400, detail=f"接続先 {t!r} は使えません（TYPESAFE_API_KEY が未設定）")
    return backend


class ToneMeta(BaseModel):
    recipients: dict[str, str]
    mediums: dict[str, str]
    purposes: dict[str, str]
    groups: dict[str, str]
    impressions: dict[str, str]
    models: list[str]
    default_model: str
    max_sentences: int


@router.get("/tone/meta")
async def tone_meta() -> ToneMeta:
    return ToneMeta(
        recipients=dict(tone.RECIPIENT_LABELS),
        mediums=dict(tone.MEDIUM_LABELS),
        purposes=dict(tone.PURPOSE_LABELS),
        groups=dict(tone.GROUP_LABELS),
        impressions=dict(tone.IMPRESSION_LABELS),
        models=list(CLAUDE_MODELS),
        default_model=DEFAULT_MODEL,
        max_sentences=tone.MAX_SENTENCES,
    )


class ToneRequest(BaseModel):
    text: tone.NonBlank
    recipient: tone.Recipient = "none"
    medium: tone.Medium = "none"


@router.post("/tone")
async def check_tone(req: ToneRequest, backend: Annotated[BackendLike, Depends(_backend)]) -> tone.ToneResult:
    all_sentences = tone.split_sentences(req.text)
    sentences = all_sentences[: tone.MAX_SENTENCES]
    questions = tone.questions(sentences)
    try:
        judged = await judge(
            backend.client,
            backend.ledger,
            app=tone.APP_NAME,
            state=tone.state(req.text, req.recipient, req.medium, sentences),
            questions=questions,
        )
    except BudgetExceededError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TypeSafeError as e:
        raise HTTPException(status_code=502, detail=f"判定に失敗しました: {type(e).__name__}: {e}") from e
    try:
        return tone.build_result(
            answer_views(questions, judged.response),
            sentences,
            truncated=len(all_sentences) > len(sentences),
            model=judged.response.model,
            latency_ms=judged.latency_ms,
            cost_usd=judged.cost_usd,
        )
    except (KeyError, ValueError) as e:
        # 接続先が想定外の目的ラベルを返した、回答が欠けている、など
        raise HTTPException(status_code=502, detail=f"判定の応答が想定と違います: {type(e).__name__}: {e}") from e


def _strings(v: object) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


@router.post("/tone/rewrite")
async def rewrite(req: tone.RewriteRequest, request: Request) -> tone.RewriteResult:
    slots: asyncio.Semaphore = request.app.state.rewrite_slots
    try:
        generator = make_generator(req.model)
        async with slots:
            out = await generator.generate(
                GenerateRequest(system=tone.REWRITE_SYSTEM, prompt=tone.rewrite_prompt(req), schema=tone.REWRITE_SCHEMA)
            )
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=f"書き換え案を作れませんでした: {e}") from e
    data = out.structured
    if data is None or not isinstance(data.get("rewritten"), str) or not data["rewritten"].strip():
        raise HTTPException(status_code=502, detail=f"書き換え案の形が想定と違います: {out.text[:200]}")
    return tone.RewriteResult(
        rewritten=str(data["rewritten"]).strip(),
        changes=_strings(data.get("changes")),
        placeholders=_strings(data.get("placeholders")),
        model=out.model,
        latency_ms=out.latency_ms,
        reported_cost_usd=out.reported_cost_usd,
    )


# ---- 契約・規約チェック ----


class ContractMeta(BaseModel):
    flags: dict[str, str]
    risks: dict[str, str]
    max_clauses: int
    max_chars: int
    disclaimer: str
    models: list[str]
    default_model: str


@router.get("/contract/meta")
async def contract_meta() -> ContractMeta:
    return ContractMeta(
        flags=dict(contract.FLAG_LABELS),
        risks=dict(contract.RISK_LABELS),
        max_clauses=contract.MAX_CLAUSES,
        max_chars=contract.MAX_CHARS,
        disclaimer=contract.DISCLAIMER,
        models=list(CLAUDE_MODELS),
        default_model=DEFAULT_MODEL,
    )


class ContractRequest(BaseModel):
    text: contract.Text


@router.post("/contract")
async def check_contract(
    req: ContractRequest, backend: Annotated[BackendLike, Depends(_backend)]
) -> contract.ContractResult:
    all_clauses = [c[: contract.MAX_CLAUSE_CHARS] for c in contract.split_clauses(req.text)]
    clauses = all_clauses[: contract.MAX_CLAUSES]
    if not clauses:
        raise HTTPException(status_code=422, detail="条項が見つかりません")
    questions = contract.questions(clauses)
    try:
        judged = await judge(
            backend.client, backend.ledger, app=contract.APP_NAME, state=contract.state(clauses), questions=questions
        )
    except BudgetExceededError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TypeSafeError as e:
        raise HTTPException(status_code=502, detail=f"判定に失敗しました: {type(e).__name__}: {e}") from e
    try:
        return contract.build_result(
            answer_views(questions, judged.response),
            clauses,
            truncated=len(all_clauses) > len(clauses),
            model=judged.response.model,
            latency_ms=judged.latency_ms,
            cost_usd=judged.cost_usd,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"判定の応答が想定と違います: {type(e).__name__}: {e}") from e


@router.post("/contract/explain")
async def explain_contract(req: contract.ExplainRequest, request: Request) -> contract.ExplainResult:
    slots: asyncio.Semaphore = request.app.state.rewrite_slots
    try:
        generator = make_generator(req.model)
        async with slots:
            out = await generator.generate(
                GenerateRequest(
                    system=contract.EXPLAIN_SYSTEM, prompt=contract.explain_prompt(req), schema=contract.EXPLAIN_SCHEMA
                )
            )
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=f"説明を作れませんでした: {e}") from e
    data = out.structured
    raw = data.get("items") if data is not None else None
    if not isinstance(raw, list):
        raise HTTPException(status_code=502, detail=f"説明の形が想定と違います: {out.text[:200]}")
    known = {c.index for c in req.clauses}
    items = [
        contract.Explanation(
            index=int(x["index"]), summary=str(x.get("summary", "")).strip(), ask=_strings(x.get("ask"))
        )
        for x in raw
        if isinstance(x, dict) and isinstance(x.get("index"), int) and x["index"] in known
    ]
    return contract.ExplainResult(
        items=items, model=out.model, latency_ms=out.latency_ms, reported_cost_usd=out.reported_cost_usd
    )


# ---- 返信前チェック ----


class ReplyMeta(BaseModel):
    default_policy: str
    missing: dict[str, str]
    max_chars: int
    models: list[str]
    default_model: str


@router.get("/reply/meta")
async def reply_meta() -> ReplyMeta:
    return ReplyMeta(
        default_policy=reply.DEFAULT_POLICY,
        missing=dict(reply.MISSING),
        max_chars=reply.MAX_CHARS,
        models=list(CLAUDE_MODELS),
        default_model=DEFAULT_MODEL,
    )


class ReplyRequest(BaseModel):
    inquiry: reply.Text
    item_id: reply.ItemId = None
    draft: reply.Text
    policy: reply.Policy = ""


def _pipeline(request: Request) -> Pipeline:
    pipeline: Pipeline = request.app.state.ops
    return pipeline


def _inquiry(request: Request, item_id: str | None, inquiry: str) -> str:
    """件から開いたときは、問い合わせをサーバ側で件の本文から作る（伏せ字済み。画面から来た値は使わない）。

    自由入力の問い合わせ・下書きは会社側の文なので、既定では伏せない（ADR 0028）。
    """
    if item_id is None:
        return inquiry
    try:
        return reply.item_inquiry(_pipeline(request).store.get(item_id))
    except ItemNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0])) from e


class ReplyItem(BaseModel):
    id: str
    inquiry: str


@router.get("/reply/item/{item_id}")
async def reply_item(item_id: str, request: Request) -> ReplyItem:
    """返信前チェックを件から開いたときの問い合わせ（伏せ字済み）。"""
    return ReplyItem(id=item_id, inquiry=_inquiry(request, item_id, ""))


@router.post("/reply")
async def check_reply(
    req: ReplyRequest, request: Request, backend: Annotated[BackendLike, Depends(_backend)]
) -> reply.ReplyResult:
    inquiry, draft = _inquiry(request, req.item_id, req.inquiry), req.draft
    questions = reply.questions()
    try:
        judged = await judge(
            backend.client,
            backend.ledger,
            app=reply.APP_NAME,
            state=reply.state(inquiry, draft, req.policy),
            questions=questions,
        )
    except BudgetExceededError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TypeSafeError as e:
        raise HTTPException(status_code=502, detail=f"判定に失敗しました: {type(e).__name__}: {e}") from e
    try:
        return reply.build_result(
            answer_views(questions, judged.response),
            sent_inquiry=inquiry,
            sent_draft=draft,
            model=judged.response.model,
            latency_ms=judged.latency_ms,
            cost_usd=judged.cost_usd,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"判定の応答が想定と違います: {type(e).__name__}: {e}") from e


async def _written(request: Request, model: ClaudeModel, system: str, prompt: str, what: str) -> tone.RewriteResult:
    """Claude に文面を書かせる（修正案・返信の案）。"""
    slots: asyncio.Semaphore = request.app.state.rewrite_slots
    try:
        generator = make_generator(model)
        async with slots:
            out = await generator.generate(GenerateRequest(system=system, prompt=prompt, schema=tone.REWRITE_SCHEMA))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=f"{what}を作れませんでした: {e}") from e
    data = out.structured
    if data is None or not isinstance(data.get("rewritten"), str) or not data["rewritten"].strip():
        raise HTTPException(status_code=502, detail=f"{what}の形が想定と違います: {out.text[:200]}")
    return tone.RewriteResult(
        rewritten=str(data["rewritten"]).strip(),
        changes=_strings(data.get("changes")),
        placeholders=_strings(data.get("placeholders")),
        model=out.model,
        latency_ms=out.latency_ms,
        reported_cost_usd=out.reported_cost_usd,
    )


@router.post("/reply/rewrite")
async def rewrite_reply(req: reply.RewriteRequest, request: Request) -> tone.RewriteResult:
    req = req.model_copy(update={"inquiry": _inquiry(request, req.item_id, req.inquiry)})
    return await _written(request, req.model, reply.REWRITE_SYSTEM, reply.rewrite_prompt(req), "修正案")


@router.post("/reply/draft")
async def draft_reply(req: reply.DraftRequest, request: Request) -> tone.RewriteResult:
    """問い合わせから、返信の案を Claude に書かせる（件から開いたときは伏せ字にした問い合わせを使う）。"""
    req = req.model_copy(update={"inquiry": _inquiry(request, req.item_id, req.inquiry)})
    return await _written(request, req.model, reply.DRAFT_SYSTEM, reply.draft_prompt(req), "返信の案")


def _line(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


@router.post("/reply/suggest/stream")
async def suggest_reply_stream(req: reply.SuggestRequest, request: Request) -> StreamingResponse:
    """AI返信案をストリーミングで返す（1 行 1 つの JSON）。

    - {"type": "text", "text": "…"}: 書いた分
    - {"type": "done", "model": "…", "latency_ms": 1234.5}: 書き終わり
    - {"type": "error", "message": "…"}: 失敗（途中まで書いた分は画面に残る）
    件から開いたときは、問い合わせをサーバ側で伏せ字にした件の本文に置き換える。
    """
    req = req.model_copy(update={"inquiry": _inquiry(request, req.item_id, req.inquiry)})
    system, prompt = reply.suggest_system_and_prompt(req)
    slots: asyncio.Semaphore = request.app.state.rewrite_slots

    async def body() -> AsyncIterator[bytes]:
        try:
            async with slots:
                async for part in make_stream_generator(req.model).stream(
                    GenerateRequest(system=system, prompt=prompt)
                ):
                    if isinstance(part, GeneratedText):
                        yield _line({"type": "done", "model": part.model, "latency_ms": part.latency_ms})
                    else:
                        yield _line({"type": "text", "text": part})
        except GenerationError as e:
            yield _line({"type": "error", "message": f"AI返信案を作れませんでした: {e}"})

    return StreamingResponse(body(), media_type="application/x-ndjson")


# ---- 個人情報チェック（ローカル） ----

# 契約書など長い文も調べられるように、ツールの入力の上限に合わせる
PII_CHECK_MAX: Final = 20000


class PiiCheckRequest(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=PII_CHECK_MAX)]


class PiiCheckResult(BaseModel):
    spans: list[Span]
    # 個人情報と判定した候補（confirmed）を【種類】に置き換えた本文
    masked_text: str
    # Kev（または MOCK）に聞いたか。Kev を使わない設定か、届かないときは規則だけ
    used_model: bool
    # 聞いた接続先の名前（規則だけなら null）
    model_name: str | None
    labels: dict[str, str]


@router.post("/pii-check")
async def pii_check(req: PiiCheckRequest, request: Request) -> PiiCheckResult:
    """ツールの入力をローカルで調べる。規則と、設定で使うときだけ Kev。Jev・Claude には送らない。"""
    spans, asked = await _pipeline(request).check_pii(req.text)
    masked = apply_mask(req.text, spans, dict.fromkeys(PII_TYPES, "mask"))
    return PiiCheckResult(
        spans=spans,
        masked_text=masked,
        used_model=asked is not None,
        model_name=TARGET_NAMES[asked] if asked else None,
        labels=dict(PII_LABELS),
    )
