"""回测引擎测试：每条规则一个手工构造的场景，预期结果用纸笔可以算出来。"""
from __future__ import annotations

import pandas as pd
import pytest

from perq.engine.costs import CostModel, lot_rule, round_buy_shares
from perq.engine.engine import BUY, SELL, Backtester, Order
from perq.engine.market import MarketData

DAYS = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109", "20240110"]
NOCOST = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.0, statutory_scale=0.0)


def frame(rows):
    """rows: (代码, 日期, 开盘, 收盘, 昨收) 加可选字段字典。涨跌停价按昨收的 ±10% 生成。"""
    out = []
    for code, day, open_, close, pre, *extra in rows:
        row = {"ts_code": code, "trade_date": day, "open": open_, "close": close,
               "up_limit": round(pre * 1.1, 2), "down_limit": round(pre * 0.9, 2), "adj_factor": 1.0, "vol": 1e7}
        row.update(extra[0] if extra else {})
        out.append(row)
    return pd.DataFrame(out)


class Script:
    """按日期给出预先写好的订单。"""

    def __init__(self, plan):
        self.plan, self.seen = plan, []

    def on_close(self, ctx):
        self.seen.append((ctx.date, round(ctx.nav, 2), {k: v.shares for k, v in ctx.positions.items()}))
        return self.plan.get(ctx.date, [])


def flat(code, days=DAYS, price=10.0, **extra):
    return [(code, d, price, price, price, dict(extra)) for d in days]


def run(rows, plan, costs=NOCOST, cash=100_000.0, days=DAYS, **kw):
    market = MarketData.from_frame(frame(rows), days=days, delist_dates=kw.pop("delist", None))
    strategy = Script(plan)
    result = Backtester(market, costs, cash=cash, **kw).run(strategy, days[0], days[-1])
    return result, strategy


# ---------------- 费用与数量规则 ----------------

def test_fee_schedule_changes_on_the_right_dates():
    c = CostModel(commission_rate=0.0003, min_commission=5.0)
    assert c.stamp_rate("20230825") == 0.001 and c.stamp_rate("20230828") == 0.0005
    assert c.transfer_rate("20220428") == 0.00002 and c.transfer_rate("20220429") == 0.00001
    assert c.commission(10_000) == 5.0 and c.commission(100_000) == pytest.approx(30.0)
    assert c.buy_fees("20240102", 100_000) == pytest.approx(30.0 + 1.0)
    assert c.sell_fees("20240102", 100_000) == pytest.approx(30.0 + 50.0 + 1.0)
    assert c.sell_fees("20230101", 100_000) == pytest.approx(30.0 + 100.0 + 1.0)


def test_lot_rules_by_board():
    assert lot_rule("主板") == (100, 100) and lot_rule("创业板") == (100, 100)
    assert round_buy_shares(399, "主板") == 300 and round_buy_shares(99, "主板") == 0
    assert round_buy_shares(399, "科创板") == 399 and round_buy_shares(199, "科创板") == 0
    assert round_buy_shares(150, "北交所") == 150 and round_buy_shares(99, "北交所") == 0


# ---------------- 成交 ----------------

def test_buy_executes_next_open_with_slippage_and_min_commission():
    rows = [("A", "20240102", 10.0, 10.0, 10.0), ("A", "20240103", 10.2, 10.5, 10.0), ("A", "20240104", 10.5, 10.4, 10.5)]
    costs = CostModel(commission_rate=0.00025, min_commission=5.0, slippage=0.001)
    result, _ = run(rows, {"20240102": [Order("A", BUY, 1000)]}, costs=costs, days=DAYS[:3])
    fill = next(f for f in result.fills if f.status == "filled")
    assert fill.date == "20240103" and fill.shares == 1000
    assert fill.price == pytest.approx(10.2 * 1.001)
    assert fill.fees == pytest.approx(5.0 + 10.2102 * 1000 * 0.00001)       # 佣金不足 5 元按 5 元收
    day2 = result.daily[1]
    assert day2["nav"] == pytest.approx(100_000 - fill.amount - fill.fees + 1000 * 10.5)
    assert result.daily[2]["nav"] == pytest.approx(day2["nav"] - 1000 * 0.1)


