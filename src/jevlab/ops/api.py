"""運用（受付箱）の API。処理の本体は Pipeline、保存は Store に任せる。"""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jevlab.apps.mail.questions import CATEGORY_LABELS
from jevlab.core import kev_health, target
from jevlab.core.generator import ClaudeModel, GenerateRequest, GenerationError, make_generator
from jevlab.ops import importers, misses, pii_eval, scope_draft, tuning
from jevlab.ops.models import (
    CHANNEL_LABELS,
    STATUSES,
    Event,
    IngestRequest,
    Item,
    MissReport,
    Post,
    Settings,
    SlaSettings,
    Status,
)
from jevlab.ops.pii import ACTIONS, PII_LABELS, PII_TYPES, PiiType, Span
from jevlab.ops.pipeline import (
    ESCALATION_CHANNEL,
    EXAMPLE_DECISIONS,
    INBOUND_CHANNEL,
    ROUTE_CHANNELS,
    Pipeline,
    category_label,
    priority_parts,
    safe_title,
)
from jevlab.ops.questions import FIELD_TITLES
from jevlab.ops.simulator import demo_inbox, ingest_next
from jevlab.ops.sla import minutes_left
from jevlab.ops.slack import SlackConnector, SlackStatus
from jevlab.ops.store import ItemNotFoundError

router = APIRouter(prefix="/api/ops", tags=["ops"])


def _pipeline(request: Request) -> Pipeline:
    pipeline: Pipeline | None = getattr(request.app.state, "ops", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="運用機能が起動していません")
    return pipeline


PipelineDep = Annotated[Pipeline, Depends(_pipeline)]


def _http(e: Exception) -> HTTPException:
    match e:
        case ItemNotFoundError():
            return HTTPException(status_code=404, detail=str(e.args[0]))
        case PermissionError():
            return HTTPException(status_code=409, detail=str(e))
        case ValueError():
            return HTTPException(status_code=422, detail=str(e))
    raise e


# 個人情報の確認に回った理由（画面の表示と、一括で流してよいかの判断に使う）
# detected: 見つかった個人情報はすべて確定済み（方針どおりマスクすれば済む）
# possible_name: 氏名かもしれない語を自動では決めきれなかった
# possible_missed: 見つかった以外にも個人情報が残っているかもしれない
PiiFlag = Literal["detected", "possible_name", "possible_missed"]


class ItemOut(Item):
    # 優先度の観点ごとの値（0〜1）。画面で重みを掛けて並べ替える
    priority: dict[str, float] = Field(default_factory=dict)
    pii_flags: list[PiiFlag] = Field(default_factory=list)
    # エスカレーションの対応目安までの残り（営業時間の分。過ぎていれば負）
    sla_left_min: float | None = None


def pii_flags(item: Item, leftover_threshold: float) -> list[PiiFlag]:
    flags: list[PiiFlag] = []
    if any(s.confirmed for s in item.pii):
        flags.append("detected")
    if any(not s.confirmed for s in item.pii):
        flags.append("possible_name")
    if (item.pii_leftover or 0.0) >= leftover_threshold:
        flags.append("possible_missed")
    return flags


def _out(item: Item, leftover_threshold: float = 0.5, sla: SlaSettings | None = None) -> ItemOut:
    return ItemOut(
        **item.model_dump(),
        priority=priority_parts(item.answers) if item.answers else {},
        pii_flags=pii_flags(item, leftover_threshold) if item.status == "pii_review" else [],
        sla_left_min=minutes_left(item.received_at, datetime.now(UTC), sla)
        if sla is not None and item.status == "escalated"
        else None,
    )


class Meta(BaseModel):
    categories: dict[str, str]
    statuses: list[str]
    channels: dict[str, str]
    pii_types: dict[str, str]
    actions: list[str]
    fields: dict[str, str]
    route_channels: dict[str, str]
    escalation_channel: str
    inbound_channel: str
    demo_count: int


