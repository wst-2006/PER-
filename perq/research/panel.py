"""中性化因子面板：把每日截面的"去极值、标准化、行业与市值中性化"结果存下来，供模型训练反复使用。

每行：股票、日期、行业、流通市值、26 个中性化因子值（float32），以及以 y_ 开头的标签列。
y_ 列只能用作训练目标和事后评估，不能当特征。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np

from ..features import registry
from ..features.factors import VERSION
from .evaluate import load_year, prepare_day, year_dirs

HORIZONS = (5, 10, 20)


def panel_dir(root: str | Path, version: str = VERSION) -> Path:
    """version 可以是 "v1" 或 "v1+v2" 这样的组合。"""
    return Path(root) / "features" / f"{version.replace('+', '_')}_neu"


def build_panel(root: str | Path, *, min_stocks: int = 200, version: str = VERSION) -> dict[str, Any]:
    import pandas as pd

    root = Path(root)
    versions = tuple(version.split("+"))
    names = list(registry.meta(versions))
    target = panel_dir(root, version)
    if target.exists():
        shutil.rmtree(target)
    rows = days = 0
    for year in year_dirs(root / "features" / "v1"):
        frame = load_year(root, year, HORIZONS, versions=versions)
        if frame is None or len(frame) == 0:
            continue
        parts = []
        for day, part in frame.groupby("trade_date", sort=True):
            if len(part) < min_stocks:
                continue
            _z, neu = prepare_day(part, names)
            out = pd.DataFrame(neu.astype("float32"), columns=names)
            out.insert(0, "ts_code", part["ts_code"].to_numpy())
            out.insert(1, "trade_date", day)
            out["sw_l1"] = part["sw_l1"].to_numpy()
            out["circ_mv"] = part["circ_mv"].to_numpy(dtype="float32")
            for h in HORIZONS:
                out[f"y_fwd_{h}"] = part[f"fwd_{h}"].to_numpy(dtype="float32")
            parts.append(out)
            days += 1
        if parts:
            folder = target / f"year={year}"
            folder.mkdir(parents=True, exist_ok=True)
            data = pd.concat(parts, ignore_index=True)
            data.to_parquet(folder / "part-0.parquet", index=False)
            rows += len(data)
    return {"rows": rows, "days": days, "factors": len(names), "path": target.as_posix()}


def load_panel(root: str | Path, start: str, end: str, version: str = VERSION) -> Any:
    import duckdb

    base = panel_dir(root, version).as_posix()
    return duckdb.sql(f"""SELECT * FROM read_parquet('{base}/*/*.parquet', hive_partitioning=true)
                          WHERE trade_date BETWEEN '{start}' AND '{end}' ORDER BY trade_date, ts_code""").df()


def panel_days(root: str | Path, version: str = VERSION) -> list[str]:
    import duckdb

    base = panel_dir(root, version).as_posix()
    return [r[0] for r in duckdb.sql(
        f"SELECT DISTINCT trade_date FROM read_parquet('{base}/*/*.parquet', hive_partitioning=true) ORDER BY 1").fetchall()]


def daily_rank_target(frame: Any, horizon: int) -> np.ndarray:
    """每日截面内的收益百分位，减 0.5 后落在 [-0.5, 0.5]。截面排名去掉了大盘涨跌，只留相对强弱。"""
    y = frame[f"y_fwd_{horizon}"]
    return (y.groupby(frame["trade_date"]).rank(pct=True) - 0.5).to_numpy()
