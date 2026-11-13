"""Tushare 调用封装：限速、限流重试、分页、密钥脱敏。永不抛异常。"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

MIN_INTERVAL_SECONDS = 0.35
RATE_LIMIT_WAIT_SECONDS = 61
RATE_LIMIT_MARKERS = ("每分钟最多访问", "每小时最多访问", "每天最多访问", "访问频率", "频率超限")
# 网络层错误（代理断开、超时）：等待后重试，不当作接口失败
NETWORK_MARKERS = ("ProxyError", "ConnectionError", "ReadTimeout", "ConnectTimeout", "Max retries exceeded",
                   "Read timed out", "RemoteDisconnected", "Connection aborted", "ChunkedEncodingError")
NETWORK_BACKOFF_SECONDS = (10, 30, 60, 120, 300, 600)
MAX_ERROR_CHARS = 300
# 接口的单次返回上限都是整数（3000、5000、6000…）。返回行数恰为 PAGE_STEP 的整数倍时，
# 视为可能被截断，继续向后翻页直到取空。
PAGE_STEP = 500
MAX_PAGES = 200

_next_slot = 0.0
_slot_lock = threading.Lock()


def _reserve_slot() -> float:
    """多线程共用的限速器：返回本次调用还需要等待的秒数。"""
    global _next_slot
    with _slot_lock:
        now = time.monotonic()
        # 排队最多排 8 个间隔：防止（测试里常见的）不真正等待的调用把时间槽无限推后
        slot = max(now, min(_next_slot, now + 8 * MIN_INTERVAL_SECONDS))
        _next_slot = slot + MIN_INTERVAL_SECONDS
        return slot - now


def scrub(text: str) -> str:
    for key in ("TUSHARE_TOKEN", "LEGACY_DB_PASSWORD"):
        secret = os.environ.get(key)
        if secret and len(secret) >= 6:
            text = text.replace(secret, "****")
    return text


def call(pro: Any, api: str, *, sleep=time.sleep, max_rate_retries: int = 2,
         network_backoff: tuple[int, ...] = NETWORK_BACKOFF_SECONDS, **params: Any) -> dict[str, Any]:
    """调用一次接口，返回 {ok, df, error, elapsed, rate_limited_times, network_retries}。"""
    rate_hits = 0
    net_hits = 0
    while True:
        wait = _reserve_slot()
        if wait > 0:
            sleep(wait)
        started = time.monotonic()
        try:
            df = pro.query(api, **params)
            return {"ok": True, "df": df, "error": None, "elapsed": round(time.monotonic() - started, 3),
                    "rate_limited_times": rate_hits, "network_retries": net_hits}
        except Exception as exc:  # noqa: BLE001
            message = scrub(str(exc))[:MAX_ERROR_CHARS]
            if any(marker in message for marker in RATE_LIMIT_MARKERS) and rate_hits < max_rate_retries:
                rate_hits += 1
                sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            text = f"{type(exc).__name__}: {message}"
            if any(marker in text for marker in NETWORK_MARKERS) and net_hits < len(network_backoff):
                sleep(network_backoff[net_hits])
                net_hits += 1
                continue
            return {"ok": False, "df": None, "error": text, "elapsed": round(time.monotonic() - started, 3),
                    "rate_limited_times": rate_hits, "network_retries": net_hits}


def fetch_all(pro: Any, api: str, params: dict[str, Any], *, sleep=time.sleep) -> dict[str, Any]:
    """取一个分区的全部行，必要时翻页。

    返回 {ok, df, error, calls, pages, truncation_suspected, note}。
    truncation_suspected 为真表示行数疑似被上限截断且无法通过翻页确认完整。
    """
    import pandas as pd

    kw = {"sleep": sleep}
    first = call(pro, api, **kw, **params)
    calls = 1
    if not first["ok"]:
        return {"ok": False, "df": None, "error": first["error"], "calls": calls, "pages": 0,
                "truncation_suspected": False, "note": None}
    frames = [first["df"]] if first["df"] is not None else []
    total = len(frames[0]) if frames else 0
    page_size = total
    suspected, note = False, None
    while page_size >= PAGE_STEP and page_size % PAGE_STEP == 0 and len(frames) < MAX_PAGES:
        nxt = call(pro, api, **kw, **params, limit=page_size, offset=total)
        calls += 1
        if not nxt["ok"]:
            suspected, note = True, f"翻页失败：{nxt['error']}"
            break
        page = nxt["df"]
        if page is None or len(page) == 0:
            break
        if len(page) == len(frames[0]) and page.reset_index(drop=True).equals(frames[0].reset_index(drop=True)):
            suspected, note = True, "接口忽略了 offset 参数，无法确认是否完整"
            break
        frames.append(page)
        total += len(page)
        if len(page) < page_size:
            break
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else (frames[0] if frames else None)
    return {"ok": True, "df": df, "error": None, "calls": calls, "pages": len(frames),
            "truncation_suspected": suspected, "note": note}
