"""脚本公共工具（由设计方提供，受保护）。

要点：
- token 只从环境变量或 .env 读取，绝不写入任何输出。
- 所有 Tushare 调用统一走 call()，自带限速与限流重试。
- 每个任务脚本结束时写 run_meta.json。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_INTERVAL_SECONDS = 0.35          # 两次调用的最小间隔
RATE_LIMIT_WAIT_SECONDS = 61         # 被限流后的等待
RATE_LIMIT_MARKERS = ("每分钟最多访问", "每小时最多访问", "每天最多访问", "访问频率")
MAX_ERROR_CHARS = 300

_last_call = 0.0


def load_env() -> None:
    """读取仓库根目录的 .env（如果存在）。不覆盖已有环境变量。"""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def scrub(text: str) -> str:
    """从文本中抹掉密钥取值，防止报错信息把 token 带进结果文件。"""
    for key in ("TUSHARE_TOKEN", "LEGACY_DB_PASSWORD"):
        secret = os.environ.get(key)
        if secret and len(secret) >= 6:
            text = text.replace(secret, "****")
    return text


def get_pro() -> Any:
    load_env()
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("缺少 TUSHARE_TOKEN：请在仓库根目录的 .env 中填写（不要提交 .env）。")
    import tushare as ts

    return ts.pro_api(token)


def call(pro: Any, api: str, *, sleep=time.sleep, max_rate_retries: int = 2, **params: Any) -> dict[str, Any]:
    """调用一个 Tushare 接口，返回 {ok, df, error, elapsed, rate_limited_times}。永不抛异常。"""
    global _last_call
    rate_hits = 0
    while True:
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            sleep(wait)
        started = time.monotonic()
        try:
            df = pro.query(api, **params)
            _last_call = time.monotonic()
            return {"ok": True, "df": df, "error": None,
                    "elapsed": round(_last_call - started, 3), "rate_limited_times": rate_hits}
        except Exception as exc:  # noqa: BLE001 — 探测脚本需要记录一切失败
            _last_call = time.monotonic()
            message = scrub(str(exc))[:MAX_ERROR_CHARS]
            if any(marker in message for marker in RATE_LIMIT_MARKERS) and rate_hits < max_rate_retries:
                rate_hits += 1
                sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            return {"ok": False, "df": None, "error": f"{type(exc).__name__}: {message}",
                    "elapsed": round(_last_call - started, 3), "rate_limited_times": rate_hits}


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_run_meta(result_dir: Path, started_at: datetime, extra: dict[str, Any] | None = None) -> None:
    try:
        import psutil

        mem = psutil.virtual_memory()
        memory = {"total_gb": round(mem.total / 2**30, 2), "available_gb": round(mem.available / 2**30, 2)}
    except Exception:  # noqa: BLE001
        memory = None
    meta = {
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "memory": memory,
    }
    if extra:
        meta.update(extra)
    write_json(result_dir / "run_meta.json", meta)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def date_range_of(df: Any) -> dict[str, dict[str, str]]:
    """返回各日期列的最小、最大值（只是元数据，不含逐行数据）。"""
    out: dict[str, dict[str, str]] = {}
    if df is None or len(df) == 0:
        return out
    for column in df.columns:
        if column.endswith("_date") or column in ("period", "enddate", "trade_time", "cal_date"):
            values = df[column].dropna().astype(str)
            if len(values):
                out[column] = {"min": values.min(), "max": values.max()}
    return out
