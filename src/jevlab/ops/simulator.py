"""受信シミュレータ。デモ用のメール・チャットを一定間隔で受信箱に流す。

本物のコネクタ（Gmail の push 通知・Slack の Events API）の代わりに、同じ受信処理（Pipeline.ingest）を呼ぶ。
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import cache
from pathlib import Path
from typing import Final

from jevlab.ops.models import IngestRequest
from jevlab.ops.pipeline import Pipeline

log = logging.getLogger(__name__)

DEMO_PATH: Final = Path(__file__).with_name("demo_inbox.jsonl")
FAST_INTERVAL_S: Final = 0.1
_VIA: Final = {"mail": "メール（support@komorebi.example）", "chat": "チャット（#お問い合わせ窓口）"}


@cache
def demo_inbox() -> tuple[IngestRequest, ...]:
    def parse(line: str) -> IngestRequest:
        row = json.loads(line)
        return IngestRequest(
            channel=row["channel"],
            from_name=row["from_name"],
            from_address=row["from_address"],
            subject=row.get("subject", ""),
            body=row["body"],
            expected=row.get("expected"),
        )

    lines = DEMO_PATH.read_text(encoding="utf-8").splitlines()
    return tuple(parse(line) for line in lines if line.strip())


def ingest_next(pipeline: Pipeline) -> bool:
    """次のデモの 1 件を受信する。最後まで流し終えたら止めて False を返す。"""
    store = pipeline.store
    settings = store.settings()
    inbox = demo_inbox()
    cursor = settings.simulator.cursor
    if cursor >= len(inbox):
        store.update_settings(
            lambda s: s.model_copy(update={"simulator": s.simulator.model_copy(update={"playing": False})})
        )
        return False
    req = inbox[cursor]
    store.update_settings(
        lambda s: s.model_copy(update={"simulator": s.simulator.model_copy(update={"cursor": cursor + 1})})
    )
    try:
        pipeline.ingest(req, via=_VIA.get(req.channel))
    except PermissionError as e:
        # コネクタが切断されている間に届いた分は取りこぼしとして記録し、次に進む
        log.warning("受信シミュレータ: %s", e)
    return True


async def run_simulator(pipeline: Pipeline) -> None:
    while True:
        try:
            settings = pipeline.store.settings().simulator
            if settings.playing:
                ingest_next(pipeline)
            # 高速は、進み具合が画面で追える程度の短い間隔で流す（一度にまとめると何が起きたか見えない）
            delay = (FAST_INTERVAL_S if settings.fast else settings.interval_s) if settings.playing else 0.5
        except Exception:
            # 1 回の失敗でシミュレータ全体が止まらないよう記録して続ける
            log.exception("受信シミュレータの処理に失敗しました")
            delay = 2.0
        await asyncio.sleep(delay)
