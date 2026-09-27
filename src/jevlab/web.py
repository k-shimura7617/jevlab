"""検証用 Web アプリ（アプリ一覧＋アプリごとのページ）。API キーはサーバ側だけで扱い、ブラウザには渡さない。

接続先（mock / custom=Kev / jev）ごとにクライアントと使用量の記録を持ち、API は ?target= で切り替える。
省略時は既定の接続先（jevlab.core.target.current）。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

from jevlab.apps import APPS
from jevlab.core import engine, kev_health, runs, target
from jevlab.core.budget import BudgetExceededError, Ledger
from jevlab.core.client import make_client
from jevlab.ops import api as ops_api
from jevlab.ops import store as ops_store
from jevlab.ops.models import GuardTarget
from jevlab.ops.models import Settings as OpsSettings
from jevlab.ops.pipeline import Pipeline, run_worker
from jevlab.ops.simulator import run_simulator
from jevlab.ops.slack import SlackConnector
from jevlab.tools import api as tools_api

# frontend/ の Vite ビルド出力先（npm run build で生成する）
STATIC_DIR = Path(__file__).with_name("static")
# Kev の死活確認。停止中はすぐ接続拒否になるため短くてよい


@dataclass(frozen=True)
class Backend:
    target: target.Target
    client: AsyncTypeSafeClient
    ledger: Ledger


def _enabled_targets(default: target.Target) -> list[target.Target]:
    # Jev は鍵があるときだけ用意する。既定が Jev なら鍵がなくても作り、起動時のエラーで気づけるようにする
    return [t for t in target.TARGETS if t != "jev" or target.jev_available() or default == "jev"]


def _initial_ops_settings(default: target.Target) -> OpsSettings:
    """受付箱の初期設定。仕分けは既定の接続先、個人情報の判定は Kev（既定がモックならモック）。"""
    base = OpsSettings()
    guard_target: GuardTarget = "mock" if default == "mock" else "custom"
    return base.model_copy(
        update={
            "guard": base.guard.model_copy(update={"target": guard_target}),
            "classify": base.classify.model_copy(update={"target": default}),
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    default = target.current()
    async with AsyncExitStack() as stack:
        backends: dict[target.Target, Backend] = {}
        for t in _enabled_targets(default):
            client = await stack.enter_async_context(make_client(t))
            backends[t] = Backend(t, client, Ledger.for_target(t))
        app.state.default_target = default
        app.state.backends = backends
        # イベントループに結び付くため、モジュール読み込み時ではなく起動時に作る
        app.state.rewrite_slots = asyncio.Semaphore(tools_api.REWRITE_SLOTS)
        store = ops_store.Store(ops_store.default_path())
        stack.callback(store.close)
        if not store.has_settings():
            store.put_settings(_initial_ops_settings(default))
        pipeline = Pipeline(store, backends)
        app.state.ops = pipeline
        # 実際の Slack とのつなぎ込み（トークンがなければ何もしない）
        slack = SlackConnector.from_env(pipeline)
        app.state.slack = slack
        tasks = [
            asyncio.create_task(run_worker(pipeline)),
            asyncio.create_task(run_simulator(pipeline)),
            asyncio.create_task(slack.run()),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="jevlab", lifespan=lifespan)
app.include_router(ops_api.router)
app.include_router(tools_api.router)
# 未ビルドでも API は使えるよう、ディレクトリの存在は起動時に確認しない
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


def _backend(
    request: Request,
    selected: Annotated[target.Target | None, Query(alias="target", description="接続先。省略時は既定")] = None,
) -> Backend:
    t = selected or request.app.state.default_target
    backend: Backend | None = request.app.state.backends.get(t)
    if backend is None:
        raise HTTPException(status_code=400, detail=f"接続先 {t!r} は使えません（TYPESAFE_API_KEY が未設定）")
    return backend


BackendDep = Annotated[Backend, Depends(_backend)]


def _spec(name: str) -> engine.AppSpec:
    spec = APPS.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"アプリ {name!r} は登録されていません")
    return spec


class TargetStatus(BaseModel):
    name: target.Target
    endpoint: str
    available: bool
    # 使えないときの理由（画面のボタンの説明に出す）
    reason: str | None
    billed: bool
    total_usd: float
    cap_usd: float


class Status(BaseModel):
    default: target.Target
    targets: list[TargetStatus]


class JudgeRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class SummaryRequest(BaseModel):
    cases: list[engine.CaseResult] = Field(min_length=1)


_HISTORY_LIMIT = 20


def _to_http(e: BudgetExceededError | TypeSafeError) -> HTTPException:
    if isinstance(e, BudgetExceededError):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=502, detail=f"TypeSafe API エラー: {type(e).__name__}: {e}")


def _spa() -> FileResponse:
    # 画面遷移は React Router が受け持つため、どのページも同じ index.html を返す
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="frontend 未ビルド: cd frontend && npm install && npm run build")
    return FileResponse(index)


@app.get("/")
async def index() -> FileResponse:
    return _spa()


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    # <link rel="icon"> のない応答（404 の JSON など）でもブラウザが取りに来るため
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/eval")
async def eval_page() -> FileResponse:
    return _spa()


@app.get("/eval/apps/{name}")
async def eval_app_page(name: str) -> FileResponse:
    _spec(name)
    return _spa()


@app.get("/eval/apps/{name}/run")
async def eval_run_page(name: str) -> FileResponse:
    _spec(name)
    return _spa()


# 以前の URL。画面側で /eval/apps/… に移す（ブックマークを壊さないため）
@app.get("/apps/{name}")
async def app_page(name: str) -> FileResponse:
    _spec(name)
    return _spa()


@app.get("/apps/{name}/run")
async def run_page(name: str) -> FileResponse:
    _spec(name)
    return _spa()


@app.get("/tools/{path:path}")
async def tools_pages(path: str) -> FileResponse:
    return _spa()


@app.get("/ops")
async def ops_page() -> FileResponse:
    return _spa()


@app.get("/ops/{path:path}")
async def ops_pages(path: str) -> FileResponse:
    return _spa()


# 管理（開発側）の画面
@app.get("/admin")
async def admin_page() -> FileResponse:
    return _spa()


@app.get("/admin/{path:path}")
async def admin_pages(path: str) -> FileResponse:
    return _spa()


async def _target_status(request: Request, t: target.Target) -> TargetStatus:
    backend: Backend | None = request.app.state.backends.get(t)
    ledger = backend.ledger if backend else Ledger.for_target(t)
    if backend is None:
        reason: str | None = "TYPESAFE_API_KEY が未設定（.env に設定して起動し直す）"
    elif t == "custom":
        reason = await kev_health.down_reason()
    else:
        reason = None
    return TargetStatus(
        name=t,
        endpoint=target.endpoint(t),
        available=reason is None,
        reason=reason,
        billed=ledger.billed,
        total_usd=ledger.total_usd(),
        cap_usd=ledger.cap_usd,
    )


@app.get("/api/status")
async def status(request: Request) -> Status:
    targets = [await _target_status(request, t) for t in target.TARGETS]
    return Status(default=request.app.state.default_target, targets=targets)


@app.get("/api/apps")
async def list_apps() -> list[engine.AppInfo]:
    return [engine.app_info(spec) for spec in APPS.values()]


@app.get("/api/apps/{name}")
async def get_app(name: str) -> engine.AppInfo:
    return engine.app_info(_spec(name))


@app.get("/api/apps/{name}/samples")
async def samples(name: str) -> list[engine.Sample]:
    return engine.load_dataset(_spec(name))


@app.post("/api/apps/{name}/judge")
async def judge_one(name: str, backend: BackendDep, payload: JudgeRequest) -> engine.JudgeResult:
    spec = _spec(name)
    try:
        return await engine.run_judge(backend.client, backend.ledger, spec, payload.body)
    except (BudgetExceededError, TypeSafeError) as e:
        raise _to_http(e) from e


@app.post("/api/apps/{name}/samples/{sample_id}/judge")
async def judge_sample(name: str, sample_id: str, backend: BackendDep) -> engine.CaseResult:
    spec = _spec(name)
    sample = next((s for s in engine.load_dataset(spec) if s.id == sample_id), None)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"評価データ {sample_id!r} は {name!r} にありません")
    try:
        return engine.grade(sample, await engine.run_judge(backend.client, backend.ledger, spec, sample.body))
    except (BudgetExceededError, TypeSafeError) as e:
        raise _to_http(e) from e


def _save_run(report: engine.EvalReport, total: int, mode: target.Target) -> None:
    runs.append(runs.default_path(), runs.to_record(report, total=total, mode=mode))


@app.post("/api/apps/{name}/evaluate")
async def evaluate(name: str, backend: BackendDep) -> engine.EvalReport:
    spec = _spec(name)
    try:
        report = await engine.evaluate(backend.client, backend.ledger, spec)
    except (BudgetExceededError, TypeSafeError) as e:
        raise _to_http(e) from e
    _save_run(report, total=report.n, mode=backend.target)
    return report


@app.post("/api/apps/{name}/summary")
async def summary(name: str, backend: BackendDep, payload: SummaryRequest) -> engine.EvalReport:
    """ライブ評価で集めた結果を集計し、実行記録に残す。途中で中止した分も受け付ける。"""
    spec = _spec(name)
    samples = {s.id: s for s in engine.load_dataset(spec)}
    ids = [c.sample.id for c in payload.cases]
    unknown = sorted(set(ids) - set(samples))
    if unknown:
        raise HTTPException(status_code=422, detail=f"評価データにない ID が含まれています: {unknown}")
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="同じ ID の結果が重複しています")
    # 正解ラベルはブラウザから送られた値ではなく、サーバ側の評価データで採点し直す
    report = engine.summarize(spec, [engine.grade(samples[c.sample.id], c.result) for c in payload.cases])
    _save_run(report, total=len(samples), mode=backend.target)
    return report


@app.get("/api/runs/latest")
async def latest_runs() -> list[runs.RunRecord]:
    return runs.latest_by_app_mode(runs.load(runs.default_path()))


@app.get("/api/apps/{name}/runs")
async def app_runs(name: str) -> list[runs.RunRecord]:
    _spec(name)
    history = [r for r in runs.load(runs.default_path()) if r.app == name]
    return history[::-1][:_HISTORY_LIMIT]
