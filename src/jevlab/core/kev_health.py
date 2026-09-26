"""Kev（ローカルの互換サーバ）に接続できるかの確認。"""

from __future__ import annotations

import time

import httpx2

from jevlab.core import target

PROBE_TIMEOUT_S = 1.5
# 運用画面は数秒おきに状態を読むので、確認の結果をしばらく使い回す（Kev に毎回問い合わせない）
CACHE_S = 5.0

_cache: tuple[float, str | None] | None = None


async def down_reason() -> str | None:
    """接続できれば None、できなければ理由。"""
    url = f"{target.kev_url()}/v1/models"
    try:
        async with httpx2.AsyncClient(timeout=PROBE_TIMEOUT_S) as http:
            res = await http.get(url)
    except httpx2.HTTPError as e:
        return f"Kev（{target.kev_url()}）に接続できません: {type(e).__name__}"
    return None if res.is_success else f"Kev（{target.kev_url()}）が HTTP {res.status_code} を返しました"


async def cached_down_reason() -> str | None:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_S:
        return _cache[1]
    reason = await down_reason()
    _cache = (time.monotonic(), reason)
    return reason
