"""候选方案检验（由设计方提供，受保护）：

1. 重算第二批因子（事件类因子"未发生"记 0）与 v1+v2 面板
2. 回归目标、全部历史、v1+v2 的模型换三个随机种子训练
3. 对种子 42 的模型做组合设计网格：总仓位控制（无 / 波动率目标 / 趋势 / 两者）× 小市值上限（不限 / 30%）× 调仓间隔（5 / 10 天）
4. 另两个种子在两种代表性设计下回测，检验结论是否依赖种子
5. 风格归因；剔除 2016 年后的结果；按累计试验次数计算收缩夏普

用法：
    python scripts/run_candidate.py [--data-dir ...] [--task T013] [--threads 8]
只用开发区。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.engine.market import MarketData  # noqa: E402
from perq.features import factors_v2  # noqa: E402
from perq.research import attribution, backtest_report, gbm, panel  # noqa: E402
from perq.research.stats import deflated_sharpe  # noqa: E402

SPEC = {"objective": "reg", "version": "v1+v2", "window": None, "day_step": 4, "rounds": 200, "retrain_every": 42}
SEEDS = {"cand_s42": 42, "cand_s7": 7, "cand_s2024": 2024}
GRID = [(mode, small, reb) for mode in ("none", "vol_target", "trend", "both") for small in (1.0, 0.3) for reb in (5, 10)]
REPRESENTATIVE = [("none", 1.0, 5), ("both", 0.3, 5)]
PRIOR_TRIALS = 3 + 45 + 14 + 8          # T009、T010、T011、T012 已做过的组合回测次数


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--task", default="T013")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--start", default="20160104")
    parser.add_argument("--end", default="20241231")
    parser.add_argument("--skip", default="", help="逗号分隔，可选 factors,panel,models")
    args = parser.parse_args()
    skip = set(filter(None, args.skip.split(",")))

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    out = c.REPO_ROOT / "results" / args.task
    timings: dict[str, int] = {}

    def stage(name, fn):
        if name in skip:
            print(f"跳过 {name}")
            return None
        t = time.time()
        result = fn()
        timings[name] = round(time.time() - t)
        print(f"{name} 完成，{timings[name]} 秒")
        return result

    s = stage("factors", lambda: factors_v2.build_factors_v2(root, args.memory, 4))
    if s:
        c.write_json(out / "factors_v2_summary.json", s)
    p = stage("panel", lambda: panel.build_panel(root, version="v1+v2"))
    if p:
        c.write_json(out / "panel_v1_v2.json", p)

    def models():
        for name, seed in SEEDS.items():
            t = time.time()
            m = gbm.walk_forward(root, name, start=args.start, end=args.end, threads=args.threads, seed=seed, **SPEC)
            m["seconds"] = round(time.time() - t)
            c.write_json(out / f"model_{name}.json", m)
            print(name, m["score_ic"], m["seconds"], "秒")

    stage("models", models)

    import pandas as pd

    market = MarketData.from_curated(root)
    styles = attribution.style_returns(root, args.start, args.end)
    rows, irs = [], []

    def run(name: str, mode: str, small: float, reb: int, save: bool) -> None:
        t = time.time()
        rep = backtest_report.run(root, name, args.start, args.end, n=30, rebalance=reb, market=market,
                                  keep_series=True, max_small_share=small, exposure_mode=mode)
        series = rep.pop("_series")
        excess = pd.Series(np.array(series["strategy"]) - np.array(series["benchmark"]), index=series["dates"])
        rep["attribution"] = attribution.regress(excess, styles)
        rep["exposures"] = attribution.exposures(root, series["holdings"])
        by = rep["by_year"]
        later = [y for y in by if y != "2016"]
        rep["annual_excess_ex2016"] = round(float(np.prod([1 + by[y]["excess"] for y in later]) ** (1 / len(later)) - 1), 4)
        rep["annual_return_ex2016"] = round(float(np.prod([1 + by[y]["strategy"] for y in later]) ** (1 / len(later)) - 1), 4)
        rep["daily_excess_skew"], rep["daily_excess_kurtosis"] = float(excess.skew()), float(excess.kurt() + 3)
        rep["n_days"] = len(excess)
        label = f"{name}|{mode}|small{small}|reb{reb}"
        if save:
            c.write_json(out / f"backtest_{name}_{mode}_small{int(small * 100)}_reb{reb}.json", rep)
        a = rep["attribution"]
        rows.append({"label": label, "annual_return": rep["annual_return"], "annual_excess": rep["annual_excess"],
                     "annual_return_ex2016": rep["annual_return_ex2016"], "annual_excess_ex2016": rep["annual_excess_ex2016"],
                     "information_ratio": rep["information_ratio"], "max_drawdown": rep["max_drawdown"],
                     "max_relative_drawdown": rep["max_relative_drawdown"],
                     "years_with_positive_excess": rep["years_with_positive_excess"],
                     "mean_exposure": rep.get("exposure", {}).get("mean_exposure", 1.0),
                     "alpha_after_styles": a.get("alpha_annual"), "alpha_t": a.get("alpha_t_nw"),
                     "smallest_quintile_weight": rep["exposures"].get("size_quintile_weight", {}).get("Q1"),
                     "turnover": rep["annual_turnover_two_sided"], "fees": rep["fees_share_of_start_nav_per_year"],
                     "seconds": round(time.time() - t)})
        irs.append((label, rep["information_ratio"], rep["daily_excess_skew"], rep["daily_excess_kurtosis"], rep["n_days"]))
        print(rows[-1])

    for mode, small, reb in GRID:
        run("cand_s42", mode, small, reb, save=True)
    for name in ("cand_s7", "cand_s2024"):
        for mode, small, reb in REPRESENTATIVE:
            run(name, mode, small, reb, save=True)

    with (out / "candidate_grid.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # 收缩夏普：试验总数 = 以前的回测次数 + 本次；离散度用 T010 网格与本次网格的全部信息比率
    prev = c.REPO_ROOT / "results" / "T010" / "design_grid.csv"
    all_ir = [r[1] for r in irs]
    if prev.exists():
        g = pd.read_csv(prev, encoding="utf-8-sig")
        all_ir += g.loc[g["cost"] == "1x", "information_ratio"].tolist()
    var = float(np.var(np.array(all_ir) / np.sqrt(244), ddof=1))
    n_trials = PRIOR_TRIALS + len(irs)
    dsr = {}
    for label, ir, skew, kurt, n in irs:
        if label.startswith("cand_s42|none|small1.0|reb5") or label.startswith("cand_s42|both|small0.3|reb5"):
            dsr[label] = deflated_sharpe(ir / np.sqrt(244), n, n_trials, var, skew, kurt)
    c.write_json(out / "deflated_sharpe.json", {
        "units": "sr 与 sr0 为日频夏普（日均超额 ÷ 日标准差），乘以 √244 为年化信息比率",
        "n_trials": n_trials, "trial_sr_variance_daily": var, "results": dsr})
    c.write_run_meta(out, started, {"task": args.task, "stage_seconds": timings}, name="run_meta_candidate.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
