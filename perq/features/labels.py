"""标签：D 日收盘后出信号，D+1 开盘买入，持有 h 个交易日后的开盘卖出。

与引擎的时序完全一致：
    入场价 = D+1 的复权开盘价
    出场价 = D+1+h 的复权开盘价；该日停牌则顺延到复牌后的第一个开盘
对每个持有期 h 输出：
    fwd_h          持有期收益
    xs_h           减去当日"股票池内且可入场"股票的等权平均后的超额收益
    exit_delay_h   出场比计划晚了几个交易日（停牌顺延）
    exit_blocked_h 计划出场日开盘即跌停（实际还会再顺延，标签未计入）
    terminal_h     股票在持有期内退市或数据终止，按最后收盘价结算
另有 entry_ok：D+1 有行情、涨跌停价不缺、开盘未涨停，即实际买得进。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..data.curated import connect

HORIZONS = (5, 10, 20)
TERMINAL_GAP = 20      # 股票最后一根日线距离全市场最后交易日超过这么多天，视为已经消失


def build_labels(root: str | Path, horizons: tuple[int, ...] = HORIZONS, version: str = "v1",
                 memory_limit: str = "2GB", threads: int = 4) -> dict[str, Any]:
    root = Path(root)
    con = connect(root, memory_limit, threads)
    bars = f"read_parquet('{(root / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE b AS
            SELECT ts_code, trade_date, cal_idx, adj_open, adj_close, open_at_up, open_at_down, limit_missing, in_universe
            FROM {bars}""")
        con.execute("""
            CREATE OR REPLACE TABLE last_bar AS
            SELECT ts_code, max(cal_idx) AS last_idx, arg_max(adj_close, cal_idx) AS last_adj_close FROM b GROUP BY 1""")
        global_last = con.execute("SELECT max(cal_idx) FROM b").fetchone()[0]
        con.execute("""
            CREATE OR REPLACE TABLE base AS
            SELECT s.ts_code, s.trade_date, s.cal_idx, s.in_universe,
                   e.adj_open AS entry_px,
                   (e.ts_code IS NOT NULL AND NOT e.open_at_up AND NOT e.limit_missing) AS entry_ok
            FROM b s LEFT JOIN b e ON e.ts_code = s.ts_code AND e.cal_idx = s.cal_idx + 1""")
        select = ["base.ts_code", "base.trade_date", "base.in_universe", "base.entry_ok"]
        joins = []
        for h in horizons:
            alias = f"x{h}"
            con.execute(f"""
                CREATE OR REPLACE TABLE {alias} AS
                SELECT t.ts_code, t.trade_date, x.adj_open AS exit_px, x.cal_idx - t.target AS delay, x.open_at_down AS blocked
                FROM (SELECT ts_code, trade_date, cal_idx + 1 + {h} AS target FROM base WHERE entry_px IS NOT NULL) t
                ASOF LEFT JOIN b x ON t.ts_code = x.ts_code AND t.target <= x.cal_idx""")
            joins.append(f"LEFT JOIN {alias} USING (ts_code, trade_date)")
            terminal = f"({alias}.exit_px IS NULL AND base.entry_px IS NOT NULL AND lb.last_idx < {global_last} - {TERMINAL_GAP})"
            select += [
                f"CASE WHEN {alias}.exit_px IS NOT NULL THEN {alias}.exit_px / base.entry_px - 1 "
                f"WHEN {terminal} THEN lb.last_adj_close / base.entry_px - 1 END AS fwd_{h}",
                f"{alias}.delay AS exit_delay_{h}", f"COALESCE({alias}.blocked, false) AS exit_blocked_{h}",
                f"{terminal} AS terminal_{h}",
            ]
        con.execute(f"""
            CREATE OR REPLACE TABLE raw_labels AS
            SELECT {', '.join(select)}
            FROM base LEFT JOIN last_bar lb USING (ts_code) {' '.join(joins)}""")
        xs = ", ".join(
            f"fwd_{h} - avg(CASE WHEN in_universe AND entry_ok THEN fwd_{h} END) OVER (PARTITION BY trade_date) AS xs_{h}"
            for h in horizons)
        target = root / "labels" / version
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"""
            COPY (SELECT *, {xs}, substr(trade_date, 1, 4) AS year FROM raw_labels)
            TO '{target.as_posix()}' (FORMAT PARQUET, PARTITION_BY (year))""")
        stats = {"rows": con.execute("SELECT count(*) FROM raw_labels").fetchone()[0],
                 "entry_ok_rate": round(con.execute("SELECT avg(entry_ok::INT) FROM raw_labels").fetchone()[0] or 0, 5)}
        for h in horizons:
            n, mean, delayed, blocked, terminal = con.execute(f"""
                SELECT count(fwd_{h}), avg(fwd_{h}), avg((exit_delay_{h} > 0)::INT), avg(exit_blocked_{h}::INT),
                       sum(terminal_{h}::INT) FROM raw_labels WHERE entry_ok""").fetchone()
            stats[f"h{h}"] = {"labelled": n, "mean_fwd": None if mean is None else round(mean, 6),
                              "exit_delayed_rate": None if delayed is None else round(delayed, 5),
                              "exit_blocked_rate": None if blocked is None else round(blocked, 5),
                              "terminal": int(terminal or 0)}
    finally:
        con.close()
    return stats