def test_buy_fails_when_open_is_limit_up_or_limit_missing_or_suspended():
    rows = (
        [("U", "20240102", 10.0, 10.0, 10.0), ("U", "20240103", 11.0, 11.0, 10.0)]                       # 开盘即涨停
        + [("M", "20240102", 10.0, 10.0, 10.0), ("M", "20240103", 10.0, 10.0, 10.0, {"limit_missing": True})]
        + [("S", "20240102", 10.0, 10.0, 10.0)]                                                         # 次日停牌
    )
    plan = {"20240102": [Order("U", BUY, 100), Order("M", BUY, 100), Order("S", BUY, 100)]}
    result, strategy = run(rows, plan, days=DAYS[:2])
    reasons = {f.ts_code: (f.status, f.reason) for f in result.fills}
    assert reasons == {"U": ("failed", "limit_up"), "M": ("failed", "limit_missing"), "S": ("failed", "suspended")}
    assert result.daily[-1]["nav"] == 100_000 and strategy.seen[-1][2] == {}


def test_buy_is_bounded_by_cash_lot_and_participation():
    rows = flat("A", DAYS[:2]) + flat("K", DAYS[:2], board="科创板") + flat("T", DAYS[:2], vol=30.0)
    plan = {"20240102": [Order("A", BUY, 20_000), Order("K", BUY, 150), Order("T", BUY, 5_000)]}
    result, strategy = run(rows, plan, cash=50_000.0, days=DAYS[:2], max_participation=0.02)
    got = {f.ts_code: (f.status, f.shares, f.reason) for f in result.fills}
    assert got["A"] == ("filled", 5000, "")                   # 现金只够 5000 股，按 100 股递减
    assert got["K"] == ("failed", 0.0, "lot")                 # 科创板最少 200 股
    assert got["T"] == ("failed", 0.0, "participation")       # 成交量 30 手，2% 上限只有 60 股
    assert result.daily[-1]["cash"] == pytest.approx(0.0)


def test_sell_is_carried_through_limit_down_and_suspension_until_filled():
    rows = [
        ("A", "20240102", 10.0, 10.0, 10.0), ("A", "20240103", 10.0, 10.0, 10.0),
        ("A", "20240104", 9.0, 9.0, 10.0),      # 一字跌停
        # 20240105 停牌，无日线
        ("A", "20240108", 8.1, 8.1, 9.0),       # 复牌再次一字跌停
        ("A", "20240109", 8.0, 8.2, 8.1),       # 打开
    ]
    plan = {"20240102": [Order("A", BUY, 1000)], "20240103": [Order("A", SELL, None, "stop")]}
    result, _ = run(rows, plan, days=DAYS[:6])
    sells = [(f.date, f.status, f.reason) for f in result.fills if f.side == SELL]
    assert sells == [("20240104", "carried", "limit_down"), ("20240105", "carried", "suspended"),
                     ("20240108", "carried", "limit_down"), ("20240109", "filled", "")]
    assert [d["pending_sells"] for d in result.daily] == [0, 0, 1, 1, 1, 0]
    assert result.daily[3]["nav"] == pytest.approx(100_000 - 10_000 + 9_000)    # 停牌日按最近收盘价估值
    assert result.daily[-1]["nav"] == pytest.approx(100_000 - 10_000 + 8_000)   # 最终以 8.0 卖出
    assert result.daily[-1]["positions"] == 0
    assert result.summary()["unfilled_by_reason"] == {"sell:carried:limit_down": 2, "sell:carried:suspended": 1}


