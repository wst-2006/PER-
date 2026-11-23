"""基线组合：每隔 rebalance 个交易日，持有打分最高的 n 只股票，等金额。

换手缓冲：已持有的股票只要排名仍在前 n × buffer 以内就继续持有，只用新股票补足空位。
已持有的股票离开股票池（例如戴帽 ST）时，无论排名都卖出。
不在调仓日时不做任何交易（顺延中的卖单由引擎自动处理）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..engine.engine import BUY, SELL, Context, Order


@dataclass
class TopN:
    scores: Any                      # 有 day(date) 方法，返回以代码为索引的打分序列
    n: int = 30
    rebalance: int = 5
    buffer: float = 2.0
    invest_ratio: float = 0.98       # 留一点现金付费用
    min_size_pct: float = 0.0        # 只在股票池里流通市值高于这个分位数的股票中选（0 表示不限制）
    max_small_share: float = 1.0     # 市值最小 20% 的股票最多占持股数的比例（1 表示不限制）
    exposure: Any = None             # 可选：exposure(date) 返回 0 到 1 的目标仓位，只能用到 date 收盘及以前的信息
    resize_tolerance: float = 0.25   # 已持有股票的市值偏离目标超过这个比例时才调整，减少零碎交易
    _count: int = field(default=0, init=False)
    log: list[dict[str, Any]] = field(default_factory=list, init=False)

    def on_close(self, ctx: Context) -> list[Order]:
        self._count += 1
        if (self._count - 1) % self.rebalance != 0 or ctx.bars is None:
            return []
        score = self.scores.day(ctx.date)
        if score is None or len(score) == 0:
            return []
        bars = ctx.bars
        eligible = score.dropna()
        pool = bars[bars["in_universe"].astype(bool)]
        small_cut = pool["circ_mv"].quantile(0.2) if len(pool) else 0.0
        if self.min_size_pct > 0:
            pool = pool[pool["circ_mv"] >= pool["circ_mv"].quantile(self.min_size_pct)]
        eligible = eligible[eligible.index.isin(pool.index)]
        ranked = eligible.sort_values(ascending=False)
        rank = {code: i for i, code in enumerate(ranked.index)}
        keep_limit = int(self.n * self.buffer)
        weight = 1.0 if self.exposure is None else float(min(max(self.exposure(ctx.date), 0.0), 1.0))
        target_value = ctx.nav * self.invest_ratio * weight / self.n

        def is_small(code: str) -> bool:
            return code in bars.index and float(bars.loc[code, "circ_mv"]) < small_cut

        orders: list[Order] = []
        kept: list[str] = []
        for code in ctx.positions:
            if code in ctx.pending_sells:
                continue
            if rank.get(code, 10**9) < keep_limit:
                kept.append(code)
            else:
                orders.append(Order(code, SELL, None, "out_of_rank" if code in rank else "left_universe"))

        # 仓位变化：已持有的股票按目标市值增减
        for code in kept:
            pos = ctx.positions[code]
            if code not in bars.index:
                continue
            close = float(bars.loc[code, "close"])
            value = pos.shares * close
            if target_value <= 0:
                orders.append(Order(code, SELL, None, "exposure_zero"))
            elif value > target_value * (1 + self.resize_tolerance):
                orders.append(Order(code, SELL, (value - target_value) / close, "trim"))
            elif value < target_value * (1 - self.resize_tolerance) and close > 0:
                orders.append(Order(code, BUY, (target_value - value) / close, "top_up"))

        slots = self.n - len(kept) if target_value > 0 else 0
        small_limit = int(self.n * self.max_small_share)
        small_count = sum(1 for code in kept if is_small(code))
        added = 0
        for code in ranked.index:
            if added >= slots:
                break
            if code in ctx.positions or code in ctx.pending_sells:
                continue
            if is_small(code):
                if small_count >= small_limit:
                    continue
                small_count += 1
            close = float(bars.loc[code, "close"])
            if close > 0:
                orders.append(Order(code, BUY, target_value / close, "new_top"))
                added += 1
        self.log.append({"date": ctx.date, "kept": len(kept), "sells": sum(o.side == SELL for o in orders),
                         "buys": added, "candidates": len(ranked), "exposure": weight, "small": small_count})
        return orders
