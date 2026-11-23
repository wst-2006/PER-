"""总仓位控制：根据市场状态决定持股占净值的比例。所有信号只用决策日收盘及以前的数据。

市场用股票池等权指数代表（与回测基准相同）。
- vol_target  仓位 = min(1, 目标波动 ÷ 近 20 日已实现波动)，目标波动取过去 750 日波动的中位数，
              即"比平时波动更大时按比例减仓"。
- trend       指数低于自身 120 日均线时仓位减半。
- both        两者相乘。
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

DAYS_PER_YEAR = 244


def build(bench: dict[str, float], mode: str, *, vol_window: int = 20, vol_anchor: int = 750,
          trend_window: int = 120, trend_cut: float = 0.5, floor: float = 0.0) -> Callable[[str], float]:
    """bench：日期 → 当日等权指数收益。返回 exposure(date)。"""
    if mode == "none":
        return lambda _d: 1.0
    dates = sorted(bench)
    rets = np.array([bench[d] for d in dates])
    level = np.cumprod(1 + rets)
    out: dict[str, float] = {}
    for i, d in enumerate(dates):
        w = 1.0
        if mode in ("vol_target", "both") and i + 1 >= vol_window:
            realized = rets[i + 1 - vol_window:i + 1].std() * np.sqrt(DAYS_PER_YEAR)
            history = [rets[j + 1 - vol_window:j + 1].std() for j in range(max(vol_window - 1, i + 1 - vol_anchor), i + 1)]
            target = float(np.median(history)) * np.sqrt(DAYS_PER_YEAR)
            if realized > 0:
                w *= min(1.0, target / realized)
        if mode in ("trend", "both") and i + 1 >= trend_window:
            if level[i] < level[i + 1 - trend_window:i + 1].mean():
                w *= trend_cut
        out[d] = max(floor, w)
    return lambda d: out.get(d, 1.0)


def describe(fn: Callable[[str], float], dates: list[str]) -> dict[str, Any]:
    w = np.array([fn(d) for d in dates])
    return {"mean_exposure": round(float(w.mean()), 3), "share_below_0.6": round(float((w < 0.6).mean()), 3)}
