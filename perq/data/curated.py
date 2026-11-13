"""整理层：由原始层重建，随时可以整体重算。

产出（均在 <数据根目录>/curated/ 下）：
- calendar.parquet   交易日及其序号
- stocks.parquet     股票基础信息（在市、退市、暂停上市合并）
- bars/year=YYYY/    日线主表：行情、复权、涨跌停、每日指标、可交易性与股票池标记
- fin_income.parquet / fin_balancesheet.parquet / fin_cashflow.parquet
                     财务报表的全部版本，带"可用日期"

全部计算由 DuckDB 在限定内存下完成，可溢出到磁盘。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .store import RawStore

BASIC_COLUMNS = ["turnover_rate", "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
                 "total_share", "float_share", "free_share", "total_mv", "circ_mv"]
FIN_TABLES = {"fin_income": "income_vip", "fin_balancesheet": "balancesheet_vip", "fin_cashflow": "cashflow_vip"}
PRICE_EPS = 0.0005
UNIVERSE_MIN_LISTED_TDAYS = 120
UNIVERSE_MIN_AMOUNT_MA20 = 3.0e7      # 元
UNIVERSE_MIN_BARS_IN_20 = 15


def connect(root: str | Path, memory_limit: str = "2GB", threads: int = 4) -> Any:
    import duckdb

    root = Path(root)
    tmp = root / "_duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET threads={int(threads)}")
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def _s(column: str) -> str:
    """把一列清洗成字符串：空串和 'nan' 视为空值。"""
    return f"NULLIF(NULLIF(CAST({column} AS VARCHAR), 'nan'), '')"


def _latest_snapshot(store: RawStore, name: str) -> str | None:
    keys = sorted(k for k, e in store.entries(name).items() if e["status"] == "ok")
    return store.path(name, keys[-1]).as_posix() if keys else None


def _columns(con: Any, source: str) -> list[str]:
    return [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]


def build_calendar(con: Any, store: RawStore, out: Path) -> int:
    path = _latest_snapshot(store, "trade_cal")
    if path is None:
        raise RuntimeError("原始层没有 trade_cal")
    con.execute(f"""
        CREATE OR REPLACE TABLE cal AS
        SELECT {_s('cal_date')} AS cal_date, row_number() OVER (ORDER BY {_s('cal_date')}) AS idx
        FROM read_parquet('{path}')
        WHERE TRY_CAST(is_open AS DOUBLE) = 1
    """)
    con.execute(f"COPY cal TO '{(out / 'calendar.parquet').as_posix()}' (FORMAT PARQUET)")
    return con.execute("SELECT count(*) FROM cal").fetchone()[0]


def build_stocks(con: Any, store: RawStore, out: Path) -> int:
    files = [p for p in (_latest_snapshot(store, f"stock_basic_{s}") for s in ("L", "D", "P")) if p]
    if not files:
        raise RuntimeError("原始层没有 stock_basic")
    listing = ", ".join(f"'{f}'" for f in files)
    source = f"read_parquet([{listing}], union_by_name=true)"
    cols = _columns(con, source)
    market = _s("market") if "market" in cols else "NULL"
    exchange = _s("exchange") if "exchange" in cols else "NULL"
    con.execute(f"""
        CREATE OR REPLACE TABLE stocks AS
        SELECT {_s('ts_code')} AS ts_code, {_s('name')} AS name,
               COALESCE({market}, CASE WHEN {exchange} = 'BSE' THEN '北交所' ELSE '主板' END) AS board,
               {exchange} AS exchange, {_s('list_status')} AS list_status,
               {_s('list_date')} AS list_date, {_s('delist_date')} AS delist_date
        FROM {source}
        QUALIFY row_number() OVER (PARTITION BY {_s('ts_code')} ORDER BY {_s('list_status')} DESC) = 1
    """)
    con.execute("""
        CREATE OR REPLACE TABLE list_idx AS
        SELECT s.ts_code, min(c.idx) AS list_idx FROM stocks s JOIN cal c ON c.cal_date >= s.list_date GROUP BY 1
    """)
    con.execute(f"COPY stocks TO '{(out / 'stocks.parquet').as_posix()}' (FORMAT PARQUET)")
    return con.execute("SELECT count(*) FROM stocks").fetchone()[0]


def _build_name_flags(con: Any, store: RawStore) -> None:
    """由曾用名记录展开出每个交易日的 ST 与退市整理标记。"""
    if not store.has_data("namechange"):
        con.execute("CREATE OR REPLACE TABLE name_flags (ts_code VARCHAR, trade_date VARCHAR, is_st BOOLEAN, is_delisting BOOLEAN)")
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE name_flags AS
        WITH iv AS (
            SELECT DISTINCT {_s('ts_code')} AS ts_code, {_s('name')} AS name, {_s('start_date')} AS start_date,
                   COALESCE({_s('end_date')}, '99991231') AS end_date
            FROM read_parquet('{store.glob('namechange')}', union_by_name=true)
            WHERE {_s('start_date')} IS NOT NULL AND ({_s('name')} LIKE '%ST%' OR {_s('name')} LIKE '%退%')
        )
        SELECT iv.ts_code, c.cal_date AS trade_date,
               bool_or(iv.name LIKE '%ST%') AS is_st, bool_or(iv.name LIKE '%退%') AS is_delisting
        FROM iv JOIN cal c ON c.cal_date BETWEEN iv.start_date AND iv.end_date
        GROUP BY 1, 2
    """)


