"""由整理层生成标签（由设计方提供，受保护）。

用法：
    python scripts/build_labels.py [--data-dir ...] [--task T007]
输出：
    <数据目录>/labels/v1/year=YYYY/
    results/<task>/labels_summary.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.features import labels  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--task", default="T007")
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    root = c.data_root(args.data_dir)
    stats = labels.build_labels(root, memory_limit=args.memory, threads=args.threads)
    c.write_json(c.REPO_ROOT / "results" / args.task / f"labels_summary{args.suffix}.json", stats)
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
