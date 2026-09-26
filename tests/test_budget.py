from __future__ import annotations

from pathlib import Path

import pytest

from jevlab.core.budget import BudgetExceededError, Ledger, cost_of


def test_cost_of_matches_price() -> None:
    assert cost_of(1_000_000) == pytest.approx(0.042)


def test_record_accumulates(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "usage.jsonl", cap_usd=1.0)
    assert ledger.total_usd() == 0.0
    ledger.record(app="t", model="m", input_tokens=1000, output_tokens=10, latency_ms=12.3)
    ledger.record(app="t", model="m", input_tokens=2000, output_tokens=10, latency_ms=12.3)
    assert ledger.total_usd() == pytest.approx(cost_of(3000))


def test_ensure_within_blocks_over_cap(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "usage.jsonl", cap_usd=cost_of(1500))
    ledger.record(app="t", model="m", input_tokens=1000, output_tokens=0, latency_ms=1)
    ledger.ensure_within(cost_of(400))
    with pytest.raises(BudgetExceededError, match="予算上限"):
        ledger.ensure_within(cost_of(600))


def test_from_env_separates_mock_ledger(mock_env: Path) -> None:
    assert Ledger.from_env().path == mock_env / "usage.mock.jsonl"


def test_from_env_custom_target_is_unbilled(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEVLAB_MOCK")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:8009")
    monkeypatch.setenv("JEVLAB_BUDGET_USD", "0")
    ledger = Ledger.from_env()
    assert ledger.path == mock_env / "usage.custom.jsonl"
    assert not ledger.billed
    ledger.ensure_within(cost_of(10_000))
    rec = ledger.record(app="t", model="kev-latest", input_tokens=1000, output_tokens=0, latency_ms=1)
    assert rec.cost_usd == 0.0
    assert ledger.total_usd() == 0.0
