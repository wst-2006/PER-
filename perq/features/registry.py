"""因子登记表：把各批因子的说明合在一起，并按版本读取因子值。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .factors import FACTORS as V1
from .factors_v2 import FACTORS_V2

BASE_COLUMNS = ["ts_code", "trade_date", "sw_l1", "circ_mv"]


def meta(versions: tuple[str, ...] = ("v1",)) -> dict[str, tuple[str, int, str]]:
    """名称 → (信息族, 先验方向, 逻辑)。"""
    out: dict[str, tuple[str, int, str]] = {}
    for version in versions:
        if version == "v1":
            out.update({n: (fam, prior, logic) for n, (_e, fam, prior, logic) in V1.items()})
        elif version == "v2":
            out.update(FACTORS_V2)
        else:
            raise ValueError(f"未知因子版本：{version}")
    return out


def read_year(root: str | Path, year: str, versions: tuple[str, ...] = ("v1",)) -> Any:
    """读取一年的因子表：v1 提供行业与市值，其余版本按（股票，日期）并入。"""
    import pandas as pd

    root = Path(root)

    def read(version: str, columns: list[str]) -> Any:
        files = sorted((root / "features" / version / f"year={year}").glob("*.parquet"))
        if not files:
            return None
        return pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)

    frame = read("v1", BASE_COLUMNS + list(V1))
    if frame is None:
        return None
    for version in versions:
        if version == "v1":
            continue
        extra = read(version, ["ts_code", "trade_date"] + list(meta((version,))))
        if extra is not None:
            frame = frame.merge(extra, on=["ts_code", "trade_date"], how="left")
    keep = BASE_COLUMNS + list(meta(versions))
    return frame[[c for c in keep if c in frame.columns]]
