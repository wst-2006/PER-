"""归因测试：一个只买小盘股的组合，在小盘股有漂移的市场里，超额应被"小市值"风格解释掉。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from perq.engine.costs import CostModel
from perq.engine.engine import BUY, SELL, Backtester, Order
from perq.engine.market import MarketData
from perq.research import attribution, backtest_report
from perq.research.stats import deflated_sharpe
from test_factors import DAYS, make_market


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("attr")
    make_market(root)
    return root


def test_style_returns_capture_planted_size_and_reversal(world):
    styles = attribution.style_returns(world, DAYS[130], DAYS[-1])
    assert list(styles.columns) == list(attribution.STYLES) and len(styles) > 200
    annual = styles.mean() * 244
    assert annual["size_small"] > 0.15 and annual["reversal"] > 0.05


def test_small_cap_portfolio_is_explained_by_size(world):
    market = MarketData.from_curated(world)
    feats = pd.read_parquet(sorted((world / "features" / "v1").glob("*/*.parquet"))[0], columns=["ts_code", "ln_mv"])
    smallest = set(feats.groupby("ts_code")["ln_mv"].mean().nsmallest(20).index)

    class HoldSmall:
        done = False

        def on_close(self, ctx):
            if self.done or ctx.date < DAYS[130]:
                return []
            self.done = True
            return [Order(code, BUY, ctx.nav * 0.9 / 20 / float(ctx.bars.loc[code, "close"])) for code in smallest]

    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)
    result = Backtester(market, costs, cash=1e7, max_participation=0).run(HoldSmall(), DAYS[130], DAYS[-1])
    bench = backtest_report.benchmark_returns(world, DAYS[131], DAYS[-1])
    dates = [r["date"] for r in result.daily if r["date"] in bench]
    excess = pd.Series([r["ret"] - bench[r["date"]] for r in result.daily if r["date"] in bench], index=dates)
    styles = attribution.style_returns(world, DAYS[131], DAYS[-1])
    reg = attribution.regress(excess, styles)
    assert reg["raw_excess_annual"] > 0.10                        # 只买小盘股确实有超额
    assert reg["betas"]["size_small"] > 0.3 and reg["beta_t_nw"]["size_small"] > 3
    assert abs(reg["alpha_annual"]) < reg["raw_excess_annual"] * 0.6    # 大部分被小市值风格解释掉
    exp = attribution.exposures(world, result.holdings)
    assert exp["average_percentile"]["ln_mv"] < 0.25 and exp["size_quintile_weight"]["Q1"] > 0.6


def test_regress_recovers_known_coefficients():
    rng = np.random.default_rng(3)
    idx = [f"d{i:04d}" for i in range(1500)]
    styles = pd.DataFrame(rng.normal(0, 0.005, (1500, 6)), index=idx, columns=list(attribution.STYLES))
    y = 0.0004 + 0.8 * styles["size_small"] - 0.3 * styles["value"] + rng.normal(0, 0.002, 1500)
    reg = attribution.regress(pd.Series(y, index=idx), styles)
    assert reg["betas"]["size_small"] == pytest.approx(0.8, abs=0.05)
    assert reg["betas"]["value"] == pytest.approx(-0.3, abs=0.05)
    assert reg["alpha_annual"] == pytest.approx(0.0004 * 244, abs=0.03) and reg["alpha_t_nw"] > 3


def test_deflated_sharpe_penalizes_many_trials():
    one = deflated_sharpe(0.06, 2200, 1, 0.0004)
    many = deflated_sharpe(0.06, 2200, 45, 0.0004)
    assert one["sr0"] == 0 and one["dsr"] > 0.99
    assert many["sr0"] > 0.02 and many["dsr"] < one["dsr"]
    noisy = deflated_sharpe(0.01, 2200, 45, 0.0004)
    assert noisy["dsr"] < 0.5                                     # 低于"运气上限"的夏普不可信


def test_size_filter_excludes_small_stocks(world):
    from perq.research import composite

    composite.build_scores(world, horizon=5, window=120, min_history=40, min_stocks=50)
    rep = backtest_report.run(world, "prior", DAYS[150], DAYS[-1], n=10, rebalance=5, min_size_pct=0.5,
                              keep_series=True)
    exp = attribution.exposures(world, rep["_series"]["holdings"])
    assert exp["size_quintile_weight"]["Q1"] == 0 and exp["size_quintile_weight"]["Q2"] < 0.2
