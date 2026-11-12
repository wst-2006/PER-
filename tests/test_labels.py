"""标签测试，以及"标签与引擎口径一致"的交叉检查。"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from perq.data import curated
from perq.data.store import RawStore
from perq.engine.costs import CostModel
from perq.engine.engine import BUY, SELL, Backtester, Order
from perq.engine.market import MarketData
from perq.features import labels

DAYS = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-01", "2024-04-30")]


@pytest.fixture()
def built(tmp_path):
    """A：价格每天 +1%，2024-02-01 除权（价格减半、因子翻倍），2 月 19 至 23 日停牌。
    B：平盘，2024-01-10 开盘即涨停；3 月 1 日之后没有数据（退市）。"""
    store = RawStore(tmp_path)
    cal = pd.date_range("2023-01-01", "2024-12-31")
    store.write("trade_cal", "x", pd.DataFrame({"cal_date": cal.strftime("%Y%m%d"), "is_open": [int(d.weekday() < 5) for d in cal]}))
    store.write("stock_basic_L", "x", pd.DataFrame({"ts_code": ["A.SZ", "B.SZ"], "name": ["a", "b"], "market": ["主板"] * 2,
                                                    "exchange": ["SZSE"] * 2, "list_status": ["L"] * 2,
                                                    "list_date": ["20200101"] * 2, "delist_date": [None, None]}))
    daily, adj, lim = [], [], []
    price = 10.0
    for day in DAYS:
        if not ("20240219" <= day <= "20240223"):
            pre = price / 2 if day == "20240201" else price
            close = round(pre * 1.01, 4)
            factor = 2.0 if day >= "20240201" else 1.0
            daily.append(("A.SZ", day, pre, close, pre))
            adj.append(("A.SZ", day, factor))
            lim.append(("A.SZ", day, round(pre * 1.1, 2), round(pre * 0.9, 2)))
            price = close
        if day <= "20240301":
            open_ = 22.0 if day == "20240110" else 20.0
            daily.append(("B.SZ", day, open_, 20.0, 20.0))
            adj.append(("B.SZ", day, 1.0))
            lim.append(("B.SZ", day, 22.0, 18.0))
    d = pd.DataFrame(daily, columns=["ts_code", "trade_date", "open", "close", "pre_close"])
    d["high"], d["low"] = d[["open", "close"]].max(axis=1), d[["open", "close"]].min(axis=1)
    d["pct_chg"], d["vol"], d["amount"] = (d.close / d.pre_close - 1) * 100, 1e6, 1e6
    for name, frame in (("daily", d), ("adj_factor", pd.DataFrame(adj, columns=["ts_code", "trade_date", "adj_factor"])),
                        ("stk_limit", pd.DataFrame(lim, columns=["ts_code", "trade_date", "up_limit", "down_limit"]))):
        for day, part in frame.groupby("trade_date"):
            store.write(name, day, part.reset_index(drop=True))
    curated.build_all(tmp_path, memory_limit="1GB", threads=2)
    stats = labels.build_labels(tmp_path, memory_limit="1GB", threads=2)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW lab AS SELECT * FROM read_parquet('{(tmp_path / 'labels' / 'v1').as_posix()}/*/*.parquet', hive_partitioning=true)")
    return tmp_path, stats, con


def test_label_is_open_to_open_and_survives_ex_rights(built):
    _, _, con = built
    # 信号日 1 月 25 日：1 月 26 日开盘买入，5 个交易日后（2 月 2 日）开盘卖出，中间经过除权日。
    # A 每天开盘 = 昨收，收盘 = 开盘 × 1.01，所以 5 个交易日的开盘到开盘收益是 1.01^5 − 1
    fwd, ok, delay = con.execute("SELECT fwd_5, entry_ok, exit_delay_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240125'").fetchone()
    assert ok and delay == 0 and fwd == pytest.approx(1.01 ** 5 - 1, rel=1e-3)


def test_label_exit_is_delayed_by_suspension(built):
    _, _, con = built
    # 信号日 2 月 8 日：2 月 9 日买入，计划 2 月 16 日卖出；
    # 信号日 2 月 9 日：2 月 12 日买入，计划 2 月 19 日卖出，但 19 至 23 日停牌，顺延到 26 日
    on_time = con.execute("SELECT exit_delay_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240208'").fetchone()[0]
    fwd, delay = con.execute("SELECT fwd_5, exit_delay_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240209'").fetchone()
    assert on_time == 0 and delay == 5
    assert fwd == pytest.approx(1.01 ** 5 - 1, rel=1e-3)      # 停牌期间价格不动，仍然只涨了 5 根日线
    # 信号日是停牌前最后一天：次日无行情，买不进
    assert con.execute("SELECT entry_ok, fwd_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240216'").fetchone() == (False, None)


def test_label_marks_untradable_entry_and_terminal_exit(built):
    _, stats, con = built
    assert con.execute("SELECT entry_ok FROM lab WHERE ts_code='B.SZ' AND trade_date='20240109'").fetchone()[0] is False
    # B 在 3 月 1 日之后消失：2 月 28 日的信号 2 月 29 日买入，持有期内数据终止，按最后收盘价结算
    fwd, terminal = con.execute("SELECT fwd_5, terminal_5 FROM lab WHERE ts_code='B.SZ' AND trade_date='20240228'").fetchone()
    assert terminal and fwd == pytest.approx(0.0)
    # A 的数据一直到最后：最后几天的标签是未知，不是终止
    assert con.execute("SELECT fwd_5, terminal_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240426'").fetchone() == (None, False)
    assert stats["h5"]["terminal"] >= 1 and 0 < stats["entry_ok_rate"] < 1


def test_excess_label_is_demeaned_within_day(built):
    _, _, con = built
    worst = con.execute("""SELECT max(abs(m)) FROM (
        SELECT trade_date, avg(xs_5) AS m FROM lab WHERE in_universe AND entry_ok AND xs_5 IS NOT NULL GROUP BY 1)""").fetchone()[0]
    assert worst is None or worst < 1e-12


def test_engine_reproduces_label_when_costs_are_zero(built):
    """同一笔交易，引擎算出的收益必须等于标签。"""
    root, _, con = built
    market = MarketData.from_curated(root)

    class OneTrade:
        def on_close(self, ctx):
            if ctx.date == "20240125":
                return [Order("A.SZ", BUY, 1000)]
            if ctx.date == "20240201":           # 2 月 2 日开盘卖出 = 买入后第 5 个交易日
                return [Order("A.SZ", SELL, None)]
            return []

    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)
    result = Backtester(market, costs, cash=1_000_000.0, max_participation=0).run(OneTrade(), "20240102", "20240210")
    buy = next(f for f in result.fills if f.side == BUY)
    sell = next(f for f in result.fills if f.side == SELL)
    engine_return = sell.amount / buy.amount - 1
    label = con.execute("SELECT fwd_5 FROM lab WHERE ts_code='A.SZ' AND trade_date='20240125'").fetchone()[0]
    assert buy.date == "20240126" and sell.date == "20240202" and sell.shares == 2000
    assert engine_return == pytest.approx(label, rel=1e-9)
