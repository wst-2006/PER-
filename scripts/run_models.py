"""模型与组合设计的系统比较（由设计方提供，受保护）。

1. 生成中性化因子面板
2. 滚动训练三个梯度提升树模型：回归目标（近 500 日）、排序目标（近 500 日）、排序目标（全部历史）
3. 对全部打分方案 × 组合设计（持股数 × 调仓间隔）逐一回测，报告结果分布
4. 对默认设计做交易成本加倍的压力测试

用法：
    python scripts/run_models.py [--data-dir ...] [--task T010] [--threads 8]
只用开发区（截至 2024-12-31）。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.engine.costs import CostModel  # noqa: E402
from perq.engine.market import MarketData  # noqa: E402
from perq.research import backtest_report, gbm, panel  # noqa: E402

MODELS = {
    "gbm_reg_w500": {"objective": "reg", "window": 500},
    "gbm_rank_w500": {"objective": "rank", "window": 500},
    "gbm_rank_all": {"objective": "rank", "window": None},
}
LINEAR = ["prior", "icir"]
GRID_N = [20, 30, 50]
GRID_REBALANCE = [5, 10, 20]
DEFAULT = (30, 5)
FIELDS = ("scheme", "n", "rebalance", "cost", "annual_return", "benchmark_annual_return", "annual_excess",
          "information_ratio", "max_drawdown", "max_relative_drawdown", "years_with_positive_excess",
          "annual_turnover_two_sided", "fees_share_of_start_nav_per_year", "seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--task", default="T010")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--start", default="20160104")
    parser.add_argument("--end", default="20241231")
    parser.add_argument("--skip-panel", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    out = c.REPO_ROOT / "results" / args.task
    if not args.skip_panel:
        t = time.time()
        info = panel.build_panel(root)
        info["seconds"] = round(time.time() - t)
        c.write_json(out / "panel_summary.json", info)
        print("面板：", info)
    if not args.skip_models:
        for name, spec in MODELS.items():
            t = time.time()
            summary = gbm.walk_forward(root, name, start=args.start, end=args.end, threads=args.threads, **spec)
            summary["seconds"] = round(time.time() - t)
            c.write_json(out / f"model_{name}.json", summary)
            print(name, summary["score_ic"], f"{summary['retrains']} 次重训，{summary['seconds']} 秒")

    market = MarketData.from_curated(root)
    rows = []
    schemes = LINEAR + list(MODELS)
    for scheme in schemes:
        for n in GRID_N:
            for rebalance in GRID_REBALANCE:
                t = time.time()
                rep = backtest_report.run(root, scheme, args.start, args.end, n=n, rebalance=rebalance, market=market)
                rep["seconds"] = round(time.time() - t)
                rep["cost"] = "1x"
                if (n, rebalance) == DEFAULT:
                    c.write_json(out / f"backtest_{scheme}.json", rep)
                rows.append({k: rep.get(k) for k in FIELDS})
                print({k: rows[-1][k] for k in ("scheme", "n", "rebalance", "annual_excess", "information_ratio")})
        doubled = CostModel(commission_rate=0.0005, min_commission=5.0, slippage=0.002)
        t = time.time()
        rep = backtest_report.run(root, scheme, args.start, args.end, n=DEFAULT[0], rebalance=DEFAULT[1],
                                  costs=doubled, market=market)
        rep["seconds"], rep["cost"] = round(time.time() - t), "2x"
        rows.append({k: rep.get(k) for k in FIELDS})
    with (out / "design_grid.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    c.write_run_meta(out, started, {"task": args.task}, name="run_meta_models.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
