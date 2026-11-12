"""回填与每日更新。两者是同一段逻辑：找出"还没有、或需要刷新"的分区，逐个拉取写入原始层。

可随时中断，重新运行会从断点继续。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import catalog as cat
from .client import fetch_all
from .store import RawStore

MAX_CONSECUTIVE_FAILURES = 15


def load_open_days(store: RawStore) -> list[str]:
    import pandas as pd

    entries = store.entries("trade_cal")
    keys = sorted(k for k, e in entries.items() if e["status"] == "ok")
    if not keys:
        return []
    frame = pd.read_parquet(store.path("trade_cal", keys[-1]))
    return sorted(frame.loc[frame["is_open"].astype(str).isin(["1", "1.0"]), "cal_date"].astype(str).tolist())


def pending(ds: cat.Dataset, store: RawStore, open_days: list[str], today: str) -> list[tuple[str, dict[str, Any]]]:
    parts = cat.partitions(ds, open_days, today)
    entries = store.entries(ds.name)
    tail = {key for key, _ in parts[-ds.refresh_tail:]} if ds.refresh_tail else set()
    todo = []
    for key, params in parts:
        entry = entries.get(key)
        if entry is None or entry["status"] == "failed":
            todo.append((key, params))
        elif key in tail and entry["fetched_at"][:10].replace("-", "") < today:
            todo.append((key, params))
    return todo


def _date_mismatch(ds: cat.Dataset, key: str, df: Any) -> str | None:
    """检查返回数据的日期列是否与请求的分区一致，防止接口忽略参数。"""
    if ds.check_column is None or df is None or len(df) == 0 or ds.check_column not in df.columns:
        return None
    values = set(df[ds.check_column].dropna().astype(str).str.replace("-", "").str[:8].unique())
    if values and values != {key}:
        sample = sorted(values)[:3]
        return f"返回的 {ds.check_column} 与请求的 {key} 不一致（例如 {sample}），疑似参数被忽略"
    return None


def run(pro: Any, store: RawStore, *, today: str | None = None, tiers: tuple[int, ...] = (1,),
        only: list[str] | None = None, max_calls: int | None = None, trial: int | None = None,
        sleep: Callable = time.sleep,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    today = today or cat.today_str()
    datasets = [d for d in cat.build_catalog() if d.tier in tiers and (only is None or d.name in only)]
    report: dict[str, Any] = {"today": today, "trial": trial, "datasets": {}, "calls": 0, "stopped_by_budget": False}
    started = time.monotonic()

    # 交易日历必须先有
    ordered = sorted(datasets, key=lambda d: (d.mode != "snapshot", ))
    cal_ds = next(d for d in cat.build_catalog() if d.name == "trade_cal")
    if cal_ds not in ordered and not store.entries("trade_cal"):
        ordered.insert(0, cal_ds)
    open_days: list[str] = load_open_days(store)

    for ds in ordered:
        if ds.mode != "snapshot" and not open_days:
            open_days = load_open_days(store)
            if not open_days:
                report["datasets"][ds.name] = {"skipped": "没有交易日历"}
                continue
        todo = pending(ds, store, open_days, today)
        if trial and len(todo) > 2 * trial:
            todo = todo[:trial] + todo[-trial:]      # 试跑：每个数据集只取最早和最近的几个分区
        stat = {"planned": len(todo), "fetched": 0, "empty": 0, "failed": 0, "rows": 0, "calls": 0,
                "truncation_suspected": 0, "skipped_after_failures": False, "last_error": None}
        consecutive = 0
        for index, (key, params) in enumerate(todo, 1):
            if max_calls is not None and report["calls"] >= max_calls:
                report["stopped_by_budget"] = True
                break
            res = fetch_all(pro, ds.api, params, sleep=sleep)
            stat["calls"] += res["calls"]
            report["calls"] += res["calls"]
            error = res["error"] if not res["ok"] else _date_mismatch(ds, key, res["df"])
            if error:
                store.record_failure(ds.name, key, error)
                stat["failed"] += 1
                stat["last_error"] = error
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    stat["skipped_after_failures"] = True
                    log(f"[{ds.name}] 连续失败 {consecutive} 次，跳过该数据集：{error}")
                    break
                continue
            consecutive = 0
            entry = store.write(ds.name, key, res["df"], note=res["note"],
                                truncation_suspected=res["truncation_suspected"])
            stat["rows"] += entry["rows"]
            stat["fetched" if entry["status"] == "ok" else "empty"] += 1
            stat["truncation_suspected"] += int(res["truncation_suspected"])
            if index % 200 == 0 or index == len(todo):
                elapsed = int(time.monotonic() - started)
                log(f"[{ds.name}] {index}/{len(todo)} 最新分区 {key} 累计 {stat['rows']} 行，已用 {elapsed} 秒")
        report["datasets"][ds.name] = stat
        if ds.name == "trade_cal":
            open_days = load_open_days(store)
        if report["stopped_by_budget"]:
            break
    report["elapsed_seconds"] = int(time.monotonic() - started)
    report["store_summary"] = {d.name: store.summary(d.name) for d in datasets}
    return report
