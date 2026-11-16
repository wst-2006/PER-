"""计算第一批因子并做单因子评估（由设计方提供，受保护）。

用法：
    python scripts/build_factors.py [--data-dir ...] [--task T008] [--skip-build] [--min-stocks 200]
输出：
    <数据目录>/features/v1/year=YYYY/
    results/<task>/factors_summary.json     因子覆盖率
    results/<task>/factor_report.json       每个因子的完整评估
    results/<task>/factor_table.csv         每个因子一行的摘要（5 日持有期）
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.features import factors  # noqa: E402
from perq.research import evaluate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--task", default="T008")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--min-stocks", type=int, default=evaluate.MIN_STOCKS)
    parser.add_argument("--end", default="20241231", help="评估截止日。默认只评估开发区，确认区与盲测区不看")
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    result_dir = c.REPO_ROOT / "results" / args.task
    if not args.skip_build:
        summary = factors.build_factors(root, args.memory, args.threads)
        c.write_json(result_dir / "factors_summary.json", summary)
        print(f"因子表 {summary['rows']} 行，{summary['first_date']} 至 {summary['last_date']}")
    report = evaluate.evaluate(root, min_stocks=args.min_stocks, end=args.end)
    report["evaluated_until"] = args.end
    c.write_json(result_dir / "factor_report.json", report)
    table = evaluate.summary_table(report, 5)
    if table:
        with (result_dir / "factor_table.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
        for row in table:
            print(f"{row['factor']:14s} IC={row['ic']!s:>9} t={row['t_nw']!s:>7} 剔除微盘 t={row['t_ex_micro']!s:>7} "
                  f"同号年份={row['years_same_sign']!s:>5} 首尾差(等权)={row['spread_ew']!s:>10}")
    c.write_run_meta(result_dir, started, {"task": args.task}, name="run_meta_factors.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
