from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """API を呼ばないモック設定。使用量の記録先は一時ディレクトリにする。"""
    monkeypatch.setenv("JEVLAB_MOCK", "1")
    monkeypatch.setenv("JEVLAB_VAR_DIR", str(tmp_path))
    monkeypatch.delenv("JEVLAB_BUDGET_USD", raising=False)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEVLAB_DEFAULT_TARGET", raising=False)
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    # 手元で Kev が動いていても結果が変わらないよう、つながらない宛先にする
    monkeypatch.setenv("KEV_URL", "http://127.0.0.1:9")
    return tmp_path
