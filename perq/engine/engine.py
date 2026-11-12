"""按日推进的回测引擎。回测与每日出单走同一段逻辑。

时序：
    D 日收盘后   策略看到当日行情与持仓，给出订单
    D+1 日开盘   引擎撮合：先卖后买
    D+1 日收盘   按收盘价估值，策略再次决策

规则：
- 买入：当日无行情（停牌）、涨跌停价缺失、开盘即涨停，均买不进，订单作废（由策略次日重新决定）。
- 卖出：当日无行情或开盘即跌停，卖不出，订单自动顺延到下一个交易日，直到成交。
- 成交价：开盘价加减滑点，且不越过涨跌停价。
- 数量：买入按板块规则取整，受现金和成交量占比上限约束；卖出可一次卖出全部（含零股）。
- T+1：订单在前一日收盘后生成、当日开盘执行，当日买入的股票最早次日卖出。
- 除权除息：按复权因子的变化调整持股数，相当于送转股到账、现金分红再投资（不计红利税）。
- 停牌期间按最近收盘价估值。
- 退市：股票的最后一根日线之后到了退市日仍未卖出，按 delist_recovery 比例结算并记为强制平仓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .costs import CostModel, lot_rule, round_buy_shares
from .market import MarketData

BUY, SELL = "buy", "sell"


@dataclass
class Order:
    ts_code: str
    side: str
    shares: float | None = None      # 卖出时 None 表示全部卖出
    reason: str = ""


@dataclass
class Position:
    shares: float
    cost: float                      # 累计买入成本（含费用），按卖出比例递减
    entry_date: str
    last_close: float
    last_factor: float
    board: str | None = None


@dataclass
class Fill:
    date: str
    ts_code: str
    side: str
    status: str                      # filled / failed / carried / forced
    shares: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    fees: float = 0.0
    reason: str = ""
    order_reason: str = ""


@dataclass
class Context:
    date: str
    cash: float
    nav: float
    positions: dict[str, Position]
    bars: Any                         # 当日行情表（以代码为索引），可能为 None
    pending_sells: dict[str, Order]

    def bar(self, ts_code: str) -> Any:
        if self.bars is None or ts_code not in self.bars.index:
            return None
        return self.bars.loc[ts_code]


class Strategy(Protocol):
    def on_close(self, ctx: Context) -> list[Order]: ...


@dataclass
class Result:
    daily: list[dict[str, Any]] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)

    def nav_series(self) -> list[tuple[str, float]]:
        return [(row["date"], row["nav"]) for row in self.daily]

    def summary(self) -> dict[str, Any]:
        if not self.daily:
            return {}
        navs = [row["nav"] for row in self.daily]
        start = self.daily[0]["nav_start"]
        peak, max_dd = start, 0.0
        for nav in navs:
            peak = max(peak, nav)
            max_dd = min(max_dd, nav / peak - 1)
        n = len(navs)
        total = navs[-1] / start - 1
        by_reason: dict[str, int] = {}
        for fill in self.fills:
            if fill.status in ("failed", "carried", "forced"):
                key = f"{fill.side}:{fill.status}:{fill.reason}"
                by_reason[key] = by_reason.get(key, 0) + 1
        traded = sum(f.amount for f in self.fills if f.status == "filled")
        avg_nav = sum(navs) / n
        return {
            "days": n, "start_nav": start, "end_nav": navs[-1], "total_return": total,
            "annualized_return": (1 + total) ** (244 / n) - 1 if n > 0 and total > -1 else None,
            "max_drawdown": max_dd,
            "fees": sum(f.fees for f in self.fills),
            "traded_amount": traded,
            "annual_turnover_two_sided": traded / avg_nav * 244 / n if avg_nav else None,
            "filled": sum(1 for f in self.fills if f.status == "filled"),
            "unfilled_by_reason": by_reason,
        }


class Backtester:
    def __init__(self, market: MarketData, costs: CostModel | None = None, cash: float = 1_000_000.0,
                 max_participation: float = 0.02, delist_recovery: float = 0.0) -> None:
        self.market = market
        self.costs = costs or CostModel()
        self.initial_cash = float(cash)
        self.max_participation = max_participation
        self.delist_recovery = delist_recovery

    # ---------- 撮合 ----------
    def _sell(self, date: str, order: Order, bar: Any, positions: dict[str, Position], cash: float,
              fills: list[Fill], carried: dict[str, Order]) -> float:
        pos = positions.get(order.ts_code)
        if pos is None or pos.shares <= 0:
            fills.append(Fill(date, order.ts_code, SELL, "failed", reason="no_position", order_reason=order.reason))
            return cash
        if bar is None:
            carried[order.ts_code] = order
            fills.append(Fill(date, order.ts_code, SELL, "carried", reason="suspended", order_reason=order.reason))
            return cash
        if bool(bar["open_at_down"]):
            carried[order.ts_code] = order
            fills.append(Fill(date, order.ts_code, SELL, "carried", reason="limit_down", order_reason=order.reason))
            return cash
        shares = pos.shares if order.shares is None else min(float(order.shares), pos.shares)
        price = float(bar["open"]) * (1 - self.costs.slippage)
        down = bar["down_limit"]
        if down == down and down is not None:          # 非空
            price = max(price, float(down))
        amount = shares * price
        fees = self.costs.sell_fees(date, amount)
        cash += amount - fees
        pos.cost *= 1 - shares / pos.shares
        pos.shares -= shares
        if pos.shares <= 1e-9:
            del positions[order.ts_code]
        fills.append(Fill(date, order.ts_code, SELL, "filled", shares, price, amount, fees, order_reason=order.reason))
        return cash

    def _buy(self, date: str, order: Order, bar: Any, positions: dict[str, Position], cash: float,
             fills: list[Fill]) -> float:
        def fail(reason: str) -> float:
            fills.append(Fill(date, order.ts_code, BUY, "failed", reason=reason, order_reason=order.reason))
            return cash

        if bar is None:
            return fail("suspended")
        if bool(bar["limit_missing"]):
            return fail("limit_missing")
        if bool(bar["open_at_up"]):
            return fail("limit_up")
        board = bar["board"]
        price = min(float(bar["open"]) * (1 + self.costs.slippage), float(bar["up_limit"]))
        wanted = float(order.shares or 0)
        minimum, step = lot_rule(board)
        if round_buy_shares(wanted, board) < minimum:
            return fail("lot")
        cap = self.max_participation * float(bar["vol"]) * 100 if self.max_participation else wanted
        shares = round_buy_shares(min(wanted, cap), board)
        if shares < minimum:
            return fail("participation")
        while shares >= minimum and shares * price + self.costs.buy_fees(date, shares * price) > cash:
            shares -= step
        if shares < minimum:
            return fail("cash")
        amount = shares * price
        fees = self.costs.buy_fees(date, amount)
        cash -= amount + fees
        pos = positions.get(order.ts_code)
        if pos is None:
            positions[order.ts_code] = Position(float(shares), amount + fees, date, float(bar["close"]),
                                                float(bar["adj_factor"]), board)
        else:
            pos.shares += shares
            pos.cost += amount + fees
        fills.append(Fill(date, order.ts_code, BUY, "filled", shares, price, amount, fees, order_reason=order.reason))
        return cash

    # ---------- 主循环 ----------
    def run(self, strategy: Strategy, start: str, end: str) -> Result:
        result = Result()
        cash = self.initial_cash
        positions: dict[str, Position] = {}
        orders: list[Order] = []
        carried: dict[str, Order] = {}
        nav_prev = cash

        for date in self.market.days(start, end):
            bars = self.market.day(date)
            get = (lambda code: bars.loc[code] if bars is not None and code in bars.index else None)  # noqa: E731
            n_fills_before = len(result.fills)

            # 1. 除权除息：按复权因子变化调整持股数
            for code, pos in positions.items():
                bar = get(code)
                if bar is not None:
                    factor = float(bar["adj_factor"])
                    if factor == factor and pos.last_factor and abs(factor / pos.last_factor - 1) > 1e-12:
                        pos.shares *= factor / pos.last_factor
                    if factor == factor:
                        pos.last_factor = factor

            # 2. 撮合：先卖（含顺延的卖单），后买
            sells = {o.ts_code: o for o in carried.values()}
            sells.update({o.ts_code: o for o in orders if o.side == SELL})
            carried = {}
            for order in sells.values():
                cash = self._sell(date, order, get(order.ts_code), positions, cash, result.fills, carried)
            for order in (o for o in orders if o.side == BUY):
                cash = self._buy(date, order, get(order.ts_code), positions, cash, result.fills)

            # 3. 退市强制结算
            for code in list(positions):
                last_bar, delist = self.market.last_bar_date(code), self.market.delist_date(code)
                if get(code) is None and last_bar is not None and last_bar < date and delist is not None and delist <= date:
                    pos = positions.pop(code)
                    amount = pos.shares * pos.last_close * self.delist_recovery
                    cash += amount
                    carried.pop(code, None)
                    result.fills.append(Fill(date, code, SELL, "forced", pos.shares, pos.last_close * self.delist_recovery,
                                             amount, 0.0, reason="delisted"))

            # 4. 收盘估值
            value = 0.0
            for code, pos in positions.items():
                bar = get(code)
                if bar is not None:
                    pos.last_close = float(bar["close"])
                value += pos.shares * pos.last_close
            nav = cash + value
            today = result.fills[n_fills_before:]
            result.daily.append({
                "date": date, "nav": nav, "cash": cash, "positions": len(positions), "nav_start": self.initial_cash,
                "ret": nav / nav_prev - 1 if nav_prev else 0.0,
                "traded": sum(f.amount for f in today if f.status == "filled"),
                "fees": sum(f.fees for f in today),
                "pending_sells": len(carried),
            })
            nav_prev = nav

            # 5. 策略决策，订单在下一个交易日开盘执行
            ctx = Context(date, cash, nav, positions, bars, dict(carried))
            # 策略对同一只股票给出新的卖单时，会在下一日撮合时覆盖顺延的旧卖单
            orders = list(strategy.on_close(ctx) or [])
        return result
