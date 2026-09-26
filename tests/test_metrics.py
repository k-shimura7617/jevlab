from __future__ import annotations

import pytest

from jevlab.core import metrics


def test_accuracy() -> None:
    assert metrics.accuracy([True, False, True, True]) == 0.75
    assert metrics.accuracy([]) == 0.0


def test_brier() -> None:
    assert metrics.brier([1.0, 0.0], [True, False]) == 0.0
    assert metrics.brier([0.5, 0.5], [True, False]) == 0.25


def test_mae() -> None:
    assert metrics.mae([0.5, 2.0], [0, 2]) == 0.25


def test_reliability_places_one_in_last_bin() -> None:
    bins = metrics.reliability([0.1, 0.9, 1.0], [False, True, False], n_bins=5)
    assert [b.count for b in bins] == [1, 0, 0, 0, 2]
    assert bins[-1].accuracy == 0.5
    assert bins[-1].mean_confidence == pytest.approx(0.95)


def test_ece_perfectly_calibrated_is_zero() -> None:
    bins = metrics.reliability([0.9] * 10, [True] * 9 + [False], n_bins=5)
    assert metrics.ece(bins) == pytest.approx(0.0)


def test_ece_overconfident() -> None:
    bins = metrics.reliability([1.0, 1.0], [True, False], n_bins=5)
    assert metrics.ece(bins) == pytest.approx(0.5)
