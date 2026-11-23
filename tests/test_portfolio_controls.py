"""组合控制测试：总仓位控制只用过去的数据；仓位下降时按比例减持；小市值数量上限。"""
from __future__ import annotations

import numpy as np
import pytest

from perq.engine.costs import CostModel
from perq.engine.engine import Context, Position
from perq.portfolio import exposure
from perq.portfolio.topn import TopN
from perq.research import backtest_report, composite
from test_factors import DAYS, make_market

import pandas as pd


def test_exposure_uses_only_past_and_reacts_to_volatility_and_trend():
    rng = np.random.default_rng(0)
    dates = [f"d{i:04d}" for i in range(1000)]
    rets = np.concatenate([rng.normal(0.0005, 0.01, 800), rng.normal(-0.003, 0.03, 200)])
    bench = dict(zip(dates, rets))
    vt = exposure.build(bench, "vol_target")
    tr = exposure.build(bench, "trend")
    both = exposure.build(bench, "both")
    assert np.mean([vt(d) for d in dates[100:780]]) > 0.8          # 平静期接近满仓
    assert np.mean([vt(d) for d in dates[850:]]) < 0.5             # 波动放大三倍后大幅减仓
    assert np.mean([tr(d) for d in dates[900:]]) == pytest.approx(0.5)
    assert both(dates[950]) == pytest.approx(vt(dates[950]) * tr(dates[950]))
    # 改掉第 900 天之后的数据，之前的仓位不变
    changed = dict(bench)
    for d in dates[900:]:
        changed[d] = 0.05
    vt2 = exposure.build(changed, "vol_target")
    assert all(vt(d) == vt2(d) for d in dates[:900])
    assert exposure.build(bench, "none")(dates[5]) == 1.0


class Book:
    def __init__(self, scores):
        self.scores = scores

    def day(self, date):
        return self.scores


def make_ctx(positions, nav, bars):
    return Context("20240102", cash=0.0, nav=nav, positions=positions, bars=bars, pending_sells={})


def test_topn_trims_when_exposure_falls_and_caps_small_caps():
    codes = [f"S{i:02d}" for i in range(20)]
    bars = pd.DataFrame({"close": 10.0, "in_universe": True, "circ_mv": np.arange(1, 21) * 1e5}, index=codes)
    scores = pd.Series(np.arange(20, 0, -1, dtype=float), index=codes)        # S00 打分最高，市值也最小
    # 满仓持有 S00–S04，每只 2 万元；仓位降到一半 → 每只减到约 1 万元
    positions = {c: Position(2000, 20000, "x", 10.0, 1.0) for c in codes[:5]}
    half = TopN(Book(scores), n=5, rebalance=1, invest_ratio=1.0, exposure=lambda d: 0.5)
    orders = half.on_close(make_ctx(positions, 100_000, bars))
    trims = {o.ts_code: o.shares for o in orders if o.reason == "trim"}
    assert set(trims) == set(codes[:5]) and all(v == pytest.approx(1000) for v in trims.values())
    # 空仓：全部持仓给出卖单，不买新股票
    zero = TopN(Book(scores), n=5, rebalance=1, exposure=lambda d: 0.0)
    assert {o.side for o in zero.on_close(make_ctx(positions, 100_000, bars))} == {"sell"}
    # 小市值上限：市值最小 20% 是 S00–S03；上限 40% 即 5 只里最多 2 只
    capped = TopN(Book(scores), n=5, rebalance=1, max_small_share=0.4)
    bought = [o.ts_code for o in capped.on_close(make_ctx({}, 100_000, bars)) if o.side == "buy"]
    assert bought == ["S00", "S01", "S04", "S05", "S06"]


def test_backtest_with_exposure_control_runs_and_reduces_exposure(tmp_path):
    make_market(tmp_path)
    composite.build_scores(tmp_path, horizon=5, window=120, min_history=40, min_stocks=50)
    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)
    base = backtest_report.run(tmp_path, "prior", DAYS[150], DAYS[-1], n=20, rebalance=5, costs=costs)
    ctrl = backtest_report.run(tmp_path, "prior", DAYS[150], DAYS[-1], n=20, rebalance=5, costs=costs,
                               exposure_mode="both", max_small_share=0.3)
    assert ctrl["exposure_mode"] == "both" and 0 < ctrl["exposure"]["mean_exposure"] <= 1
    assert ctrl["max_small_share"] == 0.3 and base["exposure_mode"] == "none"
