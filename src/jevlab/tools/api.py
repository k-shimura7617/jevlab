"""ツール（言い方チェック）の API。判定は選んだ接続先（?target=）、書き換え案は生成器（Claude）。"""

from __future__ import annotations

import asyncio
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from typesafe_sdk import TypeSafeError

from jevlab.core import target
from jevlab.core.budget import BudgetExceededError
from jevlab.core.client import judge
from jevlab.core.engine import answer_views
from jevlab.core.generator import CLAUDE_MODELS, GenerateRequest, GenerationError, make_generator
from jevlab.ops.pipeline import BackendLike
from jevlab.tools import tone

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
