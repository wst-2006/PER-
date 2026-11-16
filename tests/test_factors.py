"""因子与评估测试：数值正确、不偷看未来、能识别出埋进去的信号。"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from perq.data import curated
from perq.data.store import RawStore
from perq.features import factors, labels
from perq.research import evaluate, stats

N_STOCKS, DAYS = 120, [d.strftime("%Y%m%d") for d in pd.bdate_range("2022-01-03", "2023-06-30")]


def make_market(root, seed=0, tweak_after=None):
    """合成市场：日收益 = −0.25 × 昨日收益 + 噪声（埋入短期反转）；小市值另有每日 +0.15% 的漂移。
    tweak_after：把该日期之后的收益整体改掉，用来检验因子不依赖未来数据。"""
    rng = np.random.default_rng(seed)
    store = RawStore(root)
    cal = pd.date_range("2020-01-01", "2023-12-31")
    store.write("trade_cal", "x", pd.DataFrame({"cal_date": cal.strftime("%Y%m%d"), "is_open": [int(d.weekday() < 5) for d in cal]}))
    codes = np.array([f"{i:06d}.SZ" for i in range(N_STOCKS)])
    store.write("stock_basic_L", "x", pd.DataFrame({"ts_code": codes, "name": "s", "market": "主板", "exchange": "SZSE",
                                                    "list_status": "L", "list_date": "20200102", "delist_date": None}))
    store.write("sw_member_801080_Y", "x", pd.DataFrame({
        "ts_code": codes, "l1_code": [f"80{i % 6}.SI" for i in range(N_STOCKS)], "l1_name": "行业", "l2_code": "x",
        "l2_name": "x", "in_date": "20200102", "out_date": None, "is_new": "Y"}))
    mv = np.exp(rng.normal(13, 1, N_STOCKS))               # 流通市值（万元）
    small = mv < np.median(mv)
    price = np.full(N_STOCKS, 10.0)
    prev_ret = np.zeros(N_STOCKS)
    for day in DAYS:
        noise = rng.normal(0, 0.02, N_STOCKS)
        ret = -0.25 * prev_ret + noise + np.where(small, 0.0015, 0.0)
        if tweak_after and day > tweak_after:
            ret = rng.normal(0.01, 0.03, N_STOCKS)
        ret = np.clip(ret, -0.095, 0.095)
        pre = price.copy()
        price = np.round(pre * (1 + ret), 4)
        frame = pd.DataFrame({"ts_code": codes, "trade_date": day, "open": pre, "high": np.maximum(pre, price),
                              "low": np.minimum(pre, price), "close": price, "pre_close": pre,
                              "pct_chg": (price / pre - 1) * 100, "vol": 1e5, "amount": 1e5})
        store.write("daily", day, frame)
        store.write("adj_factor", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "adj_factor": 1.0}))
        store.write("stk_limit", day, pd.DataFrame({"ts_code": codes, "trade_date": day,
                                                    "up_limit": np.round(pre * 1.1, 2), "down_limit": np.round(pre * 0.9, 2)}))
        store.write("daily_basic", day, pd.DataFrame({"ts_code": codes, "trade_date": day, "turnover_rate_f": 1.0 + rng.random(N_STOCKS),
                                                      "pe_ttm": 20.0, "pb": 2.0, "ps_ttm": 3.0, "dv_ttm": 1.0,
                                                      "total_mv": mv * 2, "circ_mv": mv * price / 10}))
        prev_ret = ret
    curated.build_all(root, memory_limit="1GB", threads=2)
    labels.build_labels(root, memory_limit="1GB", threads=2)
    return factors.build_factors(root, memory_limit="1GB", threads=2)


def read(root, what):
    return duckdb.sql(f"SELECT * FROM read_parquet('{(root / what).as_posix()}/*/*.parquet', hive_partitioning=true)").df()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    summary = make_market(root)
    return root, summary, read(root, "features/v1"), read(root, "curated/bars")


def test_factor_values_match_hand_calculation(world):
    _, summary, feats, bars = world
    code, day = "000007.SZ", DAYS[200]
    hist = bars[(bars.ts_code == code) & (bars.trade_date <= day)].sort_values("trade_date")
    row = feats[(feats.ts_code == code) & (feats.trade_date == day)].iloc[0]
    assert row["rev_5"] == pytest.approx(np.prod(1 + hist.ret.tail(5)) - 1, rel=1e-4)
    assert row["vol_20"] == pytest.approx(hist.ret.tail(20).std(ddof=1), rel=1e-4)
    assert row["max_20"] == pytest.approx(hist.ret.tail(20).max(), rel=1e-4)
    assert row["bias_20"] == pytest.approx(hist.adj_close.iloc[-1] / hist.adj_close.tail(20).mean() - 1, rel=1e-4)
    assert row["high_250"] == pytest.approx(hist.adj_close.iloc[-1] / hist.adj_high.tail(250).max(), rel=1e-4)
    assert row["bp"] == pytest.approx(0.5) and row["ln_mv"] == pytest.approx(np.log(hist.circ_mv.iloc[-1]), rel=1e-5)
    assert summary["factors"] == len(factors.FACTORS) and summary["coverage"]["rev_5"] > 0.95


def test_factors_need_enough_history(world):
    _, _, feats, _ = world
    early = feats[feats.trade_date == feats.trade_date.min()]
    # 进入股票池的第一天只有 15 根日线：5 日窗口的因子已有值，一年窗口的因子为空
    assert early["rev_5"].notna().all() and early["mom_250_20"].isna().all() and early["rev_20"].isna().all()
    later = feats[feats.trade_date == DAYS[40]]
    assert later["rev_20"].notna().all() and later["mom_250_20"].isna().all()


def test_factors_do_not_use_future_data(world, tmp_path):
    root, _, feats, _ = world
    cut = DAYS[250]
    make_market(tmp_path, tweak_after=cut)
    other = read(tmp_path, "features/v1")
    names = list(factors.FACTORS)
    a = feats[feats.trade_date <= cut].sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    b = other[other.trade_date <= cut].sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    assert len(a) == len(b) > 0
    np.testing.assert_allclose(a[names].to_numpy(dtype=float), b[names].to_numpy(dtype=float), rtol=1e-6, equal_nan=True)
    after_a = feats[feats.trade_date > cut]["rev_5"].mean()
    after_b = other[other.trade_date > cut]["rev_5"].mean()
    assert abs(after_a - after_b) > 0.01           # 截止日之后的数据确实被改了


def test_evaluation_finds_planted_reversal_and_size(world):
    root, _, _, _ = world
    report = evaluate.evaluate(root, horizons=(5,), min_stocks=50)
    assert report["status"] == "ok" and report["days"] > 100
    rev = report["factors"]["rev_5"]["horizons"]["5"]
    assert rev["ic_neutral"]["mean"] < -0.02 and rev["ic_neutral"]["t_nw"] < -3      # 埋入的反转被识别出来
    assert rev["top_minus_bottom_ew"] < 0 and rev["monotonicity"] < -0.5
    size = report["factors"]["ln_mv"]["horizons"]["5"]
    assert size["ic_raw"]["mean"] < 0 and size["ic_raw"]["t_nw"] < -2                # 小市值漂移
    # 估值类因子在合成市场里是常数：没有任何横截面差异，IC 应为空而不是伪造出一个数
    assert report["factors"]["bp"]["horizons"]["5"]["ic_neutral"]["n"] == 0
    # 对市值中性化之后，一个与市值无关的噪声因子不应显著
    noise = report["factors"]["turn_20"]["horizons"]["5"]["ic_neutral"]
    assert abs(noise["t_nw"]) < 3
    table = evaluate.summary_table(report, 5)
    top5 = [row["factor"] for row in table[:5]]
    assert "rev_5" in top5 and "ln_mv" in top5                     # 两个埋入的效应都排在最前面
    pairs = {frozenset((a, b)) for a, b, _ in report["highly_correlated_pairs"]}
    assert frozenset(("bias_20", "rev_20")) in pairs               # 同一信息的两种写法被标出来


def test_newey_west_and_neutralize():
    rng = np.random.default_rng(1)
    white = rng.normal(0.02, 1, 2000)
    plain_t = white.mean() / (white.std() / np.sqrt(len(white)))
    assert stats.newey_west_tstat(white, 5) == pytest.approx(plain_t, rel=0.15)
    # 正自相关的序列：朴素 t 值会高估，Newey-West 应当更小
    ar = np.zeros(2000)
    for i in range(1, 2000):
        ar[i] = 0.8 * ar[i - 1] + rng.normal(0.05, 1)
    naive = ar.mean() / (ar.std() / np.sqrt(len(ar)))
    assert abs(stats.newey_west_tstat(ar, 20)) < abs(naive) * 0.6
    # 中性化：因子 = 行业效应 + 市值效应 + 噪声，残差应与行业、市值都无关
    industry = np.array(["a", "b", "c"] * 200)
    size = rng.normal(0, 1, 600)
    z = (np.where(industry == "a", 2.0, 0.0) + 1.5 * size + rng.normal(0, 0.1, 600)).reshape(-1, 1)
    resid = stats.neutralize(z, industry, size).ravel()
    assert abs(np.corrcoef(resid, size)[0, 1]) < 1e-8 and abs(resid[industry == "a"].mean()) < 1e-8
    z2 = z.copy()
    z2[5, 0] = np.nan
    assert np.isnan(stats.neutralize(z2, industry, size)[5, 0])


def test_winsorize_and_rank_handle_outliers_and_ties():
    x = np.array([[1.0], [2.0], [3.0], [4.0], [1000.0], [np.nan]])
    z = stats.winsorize_zscore(x).ravel()
    assert np.isnan(z[5]) and z[4] < 3 and np.nanargmax(z) == 4
    r = stats.rank_columns(np.array([[1.0], [1.0], [3.0], [np.nan]])).ravel()
    assert r[0] == r[1] == 1.5 and r[2] == 3 and np.isnan(r[3])


def test_winsorize_keeps_sparse_discrete_factor():
    """多数为 0、少数为 1、2 的因子（涨停次数一类）不能被截尾成常数。"""
    x = np.array([[0.0]] * 90 + [[1.0]] * 8 + [[2.0]] * 2)
    z = stats.winsorize_zscore(x).ravel()
    assert np.nanstd(z) > 0.9 and z[-1] > z[-5] > z[0]
