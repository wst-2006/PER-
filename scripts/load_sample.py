"""把 T004 的样本包装进一个独立的原始层目录，用来在样本上试跑整理层（由设计方提供，受保护）。

用法：
    python scripts/load_sample.py --out E:\\perq_data\\_sample
    （默认从 samples/ 目录读取已解压的 parquet，或 samples/perq_sample.zip）
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.data.store import RawStore  # noqa: E402

BY_DATE = {"stock_daily": "daily", "stock_adj_factor": "adj_factor", "stock_daily_basic": "daily_basic",
           "stock_stk_limit": "stk_limit", "stock_moneyflow": "moneyflow", "stock_suspend_d": "suspend_d"}
BY_PERIOD = {"stock_income": "income_vip_rt1", "stock_balancesheet": "balancesheet_vip_rt1",
             "stock_cashflow": "cashflow_vip_rt1"}


def load(sample_dir: Path, out: Path, snapshot_key: str = "20260101") -> dict[str, int]:
    import pandas as pd

    store = RawStore(out)
    counts: dict[str, int] = {}

    def read(name: str):
        path = sample_dir / f"{name}.parquet"
        return pd.read_parquet(path) if path.exists() else None

    for source, target in BY_DATE.items():
        frame = read(source)
        if frame is None:
            continue
        for day, part in frame.groupby("trade_date"):
            store.write(target, str(day), part.reset_index(drop=True))
        counts[target] = len(frame)
    for source, target in BY_PERIOD.items():
        frame = read(source)
        if frame is None:
            continue
        for period, part in frame.groupby("end_date"):
            store.write(target, str(period), part.reset_index(drop=True))
        counts[target] = len(frame)
    basic = read("stock_basic_all")
    if basic is not None:
        for status, part in basic.groupby("list_status"):
            store.write(f"stock_basic_{status}", snapshot_key, part.reset_index(drop=True))
        counts["stock_basic"] = len(basic)
    for source, target, key in (("trade_cal", "trade_cal", snapshot_key), ("stock_namechange", "namechange", "sample"),
                                ("stock_sw_member", "sw_member_sample_Y", snapshot_key)):
        frame = read(source)
        if frame is not None:
            store.write(target, key, frame)
            counts[target] = len(frame)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", default=str(c.REPO_ROOT / "samples"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sample_dir = Path(args.samples)
    archive = sample_dir / "perq_sample.zip"
    if not (sample_dir / "stock_daily.parquet").exists() and archive.exists():
        with zipfile.ZipFile(archive) as z:
            z.extractall(sample_dir)
    counts = load(sample_dir, Path(args.out))
    for name, rows in counts.items():
        print(f"{name:24s} {rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
