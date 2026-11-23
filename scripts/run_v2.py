"""第二批因子：计算、单因子评估、并入模型后的增量检验（由设计方提供，受保护）。

1. 计算 24 个 v2 因子
2. 对 v1 + v2 共 50 个因子做单因子评估（开发区）
3. 生成 v1+v2 的中性化面板
4. 用与 T011 相同的加快设定，分别训练"只用 v1"和"v1 + v2"的排序模型与回归模型，比较打分与组合
5. 新模型的风格归因与剔除最小 30% 股票后的表现

用法：
    python scripts/run_v2.py [--data-dir ...] [--task T012] [--threads 8]
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
from perq.research import attribution, backtest_report, evaluate, gbm, panel  # noqa: E402

FAST = {"window": None, "day_step": 4, "rounds": 200, "retrain_every": 42, "seed": 42}
MODELS = {
    "rank_v1_fast": dict(FAST, objective="rank", version="v1"),
    "rank_v12_fast": dict(FAST, objective="rank", version="v1+v2"),
    "reg_v1_fast": dict(FAST, objective="reg", version="v1"),
    "reg_v12_fast": dict(FAST, objective="reg", version="v1+v2"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--task", default="T012")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--start", default="20160104")
    parser.add_argument("--end", default="20241231")
    parser.add_argument("--skip", default="", help="逗号分隔，可选 factors,evaluate,panel,models")
    args = parser.parse_args()
    skip = set(filter(None, args.skip.split(",")))

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    out = c.REPO_ROOT / "results" / args.task

    def stage(name: str, fn):
        if name in skip:
            print(f"跳过 {name}")
            return None
        t = time.time()
        result = fn()
        print(f"{name} 完成，{round(time.time() - t)} 秒")
        return result

    summary = stage("factors", lambda: factors_v2.build_factors_v2(root, args.memory, 4))
    if summary:
        c.write_json(out / "factors_v2_summary.json", summary)

    def run_eval():
        report = evaluate.evaluate(root, end=args.end, versions=("v1", "v2"))
        c.write_json(out / "factor_report_v12.json", report)
        table = evaluate.summary_table(report, 5)
        with (out / "factor_table_v12.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)

    stage("evaluate", run_eval)
    for version in ("v1", "v1+v2"):
        info = stage("panel", lambda v=version: panel.build_panel(root, version=v)) if not (
            version == "v1" and panel.panel_dir(root, "v1").exists()) else None
        if info:
            c.write_json(out / f"panel_{version.replace('+', '_')}.json", info)

    def run_models():
        for name, spec in MODELS.items():
            t = time.time()
            s = gbm.walk_forward(root, name, start=args.start, end=args.end, threads=args.threads, **spec)
            s["seconds"] = round(time.time() - t)
            c.write_json(out / f"model_{name}.json", s)
            print(name, s["score_ic"], s["seconds"], "秒")

    stage("models", run_models)

    market = MarketData.from_curated(root)
    styles = attribution.style_returns(root, args.start, args.end)
    rows = []
    import pandas as pd

    for name in MODELS:
        if not (root / "scores" / name).exists():
            continue
        for min_size in (0.0, 0.3):
            rep = backtest_report.run(root, name, args.start, args.end, n=30, rebalance=5, market=market,
                                      keep_series=True, min_size_pct=min_size)
            series = rep.pop("_series")
            excess = pd.Series(np.array(series["strategy"]) - np.array(series["benchmark"]), index=series["dates"])
            rep["attribution"] = attribution.regress(excess, styles)
            rep["exposures"] = attribution.exposures(root, series["holdings"])
            label = name + ("_剔除最小30%" if min_size else "")
            c.write_json(out / f"backtest_{name}{'_exmicro' if min_size else ''}.json", rep)
            a = rep["attribution"]
            rows.append({"label": label, "annual_return": rep["annual_return"], "annual_excess": rep["annual_excess"],
                         "information_ratio": rep["information_ratio"], "max_drawdown": rep["max_drawdown"],
                         "years_with_positive_excess": rep["years_with_positive_excess"],
                         "alpha_after_styles": a.get("alpha_annual"), "alpha_t": a.get("alpha_t_nw"),
                         "smallest_quintile_weight": rep["exposures"].get("size_quintile_weight", {}).get("Q1"),
                         "turnover": rep["annual_turnover_two_sided"]})
            print(rows[-1])
    if rows:
        with (out / "v2_comparison.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    c.write_run_meta(out, started, {"task": args.task}, name="run_meta_v2.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
