"""历史回填与每日更新（由设计方提供，受保护）。可随时中断，重跑会从断点继续。

用法：
    python scripts/backfill.py                      # 全部 tier 1 数据集
    python scripts/backfill.py --only daily,adj_factor
    python scripts/backfill.py --trial 2            # 试跑：每个数据集只拉最早和最近的 2 个分区
    python scripts/backfill.py --max-calls 300      # 最多 300 次调用
    python scripts/backfill.py --tiers 1,2
输出：
    results/<task>/backfill_summary.json（只含行数、分区数等统计，不含数据）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.data import backfill  # noqa: E402
from perq.data.store import RawStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--only", help="逗号分隔的数据集名")
    parser.add_argument("--tiers", default="1")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--trial", type=int, help="试跑：每个数据集只拉最早和最近的 N 个分区")
    parser.add_argument("--task", default="T006")
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    report = backfill.run(
        c.get_pro(), RawStore(root),
        tiers=tuple(int(t) for t in args.tiers.split(",")),
        only=[n.strip() for n in args.only.split(",")] if args.only else None,
        max_calls=args.max_calls,
        trial=args.trial,
    )
    result_dir = c.REPO_ROOT / "results" / args.task
    suffix = "_trial" if (args.max_calls or args.trial) else ""
    c.write_json(result_dir / f"backfill_summary{suffix}.json", report)
    c.write_run_meta(result_dir, started, {"task": args.task})
    failed = {k: v for k, v in report["datasets"].items() if v.get("failed") or v.get("skipped_after_failures")}
    print(f"\n完成：{report['calls']} 次调用，用时 {report['elapsed_seconds']} 秒；有失败的数据集 {len(failed)} 个")
    for name, stat in failed.items():
        print(f"  {name}: 失败 {stat.get('failed')} 次，最后错误：{stat.get('last_error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
