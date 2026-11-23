"""第二批因子测试：每一类数据的数值、时点（公告日当天不可用、次一交易日起可用、过期作废）、
财务报表的单季还原、滚动十二个月与修订版本的生效时间。"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from perq.data import curated
from perq.data.store import RawStore
from perq.features import factors_v2, registry
from test_factors import DAYS, make_market

A = "000000.SZ"


def fin_rows(rows, fields):
    out = []
    for end, avail, flag, values in rows:
        out.append({"ts_code": A, "end_date": end, "ann_date": avail, "f_ann_date": avail, "report_type": "1",
                    "update_flag": flag, **dict(zip(fields, values))})
    return pd.DataFrame(out)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("v2")
    make_market(root)
    store = RawStore(root)
    codes = [f"{i:06d}.SZ" for i in range(120)]
    for day in DAYS:
        store.write("moneyflow", day, pd.DataFrame({
            "ts_code": codes, "trade_date": day, "buy_sm_amount": 5.0, "sell_sm_amount": 5.0, "buy_md_amount": 0.0,
            "sell_md_amount": 0.0, "buy_lg_amount": 3.0, "sell_lg_amount": 1.0, "buy_elg_amount": 0.0, "sell_elg_amount": 0.0}))
    margin = [{"ts_code": A, "trade_date": d, "rzye": 1e8 * 1.01 ** i, "rzmre": 1e7} for i, d in enumerate(DAYS)]
    for row in margin:
        store.write("margin_detail", row["trade_date"], pd.DataFrame([row]))
    bars = duckdb.sql(f"SELECT trade_date, close FROM read_parquet('{(root / 'curated' / 'bars').as_posix()}/*/*.parquet', "
                      f"hive_partitioning=true) WHERE ts_code = '{A}'").df().set_index("trade_date")["close"]
    for day in DAYS[:300]:
        store.write("cyq_perf", day, pd.DataFrame([{"ts_code": A, "trade_date": day, "winner_rate": 60.0,
                                                    "weight_avg": bars[day] / 1.2, "cost_15pct": 8.0, "cost_50pct": 10.0,
                                                    "cost_85pct": 12.0}]))
    store.write("stk_holdernumber", "x", pd.DataFrame({"ts_code": [A, A], "ann_date": ["20220420", "20220810"],
                                                       "end_date": ["20220331", "20220630"], "holder_num": [1000.0, 900.0]}))
    store.write("stk_holdertrade", "x", pd.DataFrame({"ts_code": [A], "ann_date": ["20220915"], "holder_name": ["x"],
                                                      "in_de": ["IN"], "change_vol": [1.0], "change_ratio": [2.0]}))
    store.write("repurchase", "x", pd.DataFrame({"ts_code": [A], "ann_date": ["20221010"], "proc": ["实施"], "end_date": [None]}))
    store.write("share_float", "x", pd.DataFrame({"ts_code": [A], "ann_date": ["20220801"], "float_date": ["20220901"],
                                                  "float_ratio": [5.0], "holder_name": ["x"], "share_type": ["x"]}))
    store.write("report_rc", "x", pd.DataFrame({
        "ts_code": [A, A], "report_date": ["20220510", "20220620"], "org_name": ["a", "b"], "author_name": ["a", "b"],
        "quarter": ["2022Q4", "2022Q4"], "report_title": ["t1", "t2"], "eps": [1.0, 1.2]}))
    store.write("forecast_vip", "x", pd.DataFrame({"ts_code": [A], "ann_date": ["20220715"], "end_date": ["20220630"],
                                                   "p_change_min": [50.0], "p_change_max": [70.0]}))
    inc = fin_rows([("20210331", "20210425", "1", (10, 100, 60)), ("20210630", "20210825", "1", (25, 210, 125)),
                    ("20210930", "20211025", "1", (36, 320, 190)), ("20211231", "20220325", "1", (50, 440, 260)),
                    ("20220331", "20220425", "1", (14, 120, 70)), ("20220630", "20220825", "1", (30, 250, 150)),
                    ("20220331", "20220826", "1", (12, 120, 70))],                    # 一季报更正
                   ["n_income_attr_p", "revenue", "oper_cost"])
    cf = fin_rows([("20210331", "20210425", "1", (5,)), ("20210630", "20210825", "1", (12,)), ("20210930", "20211025", "1", (20,)),
                   ("20211231", "20220325", "1", (40,)), ("20220331", "20220425", "1", (8,)), ("20220630", "20220825", "1", (20,))],
                  ["n_cashflow_act"])
    bs = fin_rows([(e, a, "1", (1000.0, 400.0, eq)) for e, a, eq in (
        ("20210331", "20210425", 600.0), ("20210630", "20210825", 600.0), ("20210930", "20211025", 600.0),
        ("20211231", "20220325", 600.0), ("20220331", "20220425", 650.0), ("20220630", "20220825", 660.0))],
        ["total_assets", "total_liab", "total_hldr_eqy_exc_min_int"])
    for name, frame in (("income_vip_rt1", inc), ("cashflow_vip_rt1", cf), ("balancesheet_vip_rt1", bs)):
        for period, part in frame.groupby("end_date"):
            store.write(name, period, part.reset_index(drop=True))
    curated.build_all(root, memory_limit="1GB", threads=2)
    summary = factors_v2.build_factors_v2(root, memory_limit="1GB", threads=2)
    frame = duckdb.sql(f"SELECT * FROM read_parquet('{(root / 'features' / 'v2').as_posix()}/*/*.parquet', hive_partitioning=true)").df()
    return root, summary, frame.set_index(["ts_code", "trade_date"]).sort_index(), bars


def at(frame, day, column, code=A):
    return frame.loc[(code, day), column]


def test_summary_and_daily_sources(built):
    _, summary, frame, _ = built
    assert summary["missing_sources"] == [] and summary["factors"] == len(factors_v2.FACTORS_V2)
    assert at(frame, DAYS[100], "mf_big_20") == pytest.approx(2 / 14, rel=1e-5)
    assert at(frame, DAYS[100], "mf_small_20") == pytest.approx(0.0, abs=1e-7)
    assert at(frame, DAYS[100], "margin_rz_chg_20") == pytest.approx(1.01 ** 19 - 1, rel=1e-4)
    assert at(frame, DAYS[100], "margin_buy_share_20") == pytest.approx(0.1, rel=1e-4)
    assert at(frame, DAYS[100], "chip_winner") == pytest.approx(0.6) and at(frame, DAYS[100], "chip_cgo") == pytest.approx(0.2, rel=1e-4)
    assert at(frame, DAYS[100], "chip_conc") == pytest.approx(0.4, rel=1e-5)
    assert np.isnan(at(frame, DAYS[100], "margin_rz_chg_20", code="000001.SZ"))          # 非融资标的为空


def test_announcements_are_usable_only_after_announcement_day(built):
    _, _, frame, _ = built
    assert np.isnan(at(frame, "20220810", "holder_chg")) and at(frame, "20220811", "holder_chg") == pytest.approx(-0.1)
    assert np.isnan(at(frame, "20220421", "holder_chg"))           # 第一期没有上期可比
    assert at(frame, "20220915", "insider_net_90") == 0 and at(frame, "20220916", "insider_net_90") == pytest.approx(0.02)
    assert at(frame, "20221214", "insider_net_90") == pytest.approx(0.02) and at(frame, "20221216", "insider_net_90") == 0
    assert at(frame, "20221010", "repurchase_60") == 0 and at(frame, "20221011", "repurchase_60") == 1.0
    assert at(frame, "20221212", "repurchase_60") == 0
    assert at(frame, "20220801", "unlock_30") == 0
    assert at(frame, "20220815", "unlock_30") == pytest.approx(0.05) and at(frame, "20220901", "unlock_30") == 0
    # 事件类因子"没有发生"记 0：其他股票整段都是 0
    assert at(frame, "20220815", "repurchase_60", code="000001.SZ") == 0
    assert np.isnan(at(frame, "20220715", "fc_pchg")) and at(frame, "20220718", "fc_pchg") == pytest.approx(0.6)
    assert np.isnan(at(frame, "20221125", "fc_pchg"))                                # 超过 120 天作废


def test_analyst_windows(built):
    _, _, frame, bars = built
    day = "20220630"
    assert at(frame, day, "an_cov_90") == pytest.approx(np.log(3))
    assert at(frame, day, "an_rev_eps") == pytest.approx((1.2 - 1.0) / bars[day], rel=1e-4)
    assert at(frame, day, "an_ep_fy") == pytest.approx(1.1 / bars[day], rel=1e-4)
    assert np.isnan(at(frame, "20220620", "an_rev_eps"))           # 第二篇研报次日才可用，此前近 30 天没有研报
    assert at(frame, "20230105", "an_cov_90") == pytest.approx(0.0)  # 跨年后当年口径重新开始


def test_financials_single_quarter_ttm_and_revision(built):
    _, _, frame, _ = built
    assert at(frame, "20220425", "roe_ttm") == pytest.approx(50 / 600, rel=1e-4)    # 一季报公布当天仍用上年年报
    d = "20220505"                                  # 2022 年一季报已公布
    assert at(frame, d, "roe_ttm") == pytest.approx(54 / 625, rel=1e-4)
    assert at(frame, d, "gpm_ttm") == pytest.approx(190 / 460, rel=1e-4)
    assert at(frame, d, "ni_yoy_q") == pytest.approx(0.4, rel=1e-4) and at(frame, d, "rev_yoy_q") == pytest.approx(0.2, rel=1e-4)
    assert at(frame, d, "accrual") == pytest.approx((54 - 43) / 1000, rel=1e-4)
    assert at(frame, d, "asset_growth") == pytest.approx(0.0, abs=1e-6) and at(frame, d, "leverage") == pytest.approx(0.4)
    # 8 月 26 日：半年报已可用，一季报更正当天公布、还不可用 → 二季度单季 = 30 − 14
    assert at(frame, "20220826", "ni_yoy_q") == pytest.approx((16 - 15) / 15, rel=1e-4)
    # 8 月 29 日：更正生效 → 二季度单季 = 30 − 12
    assert at(frame, "20220829", "ni_yoy_q") == pytest.approx((18 - 15) / 15, rel=1e-4)
    assert at(frame, "20220829", "roe_ttm") == pytest.approx(55 / 630, rel=1e-4)


def test_registry_merges_versions(built):
    root, _, _, _ = built
    frame = registry.read_year(root, "2022", ("v1", "v2"))
    assert {"rev_5", "roe_ttm", "sw_l1", "circ_mv"} <= set(frame.columns)
    assert len(registry.meta(("v1", "v2"))) == 26 + len(factors_v2.FACTORS_V2)


def test_combined_panel_and_model_run(built):
    from perq.research import gbm, panel

    root, _, _, _ = built
    info = panel.build_panel(root, min_stocks=50, version="v1+v2")
    assert info["factors"] == 26 + len(factors_v2.FACTORS_V2) and info["rows"] > 0
    frame = panel.load_panel(root, DAYS[200], DAYS[205], "v1+v2")
    assert {"rev_5", "roe_ttm", "mf_big_20"} <= set(frame.columns)
    summary = gbm.walk_forward(root, "combo", objective="reg", horizon=5, window=None, retrain_every=42, day_step=2,
                               rounds=30, min_train_days=60, start=DAYS[200], end=DAYS[-1], threads=2, version="v1+v2")
    assert summary["retrains"] >= 3 and "mf_big_20" in summary["feature_importance_gain_share"]