def build_industry(con: Any, store: RawStore, out: Path) -> int:
    """申万行业的历史归属区间。没有任何行业数据时建一张空表。"""
    files = []
    for folder in sorted(store.raw.glob("sw_member_*")):
        key = sorted(k for k, e in store.entries(folder.name).items() if e["status"] == "ok")
        if key:
            files.append(store.path(folder.name, key[-1]).as_posix())
    if not files:
        con.execute("""CREATE OR REPLACE TABLE industry (ts_code VARCHAR, l1_code VARCHAR, l1_name VARCHAR,
                       l2_code VARCHAR, l2_name VARCHAR, in_date VARCHAR, out_date VARCHAR)""")
    else:
        listing = ", ".join(f"'{f}'" for f in files)
        con.execute(f"""
            CREATE OR REPLACE TABLE industry AS
            SELECT DISTINCT {_s('ts_code')} AS ts_code, {_s('l1_code')} AS l1_code, {_s('l1_name')} AS l1_name,
                   {_s('l2_code')} AS l2_code, {_s('l2_name')} AS l2_name,
                   {_s('in_date')} AS in_date, {_s('out_date')} AS out_date
            FROM read_parquet([{listing}], union_by_name=true)
            WHERE {_s('ts_code')} IS NOT NULL AND {_s('in_date')} IS NOT NULL
        """)
    con.execute(f"COPY industry TO '{(out / 'industry.parquet').as_posix()}' (FORMAT PARQUET)")
    return con.execute("SELECT count(*) FROM industry").fetchone()[0]


def _dedup(store: RawStore, name: str, columns: str) -> str:
    return f"""(SELECT {columns} FROM read_parquet('{store.glob(name)}', union_by_name=true)
               QUALIFY row_number() OVER (PARTITION BY ts_code, trade_date ORDER BY _fetched_at DESC) = 1)"""


