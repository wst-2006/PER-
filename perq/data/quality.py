"""整理层的数据质量检查。任何一项"门禁"检查不通过，当日流程应当停止。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .curated import FIN_TABLES, connect

THRESHOLDS = {
    "duplicate_keys": 0,
    "nonpositive_price_rows": 0,
    "high_below_low_rows": 0,
    "close_outside_limit_rows": 0,
    "adj_factor_missing_rate": 0.001,
    "limit_missing_rate": 0.005,
    "max_yearly_limit_missing_rate": 0.02,
    "ret_mismatch_rate": 0.001,
    "min_yearly_median_coverage": 0.95,
    "fin_avail_before_period_end": 0,
}


def _check(value: Any, threshold: Any, higher_is_better: bool = False) -> dict[str, Any]:
    if value is None:
        return {"value": None, "threshold": threshold, "pass": False}
    ok = value >= threshold if higher_is_better else value <= threshold
    return {"value": value, "threshold": threshold, "pass": bool(ok)}


def run(root: str | Path, memory_limit: str = "2GB", sample_mode: bool = False) -> dict[str, Any]:
    """sample_mode：数据只是少量股票的样本时，跳过"覆盖率"这一项门禁。"""
    root = Path(root)
    cur = root / "curated"
    con = connect(root, memory_limit)
    bars = f"read_parquet('{(cur / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    stocks = f"read_parquet('{(cur / 'stocks.parquet').as_posix()}')"
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    try:
        total = one(f"SELECT count(*) FROM {bars}")
        gates: dict[str, Any] = {}
        info: dict[str, Any] = {"rows": total}
        info["first_date"], info["last_date"], info["stocks"] = con.execute(
            f"SELECT min(trade_date), max(trade_date), count(DISTINCT ts_code) FROM {bars}").fetchone()

        gates["duplicate_keys"] = _check(one(
            f"SELECT count(*) FROM (SELECT ts_code, trade_date FROM {bars} GROUP BY 1, 2 HAVING count(*) > 1)"),
            THRESHOLDS["duplicate_keys"])
        gates["nonpositive_price_rows"] = _check(one(
            f"SELECT count(*) FROM {bars} WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0"),
            THRESHOLDS["nonpositive_price_rows"])
        gates["high_below_low_rows"] = _check(one(f"SELECT count(*) FROM {bars} WHERE high < low"),
                                              THRESHOLDS["high_below_low_rows"])
        gates["close_outside_limit_rows"] = _check(one(
            f"SELECT count(*) FROM {bars} WHERE close > up_limit + 0.011 OR close < down_limit - 0.011"),
            THRESHOLDS["close_outside_limit_rows"])
        gates["adj_factor_missing_rate"] = _check(round(one(
            f"SELECT avg(CASE WHEN adj_factor IS NULL THEN 1.0 ELSE 0 END) FROM {bars}") or 0.0, 6),
            THRESHOLDS["adj_factor_missing_rate"])
        gates["limit_missing_rate"] = _check(round(one(
            f"SELECT avg(CASE WHEN limit_missing THEN 1.0 ELSE 0 END) FROM {bars}") or 0.0, 6),
            THRESHOLDS["limit_missing_rate"])
        gates["ret_mismatch_rate"] = _check(round(one(f"""
            SELECT avg(CASE WHEN abs(ret - ret_pc) > 0.005 THEN 1.0 ELSE 0 END)
            FROM {bars} WHERE ret IS NOT NULL AND ret_pc IS NOT NULL AND gap_days = 0""") or 0.0, 6),
            THRESHOLDS["ret_mismatch_rate"])

        info["by_year"] = [
            {"year": y, "rows": n, "limit_missing_rate": round(lm, 5), "st_share": round(st, 4),
             "universe_median": int(u), "suspended_resume_rows": int(g), "open_at_up": int(ou), "open_at_down": int(od)}
            for y, n, lm, st, u, g, ou, od in con.execute(f"""
                WITH daily AS (SELECT trade_date, sum(CASE WHEN in_universe THEN 1 ELSE 0 END) AS u FROM {bars} GROUP BY 1)
                SELECT b.year, count(*), avg(CASE WHEN limit_missing THEN 1.0 ELSE 0 END),
                       avg(CASE WHEN is_st THEN 1.0 ELSE 0 END),
                       (SELECT median(u) FROM daily d WHERE substr(d.trade_date, 1, 4) = CAST(b.year AS VARCHAR)),
                       sum(CASE WHEN gap_days > 0 THEN 1 ELSE 0 END),
                       sum(CASE WHEN open_at_up THEN 1 ELSE 0 END), sum(CASE WHEN open_at_down THEN 1 ELSE 0 END)
                FROM {bars} b GROUP BY b.year ORDER BY b.year""").fetchall()]
        coverage = con.execute(f"""
            WITH per_day AS (SELECT trade_date, count(*) AS n FROM {bars} GROUP BY 1),
            expected AS (
                SELECT p.trade_date, count(*) AS n_expected
                FROM per_day p JOIN {stocks} s
                  ON s.list_date <= p.trade_date AND (s.delist_date IS NULL OR s.delist_date > p.trade_date)
                GROUP BY 1)
            SELECT substr(p.trade_date, 1, 4) AS year, median(p.n * 1.0 / e.n_expected) AS med,
                   min(p.n * 1.0 / e.n_expected) AS worst, arg_min(p.trade_date, p.n * 1.0 / e.n_expected) AS worst_day,
                   count(*) AS days, median(p.n) AS median_stocks
            FROM per_day p JOIN expected e USING (trade_date) GROUP BY 1 ORDER BY 1""").fetchall()
        info["coverage_by_year"] = [
            {"year": y, "median": round(m, 4), "worst": round(w, 4), "worst_day": d, "days": n, "median_stocks": int(ms)}
            for y, m, w, d, n, ms in coverage]
        gates["max_yearly_limit_missing_rate"] = _check(
            max((row["limit_missing_rate"] for row in info["by_year"]), default=None),
            THRESHOLDS["max_yearly_limit_missing_rate"])
        min_cov = min((row["median"] for row in info["coverage_by_year"]), default=None)
        if sample_mode:
            info["min_yearly_median_coverage"] = min_cov
        else:
            gates["min_yearly_median_coverage"] = _check(min_cov, THRESHOLDS["min_yearly_median_coverage"], True)

        info["extreme_returns"] = {
            "abs_ret_over_31pct": one(f"SELECT count(*) FROM {bars} WHERE abs(ret_pc) > 0.31"),
            "worst": [list(r) for r in con.execute(
                f"SELECT ts_code, trade_date, round(ret_pc, 4), is_delisting, board FROM {bars} "
                f"WHERE ret_pc IS NOT NULL ORDER BY ret_pc LIMIT 5").fetchall()],
        }

        fin: dict[str, Any] = {}
        before_total = 0
        for table in FIN_TABLES:
            path = cur / f"{table}.parquet"
            if not path.exists():
                fin[table] = {"present": False}
                continue
            src = f"read_parquet('{path.as_posix()}')"
            rows, before, late, multi = con.execute(f"""
                SELECT count(*),
                       sum(CASE WHEN avail_date < end_date THEN 1 ELSE 0 END),
                       sum(CASE WHEN date_diff('day', strptime(end_date, '%Y%m%d'), strptime(avail_date, '%Y%m%d')) > 400 THEN 1 ELSE 0 END),
                       (SELECT count(*) FROM (SELECT ts_code, end_date FROM {src} WHERE report_type = '1'
                                              GROUP BY 1, 2 HAVING count(*) > 1))
                FROM {src}""").fetchone()
            periods, missing_original = con.execute(f"""
                SELECT count(*), sum(CASE WHEN date_diff('day', strptime(first_ann, '%Y%m%d'), strptime(first_avail, '%Y%m%d')) > 10 THEN 1 ELSE 0 END)
                FROM (SELECT ts_code, end_date, min(avail_date) AS first_avail, min(ann_date) AS first_ann
                      FROM {src} WHERE report_type IN ('1', '5') AND ann_date IS NOT NULL GROUP BY 1, 2)""").fetchone()
            by_type = dict(con.execute(f"SELECT report_type, count(*) FROM {src} GROUP BY 1 ORDER BY 1").fetchall())
            before_total += int(before or 0)
            fin[table] = {"present": True, "rows": rows, "by_report_type": by_type,
                          "avail_before_period_end": int(before or 0),
                          "avail_over_400_days_after_period_end": int(late or 0),
                          "stock_periods_with_multiple_versions": int(multi or 0),
                          "stock_periods": int(periods or 0),
                          "stock_periods_original_version_missing": int(missing_original or 0)}
        info["financials"] = fin
        if any(v.get("present") for v in fin.values()):
            gates["fin_avail_before_period_end"] = _check(before_total, THRESHOLDS["fin_avail_before_period_end"])
    finally:
        con.close()
    return {"pass": all(g["pass"] for g in gates.values()), "sample_mode": sample_mode, "gates": gates, "info": info}
