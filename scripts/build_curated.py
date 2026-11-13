"""由原始层重建整理层，并运行数据质量检查（由设计方提供，受保护）。

用法：
    python scripts/build_curated.py
    python scripts/build_curated.py --data-dir E:\\perq_data\\_sample --sample
输出：
    results/<task>/curated_summary.json
    results/<task>/dq_report.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.data import curated, quality  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--sample", action="store_true", help="样本数据：跳过覆盖率门禁")
    parser.add_argument("--task", default="T007")
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    summary = curated.build_all(root, args.memory, args.threads)
    report = quality.run(root, args.memory, sample_mode=args.sample)
    result_dir = c.REPO_ROOT / "results" / args.task
    suffix = "_sample" if args.sample else ""
    c.write_json(result_dir / f"curated_summary{suffix}.json", summary)
    c.write_json(result_dir / f"dq_report{suffix}.json", report)
    c.write_run_meta(result_dir, started, {"task": args.task}, name=f"run_meta_curated{suffix}.json")
    print(f"日线主表 {summary['bars']['rows']} 行，{summary['bars']['first_date']} 至 {summary['bars']['last_date']}")
    print(f"数据质量门禁：{'通过' if report['pass'] else '未通过'}")
    for name, gate in report["gates"].items():
        print(f"  {'通过' if gate['pass'] else '未过'}  {name} = {gate['value']}（阈值 {gate['threshold']}）")
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