def build_bars(con: Any, store: RawStore, out: Path) -> dict[str, Any]:
    for required in ("daily", "adj_factor", "stk_limit"):
        if not store.has_data(required):
            raise RuntimeError(f"原始层没有 {required}")
    _build_name_flags(con, store)
    has_basic = store.has_data("daily_basic")
    basic_cols = []
    if has_basic:
        present = _columns(con, f"read_parquet('{store.glob('daily_basic')}', union_by_name=true)")
        basic_cols = [c for c in BASIC_COLUMNS if c in present]
    basic_select = "".join(f", b.{c}" for c in basic_cols)
    basic_join = (f"LEFT JOIN {_dedup(store, 'daily_basic', 'ts_code, trade_date, ' + ', '.join(basic_cols))} b USING (ts_code, trade_date)"
                  if basic_cols else "")
    d = _dedup(store, "daily", "ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount")
    a = _dedup(store, "adj_factor", "ts_code, trade_date, adj_factor")
    lim = _dedup(store, "stk_limit", "ts_code, trade_date, up_limit, down_limit")
    e = PRICE_EPS
    con.execute(f"""
        CREATE OR REPLACE TABLE bars AS
        WITH j AS (
            SELECT d.*, a.adj_factor, l.up_limit, l.down_limit {basic_select},
                   s.board, s.exchange, c.idx AS cal_idx, li.list_idx
            FROM {d} d
            JOIN stocks s USING (ts_code)
            JOIN cal c ON c.cal_date = d.trade_date
            LEFT JOIN list_idx li USING (ts_code)
            LEFT JOIN {a} a USING (ts_code, trade_date)
            LEFT JOIN {lim} l USING (ts_code, trade_date)
            {basic_join}
            WHERE d.trade_date >= s.list_date
        ), w AS (
            SELECT j.*,
                   open * adj_factor AS adj_open, high * adj_factor AS adj_high,
                   low * adj_factor AS adj_low, close * adj_factor AS adj_close,
                   close * adj_factor / lag(close * adj_factor) OVER win - 1 AS ret,
                   close / NULLIF(pre_close, 0) - 1 AS ret_pc,
                   cal_idx - lag(cal_idx) OVER win - 1 AS gap_days,
                   cal_idx - list_idx AS listed_tdays,
                   amount * 1000 AS amount_yuan,
                   avg(amount * 1000) OVER (win ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amount_ma20,
                   count(*) OVER (PARTITION BY ts_code ORDER BY cal_idx RANGE BETWEEN 19 PRECEDING AND CURRENT ROW) AS bars_in_20
            FROM j
            WINDOW win AS (PARTITION BY ts_code ORDER BY trade_date)
        )
        SELECT w.* EXCLUDE (list_idx, amount),
               (up_limit IS NULL OR down_limit IS NULL) AS limit_missing,
               COALESCE(open >= up_limit - {e}, false) AS open_at_up,
               COALESCE(open <= down_limit + {e}, false) AS open_at_down,
               COALESCE(close >= up_limit - {e}, false) AS close_at_up,
               COALESCE(close <= down_limit + {e}, false) AS close_at_down,
               COALESCE(high >= up_limit - {e}, false) AS touched_up,
               COALESCE(low <= down_limit + {e}, false) AS touched_down,
               COALESCE(low >= up_limit - {e}, false) AS locked_up,
               COALESCE(high <= down_limit + {e}, false) AS locked_down,
               COALESCE(n.is_st, false) AS is_st,
               COALESCE(n.is_delisting, false) AS is_delisting,
               (NOT COALESCE(n.is_st, false) AND NOT COALESCE(n.is_delisting, false)
                AND listed_tdays >= {UNIVERSE_MIN_LISTED_TDAYS}
                AND bars_in_20 >= {UNIVERSE_MIN_BARS_IN_20}
                AND amount_ma20 >= {UNIVERSE_MIN_AMOUNT_MA20}) AS in_universe,
               substr(w.trade_date, 1, 4) AS year
        FROM w LEFT JOIN name_flags n USING (ts_code, trade_date)
    """)
    # 行业：取"纳入日期不晚于当日"的最近一条记录；该记录已在当日之前剔除、或当日早于第一条记录时，
    # 退回到这只股票最早的一条记录，并打上回填标记（只影响行业归属，不影响价格）。
    con.execute("""
        CREATE OR REPLACE TABLE bars AS
        WITH first_ind AS (
            SELECT ts_code, arg_min(l1_code, in_date) AS l1_code, arg_min(l1_name, in_date) AS l1_name,
                   arg_min(l2_code, in_date) AS l2_code FROM industry GROUP BY 1
        ), hit AS (
            SELECT b.ts_code, b.trade_date, i.l1_code, i.l1_name, i.l2_code, i.out_date
            FROM bars b ASOF LEFT JOIN industry i ON b.ts_code = i.ts_code AND b.trade_date >= i.in_date
        )
        SELECT b.*,
               CASE WHEN h.l1_code IS NOT NULL AND (h.out_date IS NULL OR h.out_date > b.trade_date)
                    THEN h.l1_code ELSE f.l1_code END AS sw_l1,
               CASE WHEN h.l1_code IS NOT NULL AND (h.out_date IS NULL OR h.out_date > b.trade_date)
                    THEN h.l1_name ELSE f.l1_name END AS sw_l1_name,
               CASE WHEN h.l1_code IS NOT NULL AND (h.out_date IS NULL OR h.out_date > b.trade_date)
                    THEN h.l2_code ELSE f.l2_code END AS sw_l2,
               NOT (h.l1_code IS NOT NULL AND (h.out_date IS NULL OR h.out_date > b.trade_date))
                    AND f.l1_code IS NOT NULL AS sw_backfilled
        FROM bars b LEFT JOIN hit h USING (ts_code, trade_date) LEFT JOIN first_ind f USING (ts_code)
    """)
    target = out / "bars"
    if target.exists():
        shutil.rmtree(target)
    con.execute(f"COPY bars TO '{target.as_posix()}' (FORMAT PARQUET, PARTITION_BY (year))")
    raw_rows = con.execute(f"SELECT count(*) FROM {d}").fetchone()[0]
    orphan = con.execute(f"SELECT count(*) FROM {d} d WHERE NOT EXISTS (SELECT 1 FROM stocks s WHERE s.ts_code = d.ts_code)").fetchone()[0]
    rows, first, last = con.execute("SELECT count(*), min(trade_date), max(trade_date) FROM bars").fetchone()
    return {"rows": rows, "first_date": first, "last_date": last, "raw_daily_rows": raw_rows,
            "dropped_before_listing_or_off_calendar": raw_rows - orphan - rows, "orphan_rows": orphan,
            "has_daily_basic": bool(basic_cols), "has_namechange": store.has_data("namechange"),
            "industry_missing_rate": round(con.execute("SELECT avg((sw_l1 IS NULL)::INT) FROM bars").fetchone()[0] or 0.0, 5),
            "industry_backfilled_rate": round(con.execute("SELECT avg(sw_backfilled::INT) FROM bars").fetchone()[0] or 0.0, 5)}


