"""基线闭环测试：合成打分不偷看未来；回测在埋入了信号的市场里能跑赢基准，在纯噪声市场里跑不赢。"""
from __future__ import annotations

import numpy as np
import pytest

from perq.engine.costs import CostModel
from perq.engine.market import MarketData
from perq.research import backtest_report, composite
from test_factors import DAYS, make_market


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root = tmp_path_factory.mktemp("planted")
    make_market(root)
    summary = composite.build_scores(root, horizon=5, window=120, min_history=40, min_stocks=50)
    return root, summary


def test_scores_find_planted_signal_and_weights_adapt(planted):
    _, summary = planted
    for scheme in composite.SCHEMES:
        assert summary["schemes"][scheme]["ic_mean"] > 0.02, scheme
    last = summary["weights_sampled_every_20_days"][-1]
    assert last["matured_days"] == 120
    assert last["ic"]["rev_5"] < 0 and last["ic"]["ln_mv"] < 0          # 学到了反转和小市值的方向
    first = summary["weights_sampled_every_20_days"][0]
    assert first["matured_days"] == 0                                     # 起初没有任何已到期的 IC


def test_adaptive_weights_only_use_matured_ic(tmp_path):
    """把最后 30 天之后的收益全部改掉：在那之前的打分必须逐位相同。"""
    import pandas as pd

    cut = DAYS[-40]
    a, b = tmp_path / "a", tmp_path / "b"
    make_market(a)
    make_market(b, tweak_after=cut)
    for root in (a, b):
        composite.build_scores(root, horizon=5, window=120, min_history=40, min_stocks=50)
    for scheme in ("ic", "icir"):
        sa = composite.ScoreBook(a, scheme)
        sb = composite.ScoreBook(b, scheme)
        checked = 0
        for day in DAYS:
            if day > cut:
                break
            x, y = sa.day(day), sb.day(day)
            if x is None:
                continue
            pd.testing.assert_series_equal(x.sort_index(), y.sort_index())
            checked += 1
        assert checked > 100


def test_backtest_beats_benchmark_on_planted_market(planted):
    root, _ = planted
    market = MarketData.from_curated(root)
    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)
    # 埋入的是"隔日反转"，信号一天就衰减：每日调仓能吃到大部分，每周调仓只能吃到一小部分
    daily = backtest_report.run(root, "prior", DAYS[150], DAYS[-1], n=20, rebalance=1, buffer=1.0, costs=costs, market=market)
    weekly = backtest_report.run(root, "prior", DAYS[150], DAYS[-1], n=20, rebalance=5, costs=costs, market=market)
    assert daily["annual_excess"] > 0.30 and daily["information_ratio"] > 3
    assert 0 < weekly["annual_excess"] < daily["annual_excess"]
    assert daily["average_positions"] > 15 and daily["annual_turnover_two_sided"] > weekly["annual_turnover_two_sided"]
    with_costs = backtest_report.run(root, "prior", DAYS[150], DAYS[-1], n=20, rebalance=1, buffer=1.0, market=market)
    assert with_costs["annual_excess"] < daily["annual_excess"] and with_costs["fees"] > 0


def test_relative_metrics_arithmetic():
    dates = ["20240102", "20240103", "20250102", "20250103"]
    strat = np.array([0.01, 0.0, -0.02, 0.01])
    bench = np.array([0.0, 0.0, 0.0, 0.0])
    m = backtest_report.relative_metrics(dates, strat, bench)
    assert m["by_year"]["2024"]["excess"] == pytest.approx(0.01)
    assert m["by_year"]["2025"]["excess"] == pytest.approx(0.98 * 1.01 - 1, abs=1e-4)
    assert m["max_relative_drawdown"] == pytest.approx(-0.02, abs=1e-4)
    assert m["years_with_positive_excess"] == 0.5


def test_backtest_does_not_beat_benchmark_on_pure_noise(tmp_path):
    """没有任何信号的市场：打分 IC 与超额都应接近 0（检验整条链路没有偷看未来）。"""
    import test_factors

    original = test_factors.make_market

    def noise_market(root):
        import numpy as np
        import pandas as pd

        from perq.data import curated
        from perq.data.store import RawStore
        from perq.features import factors, labels

        rng = np.random.default_rng(7)
        store = RawStore(root)
        cal = pd.date_range("2020-01-01", "2023-12-31")
        store.write("trade_cal", "x", pd.DataFrame({"cal_date": cal.strftime("%Y%m%d"), "is_open": [int(d.weekday() < 5) for d in cal]}))
        codes = np.array([f"{i:06d}.SZ" for i in range(150)])
        store.write("stock_basic_L", "x", pd.DataFrame({"ts_code": codes, "name": "s", "market": "主板", "exchange": "SZSE",
                                                        "list_status": "L", "list_date": "20200102", "delist_date": None}))
        price = np.full(150, 10.0)
        mv = np.exp(rng.normal(13, 1, 150))
        for day in DAYS:
            pre = price.copy()
            price = np.round(pre * (1 + np.clip(rng.normal(0, 0.02, 150), -0.095, 0.095)), 4)
            store.write("daily", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "open": pre, "high": np.maximum(pre, price),
                                                    "low": np.minimum(pre, price), "close": price, "pre_close": pre,
                                                    "pct_chg": (price / pre - 1) * 100, "vol": 1e5, "amount": 1e5}))
            store.write("adj_factor", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "adj_factor": 1.0}))
            store.write("stk_limit", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "up_limit": np.round(pre * 1.1, 2),
                                                        "down_limit": np.round(pre * 0.9, 2)}))
            store.write("daily_basic", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "turnover_rate_f": 1 + rng.random(150),
                                                          "pe_ttm": 5 + 40 * rng.random(150), "pb": 1 + 4 * rng.random(150),
                                                          "ps_ttm": 1 + 4 * rng.random(150), "dv_ttm": rng.random(150), "circ_mv": mv}))
        curated.build_all(root, memory_limit="1GB", threads=2)
        labels.build_labels(root, memory_limit="1GB", threads=2)
        factors.build_factors(root, memory_limit="1GB", threads=2)

    assert original is not None
    noise_market(tmp_path)
    summary = composite.build_scores(tmp_path, horizon=5, window=120, min_history=40, min_stocks=50)
    for scheme in ("ic", "icir"):
        assert abs(summary["schemes"][scheme]["t_nw"]) < 3, summary["schemes"][scheme]
    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)
    report = backtest_report.run(tmp_path, "icir", DAYS[150], DAYS[-1], n=20, rebalance=5, costs=costs)
    assert abs(report["annual_excess"]) < 0.12 and abs(report["information_ratio"]) < 1.5
