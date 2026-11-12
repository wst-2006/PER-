"""引擎读取行情的入口。按年懒加载，内存里同时只保留一年。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

COLUMNS = ["ts_code", "trade_date", "open", "close", "up_limit", "down_limit", "adj_factor", "vol",
           "board", "limit_missing", "open_at_up", "open_at_down", "is_st", "is_delisting", "in_universe"]


class MarketData:
    def __init__(self, days: list[str], delist_dates: dict[str, str], last_bar: dict[str, str]) -> None:
        self._days = sorted(days)
        self._delist = delist_dates
        self._last_bar = last_bar
        self._cache: dict[str, Any] = {}
        self._cache_year: str | None = None
        self._root: Path | None = None
        self._frame: Any = None

    # ---------- 构造 ----------
    @classmethod
    def from_frame(cls, frame: Any, delist_dates: dict[str, str] | None = None,
                   days: list[str] | None = None) -> "MarketData":
        """测试与小数据用：直接传入日线主表。days 为完整交易日列表（含无任何股票交易的日子）。"""
        import pandas as pd

        frame = frame.copy()
        for column, default in (("limit_missing", False), ("is_st", False), ("is_delisting", False),
                                ("in_universe", True), ("board", "主板"), ("adj_factor", 1.0), ("vol", 1e9)):
            if column not in frame.columns:
                frame[column] = default
        for column in ("limit_missing", "is_st", "is_delisting", "in_universe"):
            frame[column] = frame[column].astype("boolean").fillna(column == "in_universe").astype(bool)
        if "open_at_up" not in frame.columns:
            frame["open_at_up"] = frame["open"] >= frame["up_limit"] - 0.0005
        if "open_at_down" not in frame.columns:
            frame["open_at_down"] = frame["open"] <= frame["down_limit"] + 0.0005
        frame["trade_date"] = frame["trade_date"].astype(str)
        last_bar = frame.groupby("ts_code")["trade_date"].max().to_dict()
        obj = cls(days or sorted(frame["trade_date"].unique()), delist_dates or {}, last_bar)
        obj._frame = frame
        obj._cache = {day: part.set_index("ts_code") for day, part in frame.groupby("trade_date")}
        obj._cache_year = "*"
        _ = pd
        return obj

    @classmethod
    def from_curated(cls, root: str | Path) -> "MarketData":
        import duckdb
        import pandas as pd

        root = Path(root)
        cur = root / "curated"
        days = pd.read_parquet(cur / "calendar.parquet")["cal_date"].astype(str).tolist()
        stocks = pd.read_parquet(cur / "stocks.parquet", columns=["ts_code", "delist_date"])
        delist = {r.ts_code: r.delist_date for r in stocks.itertuples() if isinstance(r.delist_date, str)}
        con = duckdb.connect()
        rows = con.execute(
            f"SELECT ts_code, max(trade_date) FROM read_parquet('{(cur / 'bars').as_posix()}/*/*.parquet', "
            f"hive_partitioning=true) GROUP BY 1").fetchall()
        con.close()
        obj = cls(days, delist, dict(rows))
        obj._root = cur / "bars"
        return obj

    # ---------- 读取 ----------
    def days(self, start: str, end: str) -> list[str]:
        return [d for d in self._days if start <= d <= end]

    def _load_year(self, year: str) -> None:
        import pandas as pd

        self._cache, self._cache_year = {}, year
        folder = self._root / f"year={year}"
        if not folder.is_dir():
            return
        frame = pd.concat([pd.read_parquet(f, columns=COLUMNS) for f in sorted(folder.glob("*.parquet"))],
                          ignore_index=True)
        self._cache = {day: part.set_index("ts_code") for day, part in frame.groupby("trade_date")}

    def day(self, date: str) -> Any:
        """返回当天的行情表（以股票代码为索引）；当天没有数据时返回 None。"""
        if self._cache_year != "*" and self._cache_year != date[:4]:
            self._load_year(date[:4])
        return self._cache.get(date)

    def delist_date(self, ts_code: str) -> str | None:
        return self._delist.get(ts_code)

    def last_bar_date(self, ts_code: str) -> str | None:
        return self._last_bar.get(ts_code)
