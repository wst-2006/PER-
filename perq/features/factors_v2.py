"""第二批因子（v2）：日线以外的信息。资金流向、融资、筹码、股东户数、分析师预期、
内部人增减持、限售解禁、回购、业绩预告，以及由三大报表按时点还原的财务质量与成长。

时点规则（所有公告类数据）：
- 公告日当天收盘后才视为可知，即只有交易日严格晚于公告日的行才能用到它。
- 财务报表使用"可用日期"（公告日与实际公告日中较晚的一个），同一报告期的多个版本按各自的可用日期生效；
  观察日只能看到当时已公布的最新版本。
- 每类公告都有最长有效期，过期视为缺失，避免一条多年前的公告一直被沿用。

输出：<数据目录>/features/v2/year=YYYY/，行与 v1 因子表一一对应（只含股票池内的行）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..data.curated import connect
from ..data.store import RawStore

VERSION = "v2"

# 名称 → (信息族, 先验方向, 逻辑)。先验方向在看到任何结果之前登记，0 表示不确定。
FACTORS_V2: dict[str, tuple[str, int, str]] = {
    "mf_big_5": ("资金流向", 0, "近 5 日大单与特大单净买入占成交额"),
    "mf_big_20": ("资金流向", 0, "近 20 日大单与特大单净买入占成交额"),
    "mf_small_20": ("资金流向", -1, "近 20 日小单净买入占成交额（散户追买）"),
    "margin_rz_chg_20": ("融资", 0, "融资余额 20 日变化率"),
    "margin_buy_share_20": ("融资", -1, "近 20 日融资买入额占成交额（杠杆资金过热）"),
    "chip_winner": ("筹码", 0, "获利盘比例"),
    "chip_cgo": ("筹码", 0, "现价相对平均持仓成本（资本利得悬置）"),
    "chip_conc": ("筹码", 0, "筹码分散度：85% 与 15% 成本分位之差相对中位成本"),
    "holder_chg": ("股东", -1, "股东户数环比变化（户数减少即筹码集中）"),
    "an_cov_90": ("分析师", 0, "近 90 天研报数量（对数）"),
    "an_rev_eps": ("分析师", 1, "当年盈利预测的近 30 天均值相对 31 至 120 天前均值的变化，除以股价"),
    "an_ep_fy": ("分析师", 1, "近 90 天当年每股盈利预测均值除以股价"),
    "insider_net_90": ("公司事件", 1, "近 90 天股东增持减去减持占总股本"),
    "unlock_30": ("公司事件", -1, "已公告、未来 30 天内解禁的股份占比"),
    "repurchase_60": ("公司事件", 1, "近 60 天内有回购公告"),
    "fc_pchg": ("财务", 1, "近 120 天内最新业绩预告的净利润变动幅度中值"),
    "roe_ttm": ("财务", 1, "滚动十二个月归母净利润除以平均净资产"),
    "gpm_ttm": ("财务", 1, "滚动十二个月毛利率"),
    "ni_yoy_q": ("财务", 1, "最新单季归母净利润同比"),
    "rev_yoy_q": ("财务", 1, "最新单季营业收入同比"),
    "sue": ("财务", 1, "标准化未预期盈余：单季净利润同比变动除以过去 8 个季度该变动的标准差"),
    "accrual": ("财务", -1, "应计项目：滚动十二个月净利润减经营现金流，除以总资产"),
    "asset_growth": ("财务", -1, "总资产同比增长"),
    "leverage": ("财务", 0, "资产负债率"),
}

MAX_STALE = {"fin": 500, "holder": 200, "forecast": 120}


def _raw(store: RawStore, name: str, keys: str) -> str | None:
    """原始层某数据集去重后的子查询；数据集不存在时返回 None。"""
    if not store.has_data(name):
        return None
    return (f"(SELECT * FROM read_parquet('{store.glob(name)}', union_by_name=true) "
            f"QUALIFY row_number() OVER (PARTITION BY {keys} ORDER BY _fetched_at DESC) = 1)")


def _d(column: str) -> str:
    return f"strptime(CAST({column} AS VARCHAR), '%Y%m%d')::DATE"


def _cols(con: Any, source: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}


# ---------------------------------------------------------------- 日频数据

def _moneyflow(con: Any, store: RawStore) -> list[str]:
    src = _raw(store, "moneyflow", "ts_code, trade_date")
    if src is None:
        return []
    side = lambda s: " + ".join(f"COALESCE({s}_{k}_amount, 0)" for k in ("sm", "md", "lg", "elg"))  # noqa: E731
    con.execute(f"""
        CREATE OR REPLACE TABLE f_mf AS
        WITH m AS (
            SELECT ts_code, CAST(trade_date AS VARCHAR) AS trade_date,
                   buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount AS big_net,
                   buy_sm_amount - sell_sm_amount AS small_net,
                   ({side('buy')}) + ({side('sell')}) AS total
            FROM {src})
        SELECT ts_code, trade_date,
               CASE WHEN count(*) OVER w5 >= 4 THEN sum(big_net) OVER w5 / NULLIF(sum(total) OVER w5, 0) END AS mf_big_5,
               CASE WHEN count(*) OVER w20 >= 15 THEN sum(big_net) OVER w20 / NULLIF(sum(total) OVER w20, 0) END AS mf_big_20,
               CASE WHEN count(*) OVER w20 >= 15 THEN sum(small_net) OVER w20 / NULLIF(sum(total) OVER w20, 0) END AS mf_small_20
        FROM m
        WINDOW w5 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
               w20 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)""")
    return ["f_mf"]


def _margin(con: Any, store: RawStore, bars: str) -> list[str]:
    src = _raw(store, "margin_detail", "ts_code, trade_date")
    if src is None:
        return []
    con.execute(f"""
        CREATE OR REPLACE TABLE f_margin AS
        WITH m AS (
            SELECT m.ts_code, CAST(m.trade_date AS VARCHAR) AS trade_date, m.rzye, m.rzmre, b.amount_yuan
            FROM {src} m JOIN {bars} b ON b.ts_code = m.ts_code AND b.trade_date = CAST(m.trade_date AS VARCHAR))
        SELECT ts_code, trade_date,
               CASE WHEN count(*) OVER w20 >= 20 THEN rzye / NULLIF(first_value(rzye) OVER w20, 0) - 1 END AS margin_rz_chg_20,
               CASE WHEN count(*) OVER w20 >= 15 THEN sum(rzmre) OVER w20 / NULLIF(sum(amount_yuan) OVER w20, 0) END AS margin_buy_share_20
        FROM m
        WINDOW w20 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)""")
    return ["f_margin"]


def _chips(con: Any, store: RawStore, bars: str) -> list[str]:
    src = _raw(store, "cyq_perf", "ts_code, trade_date")
    if src is None:
        return []
    con.execute(f"""
        CREATE OR REPLACE TABLE f_chip AS
        SELECT c.ts_code, CAST(c.trade_date AS VARCHAR) AS trade_date,
               c.winner_rate / 100.0 AS chip_winner,
               b.close / NULLIF(c.weight_avg, 0) - 1 AS chip_cgo,
               (c.cost_85pct - c.cost_15pct) / NULLIF(c.cost_50pct, 0) AS chip_conc
        FROM {src} c JOIN {bars} b ON b.ts_code = c.ts_code AND b.trade_date = CAST(c.trade_date AS VARCHAR)""")
    return ["f_chip"]


# ---------------------------------------------------------------- 公告类数据

def _next_day_map(con: Any) -> None:
    """日历日 → 严格晚于它的第一个交易日。"""
    con.execute("""
        CREATE OR REPLACE TABLE next_day AS
        SELECT d.day, min(c.cal_date) AS trade_date
        FROM (SELECT strftime(x, '%Y%m%d') AS day FROM range(DATE '2005-01-01', DATE '2030-01-01', INTERVAL 1 DAY) t(x)) d
        JOIN cal c ON c.cal_date > d.day AND c.cal_date <= strftime(strptime(d.day, '%Y%m%d') + INTERVAL 20 DAY, '%Y%m%d')
        GROUP BY 1""")


def _holders(con: Any, store: RawStore) -> list[str]:
    src = _raw(store, "stk_holdernumber", "ts_code, ann_date, end_date")
    if src is None:
        return []
    con.execute(f"""
        CREATE OR REPLACE TABLE ev_holder AS
        WITH h AS (
            SELECT ts_code, CAST(ann_date AS VARCHAR) AS ann, CAST(end_date AS VARCHAR) AS end_date, holder_num
            FROM {src} WHERE ann_date IS NOT NULL AND holder_num > 0
            QUALIFY row_number() OVER (PARTITION BY ts_code, end_date ORDER BY ann_date) = 1)
        SELECT ts_code, ann, holder_num / lag(holder_num) OVER (PARTITION BY ts_code ORDER BY end_date) - 1 AS holder_chg
        FROM h""")
    return []


def _ranged_events(con: Any, store: RawStore) -> list[str]:
    """把按公告日发生的事件，映射到公告后的第一个交易日，再按自然日窗口累计。"""
    tables = []
    insider = _raw(store, "stk_holdertrade", "ts_code, ann_date, holder_name, in_de, change_vol")
    if insider is not None:
        con.execute(f"""
            CREATE OR REPLACE TABLE ev_insider AS
            SELECT i.ts_code, n.trade_date, sum(CASE WHEN i.in_de = 'IN' THEN 1 ELSE -1 END * COALESCE(i.change_ratio, 0)) / 100.0 AS v
            FROM {insider} i JOIN next_day n ON n.day = CAST(i.ann_date AS VARCHAR) GROUP BY 1, 2""")
        con.execute(f"""
            CREATE OR REPLACE TABLE f_insider AS
            SELECT b.ts_code, b.trade_date, sum(e.v) AS insider_net_90
            FROM kb b JOIN ev_insider e ON e.ts_code = b.ts_code
             AND {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 90 DAY AND e.trade_date <= b.trade_date
            GROUP BY 1, 2""")
        tables.append("f_insider")
    repo = _raw(store, "repurchase", "ts_code, ann_date, proc, end_date")
    if repo is not None:
        con.execute(f"""
            CREATE OR REPLACE TABLE ev_repo AS
            SELECT DISTINCT r.ts_code, n.trade_date FROM {repo} r JOIN next_day n ON n.day = CAST(r.ann_date AS VARCHAR)""")
        con.execute(f"""
            CREATE OR REPLACE TABLE f_repo AS
            SELECT DISTINCT b.ts_code, b.trade_date, 1.0 AS repurchase_60
            FROM kb b JOIN ev_repo e ON e.ts_code = b.ts_code
             AND {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 60 DAY AND e.trade_date <= b.trade_date""")
        tables.append("f_repo")
    unlock = _raw(store, "share_float", "ts_code, ann_date, float_date, holder_name, share_type")
    if unlock is not None:
        con.execute(f"""
            CREATE OR REPLACE TABLE f_unlock AS
            SELECT b.ts_code, b.trade_date, sum(COALESCE(u.float_ratio, 0)) / 100.0 AS unlock_30
            FROM kb b JOIN {unlock} u ON u.ts_code = b.ts_code
             AND CAST(u.ann_date AS VARCHAR) < b.trade_date
             AND CAST(u.float_date AS VARCHAR) > b.trade_date
             AND {_d('u.float_date')} <= {_d('b.trade_date')} + INTERVAL 30 DAY
            GROUP BY 1, 2""")
        tables.append("f_unlock")
    return tables


def _analyst(con: Any, store: RawStore) -> list[str]:
    src = _raw(store, "report_rc", "ts_code, report_date, org_name, author_name, quarter, report_title")
    if src is None:
        return []
    con.execute(f"""
        CREATE OR REPLACE TABLE ev_rc AS
        SELECT r.ts_code, n.trade_date, count(*) AS n_all,
               sum(CASE WHEN r.quarter = substr(n.trade_date, 1, 4) || 'Q4' AND r.eps IS NOT NULL THEN r.eps END) AS eps_sum,
               count(CASE WHEN r.quarter = substr(n.trade_date, 1, 4) || 'Q4' AND r.eps IS NOT NULL THEN 1 END) AS eps_n
        FROM {src} r JOIN next_day n ON n.day = CAST(r.report_date AS VARCHAR)
        GROUP BY 1, 2""")

    def window(lo: int, hi: int) -> str:
        # 同一自然年内（当年盈利预测口径一致），交易日 b 之前 [hi, lo) 天内发布的研报
        return (f"e.ts_code = b.ts_code AND substr(e.trade_date, 1, 4) = substr(b.trade_date, 1, 4) "
                f"AND {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL {hi} DAY "
                f"AND {_d('e.trade_date')} <= {_d('b.trade_date')} - INTERVAL {lo} DAY")

    con.execute(f"""
        CREATE OR REPLACE TABLE f_rc AS
        WITH a AS (
            SELECT b.ts_code, b.trade_date, b.close,
                       sum(CASE WHEN {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 30 DAY THEN e.eps_sum END) AS s30,
                   sum(CASE WHEN {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 30 DAY THEN e.eps_n END) AS c30,
                   sum(CASE WHEN {_d('e.trade_date')} <= {_d('b.trade_date')} - INTERVAL 30 DAY THEN e.eps_sum END) AS sp,
                   sum(CASE WHEN {_d('e.trade_date')} <= {_d('b.trade_date')} - INTERVAL 30 DAY THEN e.eps_n END) AS cp,
                   sum(CASE WHEN {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 90 DAY THEN e.eps_sum END) AS s90,
                   sum(CASE WHEN {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 90 DAY THEN e.eps_n END) AS c90
            FROM kb b JOIN ev_rc e ON {window(0, 120)}
            GROUP BY 1, 2, 3)
        SELECT ts_code, trade_date,
               CASE WHEN c30 > 0 AND cp > 0 THEN (s30 / c30 - sp / cp) / NULLIF(close, 0) END AS an_rev_eps,
               CASE WHEN c90 > 0 THEN (s90 / c90) / NULLIF(close, 0) END AS an_ep_fy
        FROM a""")
    # 覆盖度不分年份：近 90 天内任何研报都算；没有研报的股票记 0（"没人覆盖"本身就是信息）
    con.execute(f"""
        CREATE OR REPLACE TABLE f_cov AS
        SELECT b.ts_code, b.trade_date, ln(1 + COALESCE(sum(e.n_all), 0)) AS an_cov_90
        FROM kb b LEFT JOIN ev_rc e ON e.ts_code = b.ts_code
         AND {_d('e.trade_date')} > {_d('b.trade_date')} - INTERVAL 90 DAY AND e.trade_date <= b.trade_date
        GROUP BY 1, 2""")
    return ["f_rc", "f_cov"]


def _forecast(con: Any, store: RawStore) -> None:
    src = _raw(store, "forecast_vip", "ts_code, ann_date, end_date")
    if src is None:
        con.execute("CREATE OR REPLACE TABLE ev_fc (ts_code VARCHAR, ann VARCHAR, fc_pchg DOUBLE)")
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE ev_fc AS
        SELECT ts_code, CAST(ann_date AS VARCHAR) AS ann,
               least(greatest((COALESCE(p_change_min, p_change_max) + COALESCE(p_change_max, p_change_min)) / 200.0, -1.0), 3.0) AS fc_pchg
        FROM {src} WHERE ann_date IS NOT NULL AND (p_change_min IS NOT NULL OR p_change_max IS NOT NULL)""")


# ---------------------------------------------------------------- 财务（按时点还原）

# 字段 → 候选列（按顺序取第一个非空的）
FIN_FIELDS = {
    "fin_income": {"ni": ["n_income_attr_p"], "rev": ["revenue", "total_revenue"], "cogs": ["oper_cost"]},
    "fin_cashflow": {"cfo": ["n_cashflow_act"]},
    "fin_balancesheet": {"ta": ["total_assets"], "tl": ["total_liab"], "eq": ["total_hldr_eqy_exc_min_int"]},
}
FLOW = {"ni", "rev", "cogs", "cfo"}       # 年初累计值，需要还原成单季
LOOKBACK_DAYS = 1400


def _fin_events(con: Any, root: Path) -> bool:
    cur = root / "curated"
    sources = {t: cur / f"{t}.parquet" for t in FIN_FIELDS}
    if not all(p.exists() for p in sources.values()):
        return False
    for table, fields in FIN_FIELDS.items():
        cols = _cols(con, f"read_parquet('{sources[table].as_posix()}')")
        def pick(candidates: list[str]) -> str:
            present = [c for c in candidates if c in cols]
            if not present:
                return "NULL::DOUBLE"
            return f"CAST(COALESCE({', '.join(present)}) AS DOUBLE)" if len(present) > 1 else f"CAST({present[0]} AS DOUBLE)"

        sel = ", ".join(f"{pick(cands)} AS {name}" for name, cands in fields.items())
        con.execute(f"""
            CREATE OR REPLACE TABLE v_{table} AS
            SELECT ts_code, end_date, avail_date, COALESCE(update_flag, '0') AS update_flag, {sel}
            FROM read_parquet('{sources[table].as_posix()}')
            WHERE report_type IN ('1', '4', '5') AND substr(end_date, 5, 4) IN ('0331', '0630', '0930', '1231')""")
    con.execute("""
        CREATE OR REPLACE TABLE fin_ev AS
        SELECT DISTINCT ts_code, avail_date AS e FROM (
            SELECT ts_code, avail_date FROM v_fin_income UNION ALL SELECT ts_code, avail_date FROM v_fin_cashflow
            UNION ALL SELECT ts_code, avail_date FROM v_fin_balancesheet)
        WHERE avail_date >= '20140101'""")
    # 每个事件日：每个报告期取当时已公布的最新版本
    for table, fields in FIN_FIELDS.items():
        names = ", ".join(f"v.{n}" for n in fields)
        con.execute(f"""
            CREATE OR REPLACE TABLE a_{table} AS
            SELECT ev.ts_code, ev.e, v.end_date, {names}
            FROM fin_ev ev JOIN v_{table} v ON v.ts_code = ev.ts_code AND v.avail_date <= ev.e
             AND v.end_date >= strftime({_d('ev.e')} - INTERVAL {LOOKBACK_DAYS} DAY, '%Y%m%d')
            QUALIFY row_number() OVER (PARTITION BY ev.ts_code, ev.e, v.end_date ORDER BY v.avail_date DESC, v.update_flag DESC) = 1""")
    con.execute("""
        CREATE OR REPLACE TABLE fp AS
        SELECT COALESCE(i.ts_code, c.ts_code, b.ts_code) AS ts_code, COALESCE(i.e, c.e, b.e) AS e,
               COALESCE(i.end_date, c.end_date, b.end_date) AS end_date,
               i.ni, i.rev, i.cogs, c.cfo, b.ta, b.tl, b.eq
        FROM a_fin_income i
        FULL JOIN a_fin_cashflow c USING (ts_code, e, end_date)
        FULL JOIN a_fin_balancesheet b USING (ts_code, e, end_date)""")
    # 单季值：一季度即累计值；其余季度为本期累计减上一季度累计（上一季度必须存在）
    prev_q = ("CASE substr(p.end_date, 5, 4) WHEN '0630' THEN substr(p.end_date, 1, 4) || '0331' "
              "WHEN '0930' THEN substr(p.end_date, 1, 4) || '0630' WHEN '1231' THEN substr(p.end_date, 1, 4) || '0930' END")
    sq = ", ".join(f"CASE WHEN substr(p.end_date, 5, 4) = '0331' THEN p.{f} ELSE p.{f} - q.{f} END AS q_{f}" for f in sorted(FLOW))
    con.execute(f"""
        CREATE OR REPLACE TABLE fq AS
        SELECT p.*, {sq}, strftime({_d('p.end_date')} - INTERVAL 1 YEAR, '%Y%m%d') AS ly_end
        FROM fp p LEFT JOIN fp q ON q.ts_code = p.ts_code AND q.e = p.e AND q.end_date = {prev_q}""")
    # 单季同比差额（用于 SUE），以及它在之前 8 个季度上的标准差
    con.execute("""
        CREATE OR REPLACE TABLE fd AS
        SELECT a.*, b.q_ni AS q_ni_ly, b.q_rev AS q_rev_ly, a.q_ni - b.q_ni AS d_ni
        FROM fq a LEFT JOIN fq b ON b.ts_code = a.ts_code AND b.e = a.e AND b.end_date = a.ly_end""")
    con.execute("""
        CREATE OR REPLACE TABLE fs AS
        SELECT *, stddev_samp(d_ni) OVER (PARTITION BY ts_code, e ORDER BY end_date ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS d_sd,
                  count(d_ni) OVER (PARTITION BY ts_code, e ORDER BY end_date ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS d_n
        FROM fd""")
    # 每个事件日的最新报告期 L：滚动十二个月 = L 累计 + 上年年报 − 上年同期累计（L 为年报时即 L 累计）
    ttm = lambda f: (f"CASE WHEN substr(l.end_date, 5, 4) = '1231' THEN l.{f} "  # noqa: E731
                     f"ELSE l.{f} + y.{f} - s.{f} END")
    con.execute(f"""
        CREATE OR REPLACE TABLE fin_metrics AS
        WITH latest AS (
            SELECT * FROM fs WHERE ni IS NOT NULL
            QUALIFY row_number() OVER (PARTITION BY ts_code, e ORDER BY end_date DESC) = 1),
        m AS (
            SELECT l.ts_code, l.e, l.end_date,
                   {ttm('ni')} AS ni_ttm, {ttm('rev')} AS rev_ttm, {ttm('cogs')} AS cogs_ttm, {ttm('cfo')} AS cfo_ttm,
                   l.ta, l.tl, l.eq, s.eq AS eq_ly, s.ta AS ta_ly,
                   l.q_ni, l.q_ni_ly, l.q_rev, l.q_rev_ly, l.d_ni, l.d_sd, l.d_n
            FROM latest l
            LEFT JOIN fs y ON y.ts_code = l.ts_code AND y.e = l.e AND y.end_date = substr(l.ly_end, 1, 4) || '1231'
            LEFT JOIN fs s ON s.ts_code = l.ts_code AND s.e = l.e AND s.end_date = l.ly_end)
        SELECT ts_code, e,
               ni_ttm / NULLIF((eq + COALESCE(eq_ly, eq)) / 2, 0) AS roe_ttm,
               (rev_ttm - cogs_ttm) / NULLIF(rev_ttm, 0) AS gpm_ttm,
               least(greatest((q_ni - q_ni_ly) / NULLIF(abs(q_ni_ly), 0), -5), 5) AS ni_yoy_q,
               least(greatest((q_rev - q_rev_ly) / NULLIF(abs(q_rev_ly), 0), -5), 5) AS rev_yoy_q,
               CASE WHEN d_n >= 4 AND d_sd > 0 THEN least(greatest(d_ni / d_sd, -10), 10) END AS sue,
               (ni_ttm - cfo_ttm) / NULLIF(ta, 0) AS accrual,
               ta / NULLIF(ta_ly, 0) - 1 AS asset_growth,
               tl / NULLIF(ta, 0) AS leverage
        FROM m""")
    return True


# ---------------------------------------------------------------- 汇总

_BARS_KEYS = "bars_keys"


def build_factors_v2(root: str | Path, memory_limit: str = "2GB", threads: int = 4) -> dict[str, Any]:
    root = Path(root)
    store = RawStore(root)
    con = connect(root, memory_limit, threads)
    cur = root / "curated"
    bars = f"read_parquet('{(cur / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    keys = f"read_parquet('{(root / 'features' / 'v1').as_posix()}/*/*.parquet', hive_partitioning=true)"
    target = root / "features" / VERSION
    try:
        con.execute(f"CREATE OR REPLACE TABLE cal AS SELECT CAST(cal_date AS VARCHAR) AS cal_date FROM read_parquet('{(cur / 'calendar.parquet').as_posix()}')")
        con.execute(f"CREATE OR REPLACE TABLE {_BARS_KEYS} AS SELECT ts_code, trade_date, close, amount_yuan FROM {bars}")
        con.execute(f"CREATE OR REPLACE TABLE k AS SELECT ts_code, trade_date FROM {keys}")
        # 公告类因子只需要在股票池内的行上计算
        con.execute(f"CREATE OR REPLACE TABLE kb AS SELECT k.ts_code, k.trade_date, b.close FROM k JOIN {_BARS_KEYS} b USING (ts_code, trade_date)")
        _next_day_map(con)
        daily = _moneyflow(con, store) + _margin(con, store, _BARS_KEYS) + _chips(con, store, _BARS_KEYS)
        _holders(con, store)
        daily += _ranged_events(con, store) + _analyst(con, store)
        _forecast(con, store)
        has_fin = _fin_events(con, root)

        select = ["k.ts_code", "k.trade_date"]
        joins = []
        present: set[str] = set()
        for table in daily:
            cols = [c for c in _cols(con, table) if c in FACTORS_V2]
            present.update(cols)
            select += [f"{table}.{c}" for c in cols]
            joins.append(f"LEFT JOIN {table} USING (ts_code, trade_date)")
        con.execute(f"CREATE OR REPLACE TABLE out1 AS SELECT {', '.join(select)} FROM k {' '.join(joins)}")

        # 公告类：取严格早于观察日、且未过期的最近一条
        def asof(table: str, columns: list[str], stale: int) -> None:
            nonlocal present
            if con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{table}'").fetchone()[0] == 0:
                return
            cols = ", ".join(f"CASE WHEN {_d('o.trade_date')} - {_d('x.ann')} <= {stale} THEN x.{c} END AS {c}" for c in columns)
            con.execute(f"""
                CREATE OR REPLACE TABLE out1 AS
                SELECT o.*, {cols}
                FROM out1 o ASOF LEFT JOIN (SELECT * FROM {table} ORDER BY ann) x
                  ON o.ts_code = x.ts_code AND o.trade_date > x.ann""")
            present.update(columns)

        asof("ev_holder", ["holder_chg"], MAX_STALE["holder"])
        asof("ev_fc", ["fc_pchg"], MAX_STALE["forecast"])
        if has_fin:
            con.execute("CREATE OR REPLACE TABLE ev_fin AS SELECT e AS ann, * EXCLUDE (e) FROM fin_metrics")
            asof("ev_fin", ["roe_ttm", "gpm_ttm", "ni_yoy_q", "rev_yoy_q", "sue", "accrual", "asset_growth", "leverage"],
                 MAX_STALE["fin"])
        for name in FACTORS_V2:
            if name not in present:
                con.execute(f"ALTER TABLE out1 ADD COLUMN {name} DOUBLE")
        ordered = ", ".join(f"CAST({n} AS FLOAT) AS {n}" for n in FACTORS_V2)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"""COPY (SELECT ts_code, trade_date, {ordered}, substr(trade_date, 1, 4) AS year FROM out1)
                        TO '{target.as_posix()}' (FORMAT PARQUET, PARTITION_BY (year))""")
        rows = con.execute("SELECT count(*) FROM out1").fetchone()[0]
        coverage = con.execute("SELECT " + ", ".join(
            f"avg(({n} IS NOT NULL AND isfinite({n}))::INT)" for n in FACTORS_V2) + " FROM out1").fetchone()
        by_year = con.execute("SELECT substr(trade_date, 1, 4), " + ", ".join(
            f"round(avg(({n} IS NOT NULL AND isfinite({n}))::INT), 3)" for n in FACTORS_V2)
            + " FROM out1 GROUP BY 1 ORDER BY 1").fetchall()
    finally:
        con.close()
    return {"version": VERSION, "rows": rows, "factors": len(FACTORS_V2),
            "missing_sources": sorted(set(FACTORS_V2) - present),
            "coverage": {n: round(c or 0.0, 4) for n, c in zip(FACTORS_V2, coverage)},
            "coverage_by_year": {row[0]: dict(zip(FACTORS_V2, row[1:])) for row in by_year}}