def test_ex_rights_day_does_not_create_fake_loss():
    # 20240104 起 10 送 10：价格减半，复权因子翻倍
    rows = [("A", "20240102", 10.0, 10.0, 10.0), ("A", "20240103", 10.0, 10.0, 10.0),
            ("A", "20240104", 5.0, 5.1, 5.0, {"adj_factor": 2.0}), ("A", "20240105", 5.1, 5.1, 5.1, {"adj_factor": 2.0})]
    plan = {"20240102": [Order("A", BUY, 1000)], "20240104": [Order("A", SELL, None)]}
    result, strategy = run(rows, plan, days=DAYS[:4])
    assert strategy.seen[2][2] == {"A": 2000.0}
    assert result.daily[2]["nav"] == pytest.approx(100_000 + 2000 * 5.1 - 10_000)
    sell = [f for f in result.fills if f.side == SELL][0]
    assert sell.shares == 2000 and result.daily[-1]["cash"] == pytest.approx(100_000 + 200)


def test_sell_price_is_floored_at_limit_down_and_partial_sell_keeps_cost_basis():
    rows = [("A", "20240102", 10.0, 10.0, 10.0), ("A", "20240103", 10.0, 10.0, 10.0),
            ("A", "20240104", 9.005, 9.5, 10.0), ("A", "20240105", 9.5, 9.5, 9.5)]
    costs = CostModel(commission_rate=0.0, min_commission=0.0, slippage=0.01, statutory_scale=0.0)
    plan = {"20240102": [Order("A", BUY, 1000)], "20240103": [Order("A", SELL, 400)]}
    result, strategy = run(rows, plan, costs=costs, days=DAYS[:4])
    sell = [f for f in result.fills if f.side == SELL][0]
    assert sell.price == pytest.approx(9.0)                   # 9.005 × 0.99 低于跌停价 9.0，按跌停价成交
    assert strategy.seen[-1][2] == {"A": 600.0}


def test_delisted_position_is_written_off():
    rows = [("A", d, 10.0, 10.0, 10.0) for d in DAYS[:3]] + [("B", d, 5.0, 5.0, 5.0) for d in DAYS]
    plan = {"20240102": [Order("A", BUY, 1000)]}
    result, _ = run(rows, plan, delist={"A": "20240108"})
    forced = [f for f in result.fills if f.status == "forced"]
    assert [(f.date, f.ts_code, f.reason) for f in forced] == [("20240108", "A", "delisted")]
    assert result.daily[3]["nav"] == pytest.approx(100_000)   # 退市日之前仍按最后收盘价估值
    assert result.daily[4]["nav"] == pytest.approx(90_000)    # 退市日清零
    recovered, _ = run(rows, plan, delist={"A": "20240108"}, delist_recovery=0.3)
    assert recovered.daily[-1]["nav"] == pytest.approx(93_000)


def test_cash_and_positions_are_conserved():
    """任意一天：净值 = 现金 + 持仓市值；全程：期末净值 = 期初 + 持仓盈亏 − 费用。"""
    rows = [("A", d, 10 + i * 0.1, 10 + i * 0.1 + 0.05, 10 + (i - 1) * 0.1 + 0.05 if i else 10.0) for i, d in enumerate(DAYS)]
    costs = CostModel(commission_rate=0.0003, min_commission=5.0, slippage=0.001)
    plan = {"20240102": [Order("A", BUY, 3000)], "20240105": [Order("A", SELL, 1000)], "20240109": [Order("A", SELL, None)]}
    result, _ = run(rows, plan, costs=costs)
    fills = [f for f in result.fills if f.status == "filled"]
    buys = sum(f.amount for f in fills if f.side == BUY)
    sells = sum(f.amount for f in fills if f.side == SELL)
    fees = sum(f.fees for f in fills)
    assert result.daily[-1]["positions"] == 0
    assert result.daily[-1]["nav"] == pytest.approx(100_000 - buys + sells - fees)
    summary = result.summary()
    assert summary["fees"] == pytest.approx(fees) and summary["filled"] == 3
    assert summary["max_drawdown"] <= 0 and summary["end_nav"] == pytest.approx(result.daily[-1]["nav"])
