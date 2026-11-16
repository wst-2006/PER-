"""第一批因子（v1）：只用日线主表就能算出的量价、流动性、估值类因子。

时点：每个因子在 D 日的取值只用到 D 日及以前的数据；标签从 D+1 开盘算起。
窗口按"有成交的交易日"计数，停牌日不占位置。窗口内有效日数不足 minp 时取空值。

每个因子登记：所属信息族、先验方向（+1 表示值越大预期收益越高）、一句话逻辑。
先验方向只用于报告里对照，评估时不会用它翻转符号。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..data.curated import connect

VERSION = "v1"
WINDOWS = {5: 4, 20: 15, 60: 45, 250: 180}       # 窗口长度 → 最少有效日数


def _w(n: int) -> str:
    return f"w{n}"


def _guard(expr: str, n: int, on: str = "ret") -> str:
    return f"CASE WHEN count({on}) OVER {_w(n)} >= {WINDOWS[n]} THEN {expr} END"


def _cum(n: int) -> str:
    return f"exp(sum(ln(1 + greatest(ret, -0.99))) OVER {_w(n)})"


# 名称 → (表达式, 信息族, 先验方向, 逻辑)
FACTORS: dict[str, tuple[str, str, int, str]] = {
    "rev_5": (_guard(f"{_cum(5)} - 1", 5), "反转", -1, "散户主导下的短期过度反应"),
    "rev_20": (_guard(f"{_cum(20)} - 1", 20), "反转", -1, "一个月反转"),
    "mom_60": (_guard(f"{_cum(60)} - 1", 60), "动量", 0, "三个月价格趋势，A 股方向不确定"),
    "mom_250_20": (_guard(f"{_cum(250)} / {_cum(20)} - 1", 250), "动量", 1, "剔除最近一个月的一年动量"),
    "vol_20": (_guard(f"stddev_samp(ret) OVER {_w(20)}", 20), "波动", -1, "低波动异象"),
    "vol_60": (_guard(f"stddev_samp(ret) OVER {_w(60)}", 60), "波动", -1, "低波动异象"),
    "downvol_20": (_guard(f"sqrt(avg(CASE WHEN ret < 0 THEN ret * ret ELSE 0 END) OVER {_w(20)})", 20), "波动", -1, "下行波动"),
    "skew_20": (_guard(f"skewness(ret) OVER {_w(20)}", 20), "彩票偏好", -1, "正偏度股票被高估"),
    "max_20": (_guard(f"max(ret) OVER {_w(20)}", 20), "彩票偏好", -1, "近一个月最大单日涨幅"),
    "turn_20": (_guard(f"avg(turnover_rate_f) OVER {_w(20)}", 20, "turnover_rate_f"), "流动性", -1, "高换手后收益低"),
    "turn_5_60": (_guard(f"avg(turnover_rate_f) OVER {_w(5)} / NULLIF(avg(turnover_rate_f) OVER {_w(60)}, 0)", 60, "turnover_rate_f"),
                  "流动性", -1, "换手异常放大"),
    "amihud_20": (_guard(f"avg(abs(ret) / NULLIF(amount_yuan, 0)) OVER {_w(20)} * 1e8", 20), "流动性", 1, "非流动性溢价"),
    "amt_cv_20": (_guard(f"stddev_samp(amount_yuan) OVER {_w(20)} / NULLIF(avg(amount_yuan) OVER {_w(20)}, 0)", 20),
                  "流动性", -1, "成交额不稳定"),
    "amp_20": (_guard(f"avg((high - low) / NULLIF(pre_close, 0)) OVER {_w(20)}", 20), "波动", -1, "日内振幅"),
    "overnight_20": (_guard(f"sum(ln(open / NULLIF(pre_close, 0))) OVER {_w(20)}", 20), "隔夜与日内", -1, "隔夜收益累计"),
    "intraday_20": (_guard(f"sum(ln(close / NULLIF(open, 0))) OVER {_w(20)}", 20), "隔夜与日内", -1, "日内收益累计"),
    "bias_20": (_guard(f"adj_close / NULLIF(avg(adj_close) OVER {_w(20)}, 0) - 1", 20), "反转", -1, "价格偏离 20 日均线"),
    "high_250": (_guard(f"adj_close / NULLIF(max(adj_high) OVER {_w(250)}, 0)", 250), "锚定", 1, "距一年高点的距离"),
    "limitup_20": (_guard(f"sum(close_at_up::INT) OVER {_w(20)}", 20), "涨停生态", -1, "近一个月涨停次数"),
    "break_20": (_guard(f"sum((touched_up AND NOT close_at_up)::INT) OVER {_w(20)}", 20), "涨停生态", -1, "近一个月炸板次数"),
    "pv_corr_20": (_guard(f"corr(adj_close, vol) OVER {_w(20)}", 20), "量价", -1, "价量同向（放量上涨）"),
    "ep_ttm": ("1 / NULLIF(pe_ttm, 0)", "估值", 1, "盈利收益率；亏损公司为空"),
    "bp": ("1 / NULLIF(pb, 0)", "估值", 1, "账面市值比"),
    "sp_ttm": ("1 / NULLIF(ps_ttm, 0)", "估值", 1, "销售收益率"),
    "dv_ttm": ("dv_ttm", "估值", 1, "股息率"),
    "ln_mv": ("ln(NULLIF(circ_mv, 0))", "规模", -1, "流通市值对数"),
}


def build_factors(root: str | Path, memory_limit: str = "2GB", threads: int = 4, version: str = VERSION) -> dict[str, Any]:
    root = Path(root)
    con = connect(root, memory_limit, threads)
    bars = f"read_parquet('{(root / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    windows = ", ".join(
        f"{_w(n)} AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)"
        for n in WINDOWS)
    exprs = ",\n".join(f"CAST({expr} AS FLOAT) AS {name}" for name, (expr, *_rest) in FACTORS.items())
    target = root / "features" / version
    try:
        # 先在全部日线上算窗口（停牌前后的历史都要用到），再只保留股票池内的行
        con.execute(f"""
            CREATE OR REPLACE TABLE f AS
            SELECT ts_code, trade_date, in_universe, sw_l1, CAST(circ_mv AS FLOAT) AS circ_mv,
            {exprs}
            FROM {bars}
            WINDOW {windows}""")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"""
            COPY (SELECT * EXCLUDE (in_universe), substr(trade_date, 1, 4) AS year FROM f WHERE in_universe)
            TO '{target.as_posix()}' (FORMAT PARQUET, PARTITION_BY (year))""")
        rows, first, last = con.execute("SELECT count(*), min(trade_date), max(trade_date) FROM f WHERE in_universe").fetchone()
        coverage = con.execute(
            "SELECT " + ", ".join(f"avg(({name} IS NOT NULL AND isfinite({name}))::INT)" for name in FACTORS)
            + " FROM f WHERE in_universe").fetchone()
    finally:
        con.close()
    return {"version": version, "rows": rows, "first_date": first, "last_date": last, "factors": len(FACTORS),
            "coverage": {name: round(c, 4) for name, c in zip(FACTORS, coverage)}}