@router.get("/meta")
async def meta() -> Meta:
    return Meta(
        categories=CATEGORY_LABELS,
        statuses=list(STATUSES),
        channels=dict(CHANNEL_LABELS),
        pii_types={t: PII_LABELS[t] for t in PII_TYPES},
        actions=list(ACTIONS),
        fields=FIELD_TITLES,
        route_channels=ROUTE_CHANNELS,
        escalation_channel=ESCALATION_CHANNEL,
        inbound_channel=INBOUND_CHANNEL,
        demo_count=len(demo_inbox()),
    )


class Flow(BaseModel):
    """流れ図の各段の件数。"""

    received: int
    guarded: int
    pii_found: int
    pii_review: int
    masked: int
    blocked: int
    classified: int
    kev_only: int
    auto: int
    review: int
    escalated: int
    closed: int
    error: int
    waiting: int


class Accuracy(BaseModel):
    n: int
    matched: int


class KevHealth(BaseModel):
    endpoint: str
    available: bool
    reason: str | None
    # Kev を使っている処理（止まるもの）
    uses: list[str]


def kev_uses(settings: Settings) -> list[str]:
    g = settings.guard
    uses = [
        (g.enabled and g.use_model and g.target == "custom", "個人情報のチェック"),
        (settings.classify.target == "custom", "仕分け"),
        (settings.kev_first.enabled, "Kev で先に判定"),
    ]
    return [label for on, label in uses if on]


class Overview(BaseModel):
    counts: dict[str, int]
    flow: Flow
    automation_rate: float | None
    # デモの想定ラベルとの一致（最終の分類 / 人が直す前のモデルの予測）
    final_accuracy: Accuracy
    model_accuracy: Accuracy
    cost_usd: float
    audit_pending: int
    simulator: dict[str, object]
    recent: list[Event]
    # 運用の設定が Kev を使うときだけ確認する（使わなければ None）。画面の「Kev に接続できません」に使う
    kev: KevHealth | None = None


def _accuracy(items: list[Item], final: bool) -> Accuracy:
    rows = [
        (
            i.category if final else (str(i.answers["category"].prediction) if "category" in i.answers else None),
            (i.expected or {}).get("category"),
        )
        for i in items
        if i.status in ("routed", "closed") and i.expected
    ]
    graded = [(p, t) for p, t in rows if p is not None and t is not None]
    return Accuracy(n=len(graded), matched=sum(1 for p, t in graded if p == t))


@router.get("/overview")
async def overview(pipeline: PipelineDep) -> Overview:
    store = pipeline.store
    # 過去の問い合わせ（試算用）は運用の集計に入れない
    items = [i for i in store.items(limit=10_000) if not i.backfill]
    counts = Counter(i.status for i in items)
    decided = [i for i in items if i.first_route is not None]
    flow = Flow(
        received=len(items),
        guarded=sum(1 for i in items if i.pii_decision is not None or i.status == "pii_review"),
        pii_found=sum(1 for i in items if any(s.confirmed for s in i.pii)),
        pii_review=counts["pii_review"],
        masked=sum(1 for i in items if i.pii_decision == "masked"),
        blocked=sum(1 for i in items if i.pii_decision == "blocked"),
        classified=sum(1 for i in items if i.decided_by is not None),
        kev_only=sum(1 for i in items if i.decided_by == "kev"),
        auto=sum(1 for i in items if i.first_route == "routed"),
        review=counts["review"],
        escalated=counts["escalated"],
        closed=counts["closed"],
        error=counts["error"],
        waiting=counts["queued"] + counts["processing"],
    )
    settings = store.settings()
    return Overview(
        counts={s: counts[s] for s in STATUSES},
        flow=flow,
        automation_rate=flow.auto / len(decided) if decided else None,
        final_accuracy=_accuracy(items, final=True),
        model_accuracy=_accuracy(items, final=False),
        cost_usd=sum(i.cost_usd for i in items),
        audit_pending=sum(1 for i in items if i.audit and i.audit_result is None and i.status == "routed"),
        simulator={**settings.simulator.model_dump(), "total": len(demo_inbox())},
        recent=store.recent_events(40),
        kev=await _kev_health(uses) if (uses := kev_uses(settings)) else None,
    )


