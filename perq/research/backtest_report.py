"""用引擎回测一种打分方案，并与股票池等权基准比较。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..data.curated import connect
from ..engine.costs import CostModel
from ..engine.engine import Backtester
from ..engine.market import MarketData
from ..portfolio.topn import TopN
from .composite import ScoreBook

DAYS_PER_YEAR = 244


def benchmark_returns(root: str | Path, start: str, end: str) -> dict[str, float]:
    """股票池等权基准：第 t 日的收益 = 前一交易日在股票池内的股票在第 t 日的平均收益（不计费用）。"""
    root = Path(root)
    con = connect(root, "1GB", 2)
    bars = f"read_parquet('{(root / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    try:
        rows = con.execute(f"""
            WITH b AS (SELECT trade_date, ret, lag(in_universe) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_in
                       FROM {bars} WHERE trade_date <= '{end}')
            SELECT trade_date, avg(ret) FROM b WHERE prev_in AND ret IS NOT NULL AND trade_date >= '{start}'
            GROUP BY 1 ORDER BY 1""").fetchall()
    finally:
        con.close()
    return {d: float(r) for d, r in rows}


def relative_metrics(dates: list[str], strat: np.ndarray, bench: np.ndarray) -> dict[str, Any]:
    n = len(strat)
    if n < 2:
        return {}
    excess = strat - bench
    wealth_s, wealth_b = np.cumprod(1 + strat), np.cumprod(1 + bench)
    rel = wealth_s / wealth_b

    def max_dd(curve: np.ndarray) -> float:
        return float((curve / np.maximum.accumulate(curve) - 1).min())

    years = np.array([d[:4] for d in dates])
    by_year = {}
    for y in sorted(set(years)):
        m = years == y
        by_year[y] = {"strategy": round(float(np.prod(1 + strat[m]) - 1), 4),
                      "benchmark": round(float(np.prod(1 + bench[m]) - 1), 4),
                      "excess": round(float(np.prod(1 + strat[m]) / np.prod(1 + bench[m]) - 1), 4)}
    return {
        "days": n,
        "annual_return": round(float(wealth_s[-1] ** (DAYS_PER_YEAR / n) - 1), 4),
        "benchmark_annual_return": round(float(wealth_b[-1] ** (DAYS_PER_YEAR / n) - 1), 4),
        "annual_excess": round(float(rel[-1] ** (DAYS_PER_YEAR / n) - 1), 4),
        "information_ratio": round(float(excess.mean() / excess.std() * np.sqrt(DAYS_PER_YEAR)), 3) if excess.std() > 0 else None,
        "max_drawdown": round(max_dd(wealth_s), 4),
        "benchmark_max_drawdown": round(max_dd(wealth_b), 4),
        "max_relative_drawdown": round(max_dd(rel), 4),
        "years_with_positive_excess": round(float(np.mean([v["excess"] > 0 for v in by_year.values()])), 3),
        "by_year": by_year,
    }


def run(root: str | Path, scheme: str, start: str, end: str, *, n: int = 30, rebalance: int = 5,
        buffer: float = 2.0, cash: float = 1_000_000.0, costs: CostModel | None = None,
        market: MarketData | None = None) -> dict[str, Any]:
    market = market or MarketData.from_curated(root)
    strategy = TopN(ScoreBook(root, scheme), n=n, rebalance=rebalance, buffer=buffer)
    result = Backtester(market, costs or CostModel(), cash=cash).run(strategy, start, end)
    bench = benchmark_returns(root, start, end)
    dates = [row["date"] for row in result.daily if row["date"] in bench]
    strat = np.array([row["ret"] for row in result.daily if row["date"] in bench])
    metrics = relative_metrics(dates, strat, np.array([bench[d] for d in dates]))
    summary = result.summary()
    metrics.update({
        "scheme": scheme, "n": n, "rebalance": rebalance, "buffer": buffer, "start": start, "end": end,
        "fees": round(summary.get("fees", 0.0), 2),
        "fees_share_of_start_nav_per_year": round(summary.get("fees", 0.0) / cash / max(len(dates), 1) * DAYS_PER_YEAR, 5),
        "annual_turnover_two_sided": round(summary.get("annual_turnover_two_sided") or 0.0, 2),
        "unfilled_by_reason": summary.get("unfilled_by_reason", {}),
        "average_positions": round(float(np.mean([row["positions"] for row in result.daily])), 1) if result.daily else 0,
    })
    metrics["nav_curve"] = [[row["date"], round(row["nav"], 2)] for row in result.daily[::5]]
    return metrics
