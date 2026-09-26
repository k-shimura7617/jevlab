"""Jev の確信度の定義（公式の説明どおり）。確率分布から確信度を計算する。

確信度は「最大の確率」そのものではない。
- Choice: 最大確率を、選択肢の数 N で決まる当て推量の確率 1/N から見た伸びに直す。N が変わると同じ確率でも値が変わる
- Score: 最大確率の段階から離れた段階に確率があるほど下がる。両端に割れると 0 になる
"""

from __future__ import annotations

from collections.abc import Sequence


def choice_confidence(p_max: float, n: int) -> float:
    """(p_max − 1/N) / (1 − 1/N)。選択肢が 1 つなら 1。"""
    if n <= 1:
        return 1.0
    chance = 1 / n
    return max(0.0, (p_max - chance) / (1 - chance))


def score_confidence(probs: Sequence[float]) -> float:
    """max(0, 1 − D/B)。

    D: 最大確率の段階からの距離の期待値。
    B: 段階の中央からの平均距離（3 段階なら 2/3）。
    """
    n = len(probs)
    if n <= 1:
        return 1.0
    top = max(range(n), key=lambda i: probs[i])
    d = sum(p * abs(i - top) for i, p in enumerate(probs))
    center = (n - 1) / 2
    b = sum(abs(i - center) for i in range(n)) / n
    return max(0.0, 1 - d / b)