def build_financials(con: Any, store: RawStore, out: Path) -> dict[str, Any]:
    """保留每个报告期的全部版本。可用日期取公告日与实际公告日中较晚的一个。"""
    summary: dict[str, Any] = {}
    for table, api in FIN_TABLES.items():
        variants = [d for d in sorted(store.raw.glob(f"{api}_rt*")) if any(d.glob("*.parquet"))]
        if not variants:
            summary[table] = {"skipped": "原始层没有该报表"}
            continue
        listing = ", ".join(f"'{(v / '*.parquet').as_posix()}'" for v in variants)
        source = f"read_parquet([{listing}], union_by_name=true)"
        cols = _columns(con, source)
        f_ann = _s("f_ann_date") if "f_ann_date" in cols else "NULL"
        flag = _s("update_flag") if "update_flag" in cols else "'0'"
        # 上游把数值型的代码列存成了浮点（如 1.0），统一去掉小数部分
        rtype = f"regexp_replace({_s('report_type')}, '\\.0$', '')" if "report_type" in cols else "'1'"
        flag = f"regexp_replace({flag}, '\\.0$', '')"
        drop = [c for c in ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "update_flag") if c in cols]
        con.execute(f"""
            CREATE OR REPLACE TABLE {table} AS
            WITH v AS (
                SELECT {_s('ts_code')} AS ts_code, {_s('end_date')} AS end_date, {rtype} AS report_type,
                       {_s('ann_date')} AS ann_date, {f_ann} AS f_ann_date, {flag} AS update_flag,
                       CASE WHEN {f_ann} IS NULL THEN {_s('ann_date')}
                            WHEN {_s('ann_date')} IS NULL THEN {f_ann}
                            WHEN {f_ann} > {_s('ann_date')} THEN {f_ann} ELSE {_s('ann_date')} END AS avail_date,
                       * EXCLUDE ({', '.join(drop)})
                FROM {source}
            )
            SELECT * FROM v
            WHERE avail_date IS NOT NULL AND ts_code IS NOT NULL AND end_date IS NOT NULL
            QUALIFY row_number() OVER (PARTITION BY ts_code, end_date, report_type, avail_date
                                       ORDER BY update_flag DESC, _fetched_at DESC) = 1
        """)
        con.execute(f"COPY {table} TO '{(out / (table + '.parquet')).as_posix()}' (FORMAT PARQUET)")
        rows, periods, lo, hi = con.execute(
            f"SELECT count(*), count(DISTINCT (ts_code, end_date)), min(end_date), max(end_date) FROM {table}").fetchone()
        summary[table] = {"rows": rows, "stock_periods": periods, "first_period": lo, "last_period": hi,
                          "variants": [v.name for v in variants]}
    return summary


def build_all(root: str | Path, memory_limit: str = "2GB", threads: int = 4) -> dict[str, Any]:
    root = Path(root)
    store = RawStore(root)
    out = root / "curated"
    out.mkdir(parents=True, exist_ok=True)
    con = connect(root, memory_limit, threads)
    try:
        summary = {"calendar_days": build_calendar(con, store, out), "stocks": build_stocks(con, store, out)}
        summary["industry_rows"] = build_industry(con, store, out)
        summary["bars"] = build_bars(con, store, out)
        summary["financials"] = build_financials(con, store, out)
    finally:
        con.close()
    return summary


# ---------- 时点查询 ----------

def fin_asof_sql(source: str, asof: str, report_types: tuple[str, ...] = ("1",)) -> str:
    """观察日 asof 当天收盘后能看到的、每个（股票，报告期）的最新版本。

    只使用可用日期不晚于观察日的版本，因此不会用到观察日之后才公布的修订。
    """
    types = ", ".join(f"'{t}'" for t in report_types)
    return f"""
        SELECT * FROM {source}
        WHERE avail_date <= '{asof}' AND report_type IN ({types})
        QUALIFY row_number() OVER (PARTITION BY ts_code, end_date
                                   ORDER BY avail_date DESC, update_flag DESC) = 1
    """
