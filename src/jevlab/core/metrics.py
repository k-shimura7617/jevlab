"""評価指標（純粋関数）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class Bin:
    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float


def accuracy(correct: Sequence[bool]) -> float:
    return fmean(correct) if correct else 0.0


def brier(probs: Sequence[float], labels: Sequence[bool]) -> float:
    """二値の Brier スコア。0 が完璧、0.25 は常に 0.5 と答えた場合。"""
    return fmean((p - float(y)) ** 2 for p, y in zip(probs, labels, strict=True)) if probs else 0.0


def mae(preds: Sequence[float], labels: Sequence[float]) -> float:
    return fmean(abs(p - y) for p, y in zip(preds, labels, strict=True)) if preds else 0.0


def reliability(confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 5) -> list[Bin]:
    """確信度を区間に分け、区間ごとの平均確信度と実際の正解率を並べる（較正の確認用）。"""
    edges = [i / n_bins for i in range(n_bins + 1)]

    def in_bin(c: float, i: int) -> bool:
        return edges[i] <= c < edges[i + 1] or (i == n_bins - 1 and c == 1.0)

    def make(i: int) -> Bin:
        members = [(c, ok) for c, ok in zip(confidences, correct, strict=True) if in_bin(c, i)]
        return Bin(
            lower=edges[i],
            upper=edges[i + 1],
            count=len(members),
            mean_confidence=fmean(c for c, _ in members) if members else 0.0,
            accuracy=fmean(ok for _, ok in members) if members else 0.0,
        )

    return [make(i) for i in range(n_bins)]


@dataclass(frozen=True)
class NoulBias:
    """Noul の偏り。Jev は「当てはまる」に寄りがちなので、正解が否の件の P(はい) の平均で見る。"""

    n_true: int
    n_false: int
    # 正解が「はい」／「いいえ」の件の、P(はい) の平均（その側の件がなければ None）
    mean_yes_when_true: float | None
    mean_yes_when_false: float | None
    # はい・いいえの両側の正解率の平均がいちばん高い閾値（同点なら 0.5 に近い方）
    best_threshold: float
    accuracy_at_best: float
    accuracy_at_default: float


_THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]


def _balanced_accuracy(probs: Sequence[float], labels: Sequence[bool], threshold: float) -> float:
    """はい・いいえの両側の正解率の平均（片側しかなければ、その側の正解率）。"""
    sides = [[(p >= threshold) == y for p, y in zip(probs, labels, strict=True) if y is side] for side in (True, False)]
    rates = [fmean(s) for s in sides if s]
    return fmean(rates) if rates else 0.0


def noul_bias(probs: Sequence[float], labels: Sequence[bool], default: float = 0.5) -> NoulBias:
    """質問ごとに、P(はい) の偏りと、評価データで最もよく分けられる閾値を求める。"""
    yes = [p for p, y in zip(probs, labels, strict=True) if y]
    no = [p for p, y in zip(probs, labels, strict=True) if not y]
    best = max(_THRESHOLDS, key=lambda t: (_balanced_accuracy(probs, labels, t), -abs(t - default)))
    return NoulBias(
        n_true=len(yes),
        n_false=len(no),
        mean_yes_when_true=fmean(yes) if yes else None,
        mean_yes_when_false=fmean(no) if no else None,
        best_threshold=best,
        accuracy_at_best=_balanced_accuracy(probs, labels, best),
        accuracy_at_default=_balanced_accuracy(probs, labels, default),
    )


def ece(bins: Sequence[Bin]) -> float:
    """Expected Calibration Error。確信度と正解率のずれの加重平均。0 に近いほど較正が良い。"""
    total = sum(b.count for b in bins)
    return sum(b.count / total * abs(b.mean_confidence - b.accuracy) for b in bins) if total else 0.0
