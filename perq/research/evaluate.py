"""单因子评估。按年读入因子与标签，逐日计算，再汇总。

对每个因子、每个持有期输出：
- Rank IC（原始值，以及对行业和市值中性化之后的值）：均值、ICIR、Newey-West t 值、为正的比例、分年均值
- 剔除流通市值最小 30% 之后的 IC（检验结论是否依赖微盘股）
- 十分组平均超额收益：等权与流通市值加权两种口径，首尾差，单调性
- 第十组（因子值最大）成分的日换手率
另输出因子之间的平均秩相关矩阵。

评估范围：股票池内、次日能买进（entry_ok）、标签非空的样本。
超额收益 = 持有期收益减去当日评估范围内的等权平均。
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np

from ..features import registry
from ..features.factors import VERSION
from .stats import column_corr, neutralize, newey_west_tstat, rank_columns, winsorize_zscore

GROUPS = 10
MICRO_CUT = 0.30
MIN_STOCKS = 200


def prepare_day(part: Any, names: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """一天的截面：去极值标准化，再对行业和市值中性化。返回（标准化值，中性化值）。"""
    raw = part[names].to_numpy(dtype=float)
    mv = part["circ_mv"].to_numpy(dtype=float)
    ln_mv = np.log(np.where(mv > 0, mv, np.nan))
    industry = part["sw_l1"].fillna("NA").to_numpy()
    z = winsorize_zscore(raw)
    size_z = winsorize_zscore(ln_mv.reshape(-1, 1)).ravel()
    neu = neutralize(z, industry, size_z)
    if "ln_mv" in names:                     # 市值因子自身只对行业中性化
        j = names.index("ln_mv")
        neu[:, j] = neutralize(z[:, [j]], industry, None).ravel()
    return z, neu


def year_dirs(base: Path) -> list[str]:
    return sorted(p.name.split("=")[1] for p in base.glob("year=*") if p.is_dir())


def load_year(root: Path, year: str, horizons: tuple[int, ...], version: str = VERSION,
              labels_version: str = "v1", versions: tuple[str, ...] | None = None) -> Any:
    """读取一年的因子与标签。versions 为要合并的因子版本，默认只用 version 一个。"""
    import pandas as pd

    def read(base: Path, columns: list[str]) -> Any:
        files = sorted((base / f"year={year}").glob("*.parquet"))
        if not files:
            return None
        return pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)

    feats = registry.read_year(root, year, versions or (version,))
    labs = read(root / "labels" / labels_version, ["ts_code", "trade_date", "entry_ok"] + [f"fwd_{h}" for h in horizons])
    if feats is None or labs is None:
        return None
    frame = feats.merge(labs, on=["ts_code", "trade_date"], how="inner")
    return frame[frame["entry_ok"].astype(bool)]


def evaluate(root: str | Path, horizons: tuple[int, ...] = (5, 10, 20), version: str = VERSION,
             labels_version: str = "v1", min_stocks: int = MIN_STOCKS, start: str | None = None,
             end: str | None = None, versions: tuple[str, ...] | None = None) -> dict[str, Any]:
    root = Path(root)
    versions = versions or (version,)
    info = registry.meta(versions)
    names = list(info)
    k = len(names)
    days: list[str] = []
    ic_raw = {h: [] for h in horizons}
    ic_neu = {h: [] for h in horizons}
    ic_exmicro = {h: [] for h in horizons}
    group_ew = {h: np.zeros((k, GROUPS)) for h in horizons}
    group_vw = {h: np.zeros((k, GROUPS)) for h in horizons}
    group_n = {h: np.zeros((k, GROUPS)) for h in horizons}
    spread = {h: [] for h in horizons}
    turnover_sum, turnover_n = np.zeros(k), np.zeros(k)
    prev_top: list[set | None] = [None] * k
    corr_sum, corr_cnt = np.zeros((k, k)), np.zeros((k, k))
    n_stocks: list[int] = []

    for year in year_dirs(root / "features" / version):
        frame = load_year(root, year, horizons, version, labels_version, versions)
        if frame is None or len(frame) == 0:
            continue
        for day, part in frame.groupby("trade_date", sort=True):
            if (start and day < start) or (end and day > end) or len(part) < min_stocks:
                continue
            z, neu = prepare_day(part, names)
            mv = part["circ_mv"].to_numpy(dtype=float)
            r_raw, r_neu = rank_columns(z), rank_columns(neu)
            big = mv >= np.nanquantile(mv, MICRO_CUT)
            codes = part["ts_code"].to_numpy()
            days.append(day)
            n_stocks.append(len(part))

            with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                filled = np.where(np.isfinite(r_neu), r_neu, np.nanmean(r_neu, axis=0))
                c = np.corrcoef(np.nan_to_num(filled), rowvar=False)
            finite = np.isfinite(c)
            corr_sum += np.where(finite, c, 0.0)
            corr_cnt += finite

            # 十分组（按中性化后的值）
            pct = r_neu / np.sum(np.isfinite(r_neu), axis=0)
            group = np.clip(np.ceil(pct * GROUPS) - 1, 0, GROUPS - 1)
            for j in range(k):
                top = set(codes[group[:, j] == GROUPS - 1])
                if prev_top[j] is not None and top:
                    turnover_sum[j] += 1 - len(top & prev_top[j]) / len(top)
                    turnover_n[j] += 1
                prev_top[j] = top

            for h in horizons:
                y = part[f"fwd_{h}"].to_numpy(dtype=float)
                ok = np.isfinite(y)
                if ok.sum() < min_stocks:
                    for store in (ic_raw, ic_neu, ic_exmicro):
                        store[h].append(np.full(k, np.nan))
                    spread[h].append(np.full(k, np.nan))
                    continue
                excess = y - np.nanmean(y)
                ry = rank_columns(np.where(ok, y, np.nan).reshape(-1, 1)).ravel()
                ic_raw[h].append(column_corr(r_raw, ry))
                ic_neu[h].append(column_corr(r_neu, ry))
                ry_big = rank_columns(np.where(ok & big, y, np.nan).reshape(-1, 1)).ravel()
                ic_exmicro[h].append(column_corr(np.where(big[:, None], r_neu, np.nan), ry_big))
                day_spread = np.full(k, np.nan)
                w = np.where(np.isfinite(mv) & ok, mv, 0.0)
                for j in range(k):
                    valid = ok & np.isfinite(group[:, j])
                    g = group[valid, j].astype(int)
                    cnt = np.bincount(g, minlength=GROUPS)
                    if cnt.min() == 0:
                        continue
                    ew = np.bincount(g, weights=excess[valid], minlength=GROUPS) / cnt
                    wsum = np.bincount(g, weights=w[valid], minlength=GROUPS)
                    vw = np.bincount(g, weights=excess[valid] * w[valid], minlength=GROUPS) / np.where(wsum > 0, wsum, np.nan)
                    group_ew[h][j] += ew
                    group_vw[h][j] += np.nan_to_num(vw)
                    group_n[h][j] += 1
                    day_spread[j] = ew[-1] - ew[0]
                spread[h].append(day_spread)

    if not days:
        return {"status": "empty", "factors": {}}
    years = np.array([d[:4] for d in days])
    report: dict[str, Any] = {
        "status": "ok", "version": "+".join(versions), "first_date": days[0], "last_date": days[-1], "days": len(days),
        "median_stocks_per_day": int(np.median(n_stocks)), "horizons": list(horizons), "groups": GROUPS,
        "factors": {},
    }

    def describe(series: np.ndarray, h: int) -> dict[str, Any]:
        x = series[np.isfinite(series)]
        if len(x) < 20:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean": round(float(x.mean()), 5), "std": round(float(x.std()), 5),
                "icir": round(float(x.mean() / x.std()), 4) if x.std() > 0 else None,
                "t_nw": round(newey_west_tstat(series, h), 2), "positive_share": round(float((x > 0).mean()), 4)}

    for j, name in enumerate(names):
        family, prior, logic = info[name]
        entry: dict[str, Any] = {"family": family, "prior_sign": prior, "logic": logic,
                                 "top_group_daily_turnover": round(float(turnover_sum[j] / turnover_n[j]), 4) if turnover_n[j] else None,
                                 "horizons": {}}
        for h in horizons:
            raw_s, neu_s, big_s = (np.array(store[h])[:, j] for store in (ic_raw, ic_neu, ic_exmicro))
            spr = np.array(spread[h])[:, j]
            n = group_n[h][j][0]
            ew = group_ew[h][j] / n if n else np.full(GROUPS, np.nan)
            vw = group_vw[h][j] / n if n else np.full(GROUPS, np.nan)
            order = np.arange(GROUPS)
            mono = float(np.corrcoef(order, rank_columns(ew.reshape(-1, 1)).ravel())[0, 1]) if n else None
            by_year = {y: round(float(np.nanmean(neu_s[years == y])), 5) for y in sorted(set(years))
                       if np.isfinite(neu_s[years == y]).sum() >= 20}
            entry["horizons"][str(h)] = {
                "ic_raw": describe(raw_s, h), "ic_neutral": describe(neu_s, h), "ic_neutral_ex_micro": describe(big_s, h),
                "ic_neutral_by_year": by_year,
                "years_same_sign_share": round(float(np.mean([np.sign(v) == np.sign(np.nanmean(neu_s)) for v in by_year.values()])), 3) if by_year else None,
                "group_excess_ew": [round(float(v), 6) for v in ew], "group_excess_vw": [round(float(v), 6) for v in vw],
                "top_minus_bottom_ew": round(float(ew[-1] - ew[0]), 6) if n else None,
                "top_minus_bottom_vw": round(float(vw[-1] - vw[0]), 6) if n else None,
                "spread_t_nw": round(newey_west_tstat(spr, h), 2),
                "monotonicity": None if mono is None else round(mono, 3),
            }
        report["factors"][name] = entry
    if corr_cnt.max() > 0:
        avg = np.where(corr_cnt > 0, corr_sum / np.where(corr_cnt > 0, corr_cnt, 1), np.nan)
        report["rank_correlation"] = {"names": names,
                                      "matrix": [[None if not np.isfinite(v) else round(float(v), 3) for v in row] for row in avg]}
        pairs = [(names[a], names[b], round(float(avg[a, b]), 3)) for a in range(k) for b in range(a + 1, k)
                 if np.isfinite(avg[a, b]) and abs(avg[a, b]) >= 0.7]
        report["highly_correlated_pairs"] = sorted(pairs, key=lambda p: -abs(p[2]))
    return report


def summary_table(report: dict[str, Any], horizon: int = 5) -> list[dict[str, Any]]:
    """每个因子一行的摘要，按中性化 IC 的 t 值绝对值排序。"""
    rows = []
    for name, entry in report.get("factors", {}).items():
        h = entry["horizons"].get(str(horizon), {})
        neu, big = h.get("ic_neutral", {}), h.get("ic_neutral_ex_micro", {})
        rows.append({
            "factor": name, "family": entry["family"], "prior_sign": entry["prior_sign"],
            "ic": neu.get("mean"), "icir": neu.get("icir"), "t_nw": neu.get("t_nw"),
            "ic_ex_micro": big.get("mean"), "t_ex_micro": big.get("t_nw"),
            "years_same_sign": h.get("years_same_sign_share"),
            "spread_ew": h.get("top_minus_bottom_ew"), "spread_vw": h.get("top_minus_bottom_vw"),
            "monotonicity": h.get("monotonicity"), "turnover": entry["top_group_daily_turnover"],
        })
    return sorted(rows, key=lambda r: -abs(r["t_nw"] or 0))
