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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perq.data.client import (  # noqa: E402,F401
    MAX_ERROR_CHARS, MIN_INTERVAL_SECONDS, RATE_LIMIT_MARKERS, RATE_LIMIT_WAIT_SECONDS, call, scrub,
)


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


def get_pro() -> Any:
    load_env()
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("缺少 TUSHARE_TOKEN：请在仓库根目录的 .env 中填写（不要提交 .env）。")
    import tushare as ts

    return ts.pro_api(token)


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_run_meta(result_dir: Path, started_at: datetime, extra: dict[str, Any] | None = None,
                   name: str = "run_meta.json") -> None:
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
    write_json(result_dir / name, meta)


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


def data_root(override: str | None = None) -> Path:
    """数据根目录：命令行参数优先，其次 .env 里的 PERQ_DATA_DIR。"""
    load_env()
    raw = (override or os.environ.get("PERQ_DATA_DIR", "")).strip()
    if not raw:
        raise SystemExit("没有指定数据目录：请在 .env 中填写 PERQ_DATA_DIR，或使用 --data-dir。")
    root = Path(raw)
    root.mkdir(parents=True, exist_ok=True)
    return root
