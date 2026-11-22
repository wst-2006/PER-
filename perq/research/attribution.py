"""收益归因：组合的超额收益有多少来自风格暴露（小市值、低估值、低流动性、反转、低波动、低换手），
剩下多少是风格解释不了的选股收益。

风格收益：每天在股票池内按前一日的特征值分五组，取"预期好的一端减另一端"的次日等权收益差。
归因回归：组合每日超额收益（相对股票池等权）对六个风格收益做回归，截距即剔除风格后的选股收益。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..data.curated import connect
from .stats import newey_west_se

DAYS_PER_YEAR = 244
# 名称 → (因子列, 方向)：方向 −1 表示因子值小的一端是"多头"
STYLES = {
    "size_small": ("ln_mv", -1),
    "value": ("bp", 1),
    "illiquidity": ("amihud_20", 1),
    "reversal": ("rev_20", -1),
    "low_vol": ("vol_20", -1),
    "low_turnover": ("turn_20", -1),
}


def style_returns(root: str | Path, start: str, end: str, version: str = "v1") -> Any:
    """返回以日期为索引、每个风格一列的日收益表。第 t 日的值用第 t−1 日的分组、第 t 日的收益。"""
    root = Path(root)
    con = connect(root, "2GB", 4)
    feats = f"read_parquet('{(root / 'features' / version).as_posix()}/*/*.parquet', hive_partitioning=true)"
    bars = f"read_parquet('{(root / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    try:
        con.execute(f"""CREATE OR REPLACE TABLE nxt AS
            SELECT ts_code, trade_date, lead(trade_date) OVER w AS next_date, lead(ret) OVER w AS next_ret
            FROM {bars} WHERE trade_date BETWEEN '{start}' AND '{end}' OR trade_date < '{start}'
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)""")
        parts = []
        for name, (column, sign) in STYLES.items():
            parts.append(f"""
                SELECT '{name}' AS style, next_date AS trade_date,
                       {sign} * (avg(next_ret) FILTER (WHERE q = 5) - avg(next_ret) FILTER (WHERE q = 1)) AS r
                FROM (SELECT f.trade_date, n.next_date, n.next_ret,
                             ntile(5) OVER (PARTITION BY f.trade_date ORDER BY f.{column}) AS q
                      FROM {feats} f JOIN nxt n USING (ts_code, trade_date)
                      WHERE f.{column} IS NOT NULL AND isfinite(f.{column}) AND n.next_ret IS NOT NULL)
                WHERE next_date BETWEEN '{start}' AND '{end}'
                GROUP BY 1, 2""")
        frame = con.execute(" UNION ALL ".join(parts)).df()
    finally:
        con.close()
    return frame.pivot(index="trade_date", columns="style", values="r").sort_index()[list(STYLES)]


def regress(excess: Any, styles: Any, lags: int = 5) -> dict[str, Any]:
    """excess：以日期为索引的超额收益序列。返回截距（年化）、各风格系数及其 Newey-West t 值、R²。"""
    data = styles.join(excess.rename("y"), how="inner").dropna()
    if len(data) < 60:
        return {"days": int(len(data))}
    y = data["y"].to_numpy()
    x = np.column_stack([np.ones(len(data)), data[list(STYLES)].to_numpy()])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    r2 = 1 - resid.var() / y.var() if y.var() > 0 else None

    pinv = np.linalg.pinv(x)

    def t_of(j: int) -> float:
        # 系数 = (X'X)^-1 X' 的第 j 行与 y 的内积，其抽样误差 = 该行与残差内积的波动；
        # 把它写成 n 个观测值的均值后，用 Newey-West 标准误
        se = newey_west_se(pinv[j] * resid * len(y), lags)
        return float(beta[j] / se) if se and np.isfinite(se) else float("nan")

    return {
        "days": int(len(data)),
        "alpha_annual": round(float(beta[0] * DAYS_PER_YEAR), 4),
        "alpha_t_nw": round(t_of(0), 2),
        "raw_excess_annual": round(float(y.mean() * DAYS_PER_YEAR), 4),
        "betas": {name: round(float(b), 3) for name, b in zip(STYLES, beta[1:])},
        "beta_t_nw": {name: round(t_of(j + 1), 2) for j, name in enumerate(STYLES)},
        "r2": None if r2 is None else round(float(r2), 3),
        "style_annual_returns": {name: round(float(data[name].mean() * DAYS_PER_YEAR), 4) for name in STYLES},
    }


def exposures(root: str | Path, holdings: list[tuple[str, dict[str, float]]], every: int = 5,
              version: str = "v1") -> dict[str, Any]:
    """持仓在各特征上的加权平均百分位（0.5 为股票池平均水平），以及持仓在股票池市值五等分中的分布。"""
    import pandas as pd

    rows = [(d, c, w) for d, h in holdings[::every] for c, w in h.items() if w > 0]
    if not rows:
        return {}
    hold = pd.DataFrame(rows, columns=["trade_date", "ts_code", "weight"])
    days = sorted(hold["trade_date"].unique())
    columns = [c for c, _ in STYLES.values()]
    root = Path(root)
    con = connect(root, "2GB", 4)
    feats = f"read_parquet('{(root / 'features' / version).as_posix()}/*/*.parquet', hive_partitioning=true)"
    try:
        con.register("hold", hold)
        con.register("days", pd.DataFrame({"trade_date": days}))
        pct = ", ".join(f"percent_rank() OVER (PARTITION BY trade_date ORDER BY {c}) AS {c}" for c in columns)
        frame = con.execute(f"""
            WITH p AS (SELECT ts_code, trade_date, {pct}, ntile(5) OVER (PARTITION BY trade_date ORDER BY ln_mv) AS size_q
                       FROM {feats} WHERE trade_date IN (SELECT trade_date FROM days))
            SELECT h.weight, p.* FROM hold h JOIN p USING (ts_code, trade_date)""").df()
    finally:
        con.close()
    out: dict[str, Any] = {"sampled_days": len(days), "matched_weight_share": round(float(frame["weight"].sum() / hold["weight"].sum()), 4)}
    w = frame["weight"]
    out["average_percentile"] = {c: round(float((frame[c] * w).sum() / w.sum()), 3) for c in columns}
    out["size_quintile_weight"] = {f"Q{int(q)}": round(float(w[frame["size_q"] == q].sum() / w.sum()), 3) for q in range(1, 6)}
    by_year = {}
    for year, part in frame.groupby(frame["trade_date"].str[:4]):
        pw = part["weight"]
        by_year[year] = {"ln_mv": round(float((part["ln_mv"] * pw).sum() / pw.sum()), 3),
                         "smallest_quintile_weight": round(float(pw[part["size_q"] == 1].sum() / pw.sum()), 3)}
    out["size_by_year"] = by_year
    return out