async def _kev_health(uses: list[str]) -> KevHealth:
    reason = await kev_health.cached_down_reason()
    return KevHealth(endpoint=target.kev_url(), available=reason is None, reason=reason, uses=uses)


@router.get("/items")
async def list_items(
    pipeline: PipelineDep,
    status: Annotated[list[Status] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[ItemOut]:
    settings = pipeline.store.settings()
    return [_out(i, settings.guard.leftover_threshold, settings.sla) for i in pipeline.store.items(status, limit)]


class ItemDetail(BaseModel):
    item: ItemOut
    events: list[Event]


@router.get("/items/{item_id}")
async def get_item(item_id: str, pipeline: PipelineDep) -> ItemDetail:
    try:
        item = pipeline.store.get(item_id)
    except ItemNotFoundError as e:
        raise _http(e) from e
    settings = pipeline.store.settings()
    return ItemDetail(
        item=_out(item, settings.guard.leftover_threshold, settings.sla), events=pipeline.store.events(item_id)
    )


@router.post("/ingest")
async def ingest(req: IngestRequest, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.ingest(req, via="API" if req.channel == "api" else None))
    except PermissionError as e:
        raise _http(e) from e


class ChatMessage(BaseModel):
    from_name: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=2000)


@router.post("/chat")
async def post_chat(msg: ChatMessage, pipeline: PipelineDep) -> ItemOut:
    req = IngestRequest(channel="chat", from_name=msg.from_name, from_address="", subject="", body=msg.body)
    try:
        return _out(pipeline.ingest(req, via="チャット（#お問い合わせ窓口）"))
    except PermissionError as e:
        raise _http(e) from e


class ImportRow(BaseModel):
    from_name: str = Field("", max_length=100)
    from_address: str = Field("", max_length=200)
    subject: str = Field("", max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    # 過去の分類（mail の分類のキー）。導入前の試算の正解に使う
    category: str | None = None
    received_at: str | None = Field(None, max_length=40)


class ImportRequest(BaseModel):
    file_name: str = Field("", max_length=200)
    # 取り込んだ件の経路（表は CSV、メールのファイルはメール、Slack のエクスポートは Slack）
    channel: Literal["csv", "mail", "slack"] = "csv"
    # 過去の問い合わせとして取り込む（仕分けまで行い、投稿・人の対応には回さない）
    backfill: bool = False
    rows: list[ImportRow] = Field(min_length=1, max_length=500)


class ImportResult(BaseModel):
    imported: int
    ids: list[str]


@router.post("/import")
async def import_rows(req: ImportRequest, pipeline: PipelineDep) -> ImportResult:
    bad = sorted({r.category for r in req.rows if r.category is not None and r.category not in CATEGORY_LABELS})
    if bad:
        raise HTTPException(status_code=422, detail=f"分類 {bad} は不明です（{' / '.join(CATEGORY_LABELS)}）")
    via = f"ファイル取り込み（{req.file_name}）" if req.file_name else "ファイル取り込み"
    if req.backfill:
        via += "・過去の問い合わせ（試算用）"
    try:
        items = [
            pipeline.ingest(
                IngestRequest(
                    channel=req.channel,
                    from_name=r.from_name,
                    from_address=r.from_address,
                    subject=r.subject,
                    body=r.body,
                    expected={"category": r.category} if r.category else None,
                    backfill=req.backfill,
                    received_at=r.received_at,
                ),
                via=via,
                connector="csv",
            )
            for r in req.rows
        ]
    except PermissionError as e:
        raise _http(e) from e
    return ImportResult(imported=len(items), ids=[i.id for i in items])


class ParseRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    # ファイルの中身（base64）。上限はおよそ 20MB
    data: str = Field(min_length=1, max_length=28_000_000)


@router.post("/import/parse")
async def parse_import_file(req: ParseRequest) -> importers.ParsedFile:
    try:
        raw = base64.b64decode(req.data, validate=True)
    except binascii.Error as e:
        raise HTTPException(status_code=422, detail="ファイルの中身を読めません（base64 の誤り）") from e
    try:
        return importers.parse_file(req.file_name, raw)
    except importers.FileFormatError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class PiiDecision(BaseModel):
    spans: list[Span] = Field(max_length=200)
    action: Literal["continue", "block"]


@router.post("/items/{item_id}/pii")
async def submit_pii(item_id: str, req: PiiDecision, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.submit_pii(item_id, req.spans, req.action))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


class PiiDraft(BaseModel):
    # None で下書きを取り消す（検出した内容に戻す）
    spans: list[Span] | None = Field(max_length=200)


@router.put("/items/{item_id}/pii/draft")
async def save_pii_draft(item_id: str, req: PiiDraft, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.save_pii_draft(item_id, req.spans))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


class MissIn(BaseModel):
    type: PiiType
    start: int = Field(ge=0)
    end: int = Field(ge=1)


@router.post("/items/{item_id}/miss")
async def report_miss(item_id: str, req: MissIn, pipeline: PipelineDep) -> MissReport:
    try:
        return pipeline.report_miss(item_id, req.type, req.start, req.end)
    except (ItemNotFoundError, ValueError, PermissionError) as e:
        raise _http(e) from e


@router.get("/misses")
async def miss_summary(pipeline: PipelineDep, catch: Annotated[float, Query(gt=0, le=1)] = 0.8) -> misses.MissSummary:
    store = pipeline.store
    threshold = store.settings().guard.leftover_threshold
    return misses.summarize(store.misses(), store.items(limit=10_000), catch, threshold)


@router.get("/pii-eval")
async def pii_eval_summary(pipeline: PipelineDep) -> pii_eval.PiiEval:
    store = pipeline.store
    return pii_eval.summarize(
        store.items(limit=10_000),
        store.events_of_kind(["guard", "pii_review"]),
        store.settings().guard.leftover_threshold,
    )


class Decide(BaseModel):
    category: str
    note: str | None = Field(None, max_length=1000)


@router.post("/items/{item_id}/decide")
async def decide(item_id: str, req: Decide, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.decide(item_id, req.category, note=req.note))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


class Assign(BaseModel):
    assignee: str = Field("", max_length=100)


@router.post("/items/{item_id}/assign")
async def assign(item_id: str, req: Assign, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.assign(item_id, req.assignee))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


class BulkIds(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class BulkAssign(BulkIds):
    # 空文字なら担当を外す
    assignee: str = Field(max_length=40)


class BulkResult(BaseModel):
    done: list[str]
    errors: dict[str, str]


def _bulk(ids: list[str], f: Callable[[str], object]) -> BulkResult:
    """1 件ずつ処理し、失敗した件は理由を返す（途中の失敗で残りを止めない）。"""
    done: list[str] = []
    errors: dict[str, str] = {}
    for item_id in dict.fromkeys(ids):
        try:
            f(item_id)
            done.append(item_id)
        except (ItemNotFoundError, ValueError) as e:
            errors[item_id] = str(e.args[0]) if isinstance(e, ItemNotFoundError) else str(e)
    return BulkResult(done=done, errors=errors)


@router.post("/bulk/assign")
async def bulk_assign(req: BulkAssign, pipeline: PipelineDep) -> BulkResult:
    return _bulk(req.ids, lambda i: pipeline.assign(i, req.assignee, bulk=True))


@router.post("/bulk/pii")
async def bulk_pii(req: BulkIds, pipeline: PipelineDep) -> BulkResult:
    def one(item_id: str) -> None:
        item = pipeline.store.get(item_id)
        # 人が編集した件は、その判断（下書き）どおりに処理する。
        # 編集していない件は本文を見ていないので、氏名かもしれない語も個人情報として扱ってマスクする（安全側）
        spans = (
            item.pii_draft
            if item.pii_draft is not None
            else [s.model_copy(update={"confirmed": True}) for s in item.pii]
        )
        pipeline.submit_pii(item_id, spans, "continue", bulk=True)

    return _bulk(req.ids, one)


class AssignPair(BaseModel):
    suggested: str | None
    actual: str
    count: int


class AssignStats(BaseModel):
    """担当者の推定が、実際に対応を完了した担当とどれだけ合っていたか。"""

    closed: int
    with_suggestion: int
    matched: int
    auto_assigned: int
    auto_changed: int
    pairs: list[AssignPair]
    # 担当範囲の案の材料になる件の数（人が割り当てて完了し、個人情報の確認を通った件）
    handled_by_staff: dict[str, int] = {}


@router.get("/assignment")
async def assignment_stats(pipeline: PipelineDep) -> AssignStats:
    closed = [i for i in pipeline.store.items(["closed"], limit=10_000) if i.assignee]
    handled = Counter(
        i.assignee for i in closed if i.assignee and i.assigned_by == "human" and i.pii_decision in EXAMPLE_DECISIONS
    )
    # 推定の当たり具合は、人が担当を決めた件だけで測る（自動で割り当てたままの件は必ず一致するため除く）
    judged = [i for i in closed if i.assign_suggestion is not None and i.assigned_by == "human"]
    auto = [i for i in pipeline.store.items(limit=10_000) if i.auto_assigned]
    pairs = Counter((i.assign_suggestion, i.assignee or "") for i in judged if i.assign_suggestion != i.assignee)
    return AssignStats(
        closed=len(closed),
        with_suggestion=len(judged),
        matched=sum(1 for i in judged if i.assign_suggestion == i.assignee),
        auto_assigned=len(auto),
        auto_changed=sum(1 for i in auto if i.assignee != i.assign_suggestion),
        pairs=[AssignPair(suggested=s, actual=a, count=n) for (s, a), n in pairs.most_common(10)],
        handled_by_staff=dict(handled),
    )


class ScopeDraftRequest(BaseModel):
    model: ClaudeModel = "sonnet"


@router.post("/staff/{staff_id}/scope-draft")
async def draft_scope(
    staff_id: str, req: ScopeDraftRequest, request: Request, pipeline: PipelineDep
) -> scope_draft.ScopeDraft:
    """担当者が対応を完了した件の見出し（伏せ字）と分類から、担当範囲の案を Claude に作らせる。保存はしない。"""
    settings = pipeline.store.settings()
    staff = next((s for s in settings.staff if s.id == staff_id), None)
    if staff is None:
        raise HTTPException(status_code=404, detail=f"担当者 {staff_id!r} は登録されていません")
    handled = scope_draft.handled_items(
        pipeline.store.items(["closed"], limit=2000), staff_id, sorted(EXAMPLE_DECISIONS)
    )
    need = settings.assign.scope_draft_min
    if len(handled) < need:
        raise HTTPException(status_code=422, detail=f"完了した件が {len(handled)} 件です（{need} 件から作れます）")
    data = scope_draft.ScopeDraftInput(
        staff=staff,
        others=[s for s in settings.staff if s.id != staff_id and s.active],
        handled=[(safe_title(i, limit=40), category_label(i.category)) for i in handled[: scope_draft.MAX_ITEMS]],
    )
    slots = request.app.state.rewrite_slots
    try:
        generator = make_generator(req.model)
        async with slots:
            out = await generator.generate(
                GenerateRequest(
                    system=scope_draft.SYSTEM, prompt=scope_draft.build_prompt(data), schema=scope_draft.SCHEMA
                )
            )
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=f"担当範囲の案を作れませんでした: {e}") from e
    result = out.structured or {}
    scope = result.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise HTTPException(status_code=502, detail=f"担当範囲の案の形が想定と違います: {out.text[:200]}")
    notes = result.get("notes")
    return scope_draft.ScopeDraft(
        staff_id=staff_id,
        scope=scope.strip()[: scope_draft.MAX_SCOPE],
        notes=[str(n) for n in notes] if isinstance(notes, list) else [],
        based_on=len(data.handled),
        model=out.model,
        latency_ms=out.latency_ms,
    )


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/items/{item_id}/note")
async def note(item_id: str, req: Note, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.add_note(item_id, req.text))
    except ItemNotFoundError as e:
        raise _http(e) from e


class Close(BaseModel):
    category: str | None = None


@router.post("/items/{item_id}/close")
async def close(item_id: str, req: Close, pipeline: PipelineDep) -> ItemOut:
    if req.category is not None and req.category not in CATEGORY_LABELS:
        raise HTTPException(status_code=422, detail=f"分類 {req.category!r} は不明です")
    try:
        return _out(pipeline.close(item_id, req.category))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


@router.post("/items/{item_id}/retry")
async def retry(item_id: str, pipeline: PipelineDep) -> ItemOut:
    try:
        return _out(pipeline.retry(item_id))
    except (ItemNotFoundError, ValueError) as e:
        raise _http(e) from e


@router.get("/settings")
async def get_settings(pipeline: PipelineDep) -> Settings:
    return pipeline.store.settings()


@router.put("/settings")
async def put_settings(settings: Settings, pipeline: PipelineDep) -> Settings:
    if settings.classify.review_threshold > settings.classify.auto_threshold:
        raise HTTPException(status_code=422, detail="確認待ちの閾値は、自動の閾値以下にしてください")
    # 保存済みの設定を読むときは弾かない（対応目安だけ短くした古い設定でも起動できるように）。保存するときに確かめる
    sla_min = settings.sla.hours * 60
    if settings.slack.reminder and settings.slack.reminder_before_min >= sla_min:
        raise HTTPException(
            status_code=422,
            detail=f"期限前に知らせる時間（{settings.slack.reminder_before_min} 分）は、"
            f"対応目安（{sla_min:g} 分）より短くしてください",
        )
    # 受信シミュレータの状態は専用の API（/simulator）でだけ変える。設定画面などが持っている古い値で上書きしない
    current = pipeline.store.settings().simulator
    saved = pipeline.store.put_settings(settings.model_copy(update={"simulator": current}))
    pipeline.wake.set()
    return saved


class SimulatorControl(BaseModel):
    playing: bool | None = None
    interval_s: float | None = Field(None, ge=0.5, le=60)
    # 1 件だけ流す
    step: bool = False
    # 先頭に戻す（受信済みの件は消さない）
    rewind: bool = False


@router.post("/simulator")
async def simulator(req: SimulatorControl, pipeline: PipelineDep) -> dict[str, object]:
    store = pipeline.store

    def change(s: Settings) -> Settings:
        sim = s.simulator
        update: dict[str, object] = {}
        if req.playing is not None:
            update["playing"] = req.playing
        if req.interval_s is not None:
            update["interval_s"] = req.interval_s
        if req.rewind:
            update["cursor"] = 0
        return s.model_copy(update={"simulator": sim.model_copy(update=update)})

    store.update_settings(change)
    if req.step:
        ingest_next(pipeline)
    return {**store.settings().simulator.model_dump(), "total": len(demo_inbox())}


@router.post("/reset")
async def reset(pipeline: PipelineDep) -> dict[str, str]:
    pipeline.store.reset()
    return {"detail": "受付箱を空にしました"}


@router.get("/slack")
async def slack_status(request: Request) -> SlackStatus:
    connector: SlackConnector = request.app.state.slack
    return connector.status()


@router.get("/posts")
async def posts(pipeline: PipelineDep, limit: Annotated[int, Query(ge=1, le=1000)] = 300) -> list[Post]:
    return pipeline.store.posts(limit)


@router.get("/tuning")
async def tuning_report(
    pipeline: PipelineDep,
    source: tuning.TruthSource = "human",
    target_error: Annotated[float, Query(ge=0, le=0.5)] = 0.02,
) -> tuning.TuningReport:
    return tuning.report(pipeline.store.items(limit=10_000), source, target_error, CATEGORY_LABELS)
