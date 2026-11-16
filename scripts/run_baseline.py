"""基线闭环：因子合成打分 → 引擎回测 → 与股票池等权基准比较（由设计方提供，受保护）。

用法：
    python scripts/run_baseline.py [--data-dir ...] [--task T009]
默认只用开发区（截至 2024-12-31）。
输出：
    <数据目录>/scores/<方案>/
    results/<task>/composite_summary.json   三种合成方案的打分 IC 与权重轨迹
    results/<task>/baseline_<方案>.json      每种方案的回测结果
    results/<task>/baseline_table.csv       汇总表
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.engine.market import MarketData  # noqa: E402
from perq.research import backtest_report, composite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--task", default="T009")
    parser.add_argument("--start", default="20160104", help="回测起点。2015 年只用于积累因子历史")
    parser.add_argument("--end", default="20241231")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--rebalance", type=int, default=5)
    parser.add_argument("--skip-scores", action="store_true")
    parser.add_argument("--min-stocks", type=int, default=200)
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    result_dir = c.REPO_ROOT / "results" / args.task
    if not args.skip_scores:
        summary = composite.build_scores(root, end=args.end, min_stocks=args.min_stocks)
        c.write_json(result_dir / "composite_summary.json", summary)
        print("打分 IC：", summary["schemes"])
    market = MarketData.from_curated(root)
    rows = []
    for scheme in composite.SCHEMES:
        report = backtest_report.run(root, scheme, args.start, args.end, n=args.n, rebalance=args.rebalance, market=market)
        c.write_json(result_dir / f"baseline_{scheme}.json", report)
        row = {k: report.get(k) for k in ("scheme", "annual_return", "benchmark_annual_return", "annual_excess",
                                          "information_ratio", "max_drawdown", "max_relative_drawdown",
                                          "years_with_positive_excess", "annual_turnover_two_sided",
                                          "fees_share_of_start_nav_per_year", "average_positions")}
        rows.append(row)
        print(row)
    with (result_dir / "baseline_table.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    c.write_run_meta(result_dir, started, {"task": args.task}, name="run_meta_baseline.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
