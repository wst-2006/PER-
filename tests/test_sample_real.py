"""在真实样本包上的端到端检查。仓库不含数据：samples/ 不存在时自动跳过。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

pytestmark = pytest.mark.skipif(not (SAMPLES / "stock_daily.parquet").exists() and not (SAMPLES / "perq_sample.zip").exists(),
                                reason="没有样本包")


def test_sample_end_to_end(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import zipfile

    import load_sample
    from perq.data import curated, quality

    src = SAMPLES
    if not (SAMPLES / "stock_daily.parquet").exists():
        src = tmp_path / "unzipped"
        with zipfile.ZipFile(SAMPLES / "perq_sample.zip") as z:
            z.extractall(src)
    store_root = tmp_path / "store"
    load_sample.load(src, store_root)
    summary = curated.build_all(store_root, memory_limit="1GB", threads=2)
    report = quality.run(store_root, memory_limit="1GB", sample_mode=True)
    assert report["pass"], report["gates"]
    assert summary["bars"]["orphan_rows"] == 0
    bars = f"read_parquet('{(store_root / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)"
    con = duckdb.connect()
    # 北交所股票在上市日之前不应有任何记录
    stocks = f"read_parquet('{(store_root / 'curated' / 'stocks.parquet').as_posix()}')"
    assert con.execute(f"SELECT count(*) FROM {bars} b JOIN {stocks} s USING (ts_code) WHERE b.trade_date < s.list_date").fetchone()[0] == 0
    # 复权收益与"收盘价除以昨收"几乎处处一致
    assert report["gates"]["ret_mismatch_rate"]["value"] <= 0.001
