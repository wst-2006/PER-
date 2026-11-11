"""T001：环境报告（由设计方提供，受保护）。

记录 Python、内存、磁盘、GPU、依赖版本，并做一次 DuckDB + Parquet 读写自检。
不记录主机名和用户名。

用法：
    python scripts/env_report.py
输出：
    results/T001/env.json
    results/T001/run_meta.json
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

RESULT_DIR = c.REPO_ROOT / "results" / "T001"
PACKAGES = ["tushare", "pandas", "numpy", "pyarrow", "duckdb", "psutil", "pytest", "psycopg2"]


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            module = importlib.import_module(name)
            out[name] = str(getattr(module, "__version__", "unknown"))
        except Exception as exc:  # noqa: BLE001
            out[name] = None
            out[f"{name}__error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def disk_usage(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    drive = probe.anchor or str(probe)
    return {"drive": drive, "total_gb": round(usage.total / 2**30, 1), "free_gb": round(usage.free / 2**30, 1)}


def gpu_info() -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return {"available": False, "error": out.stderr.strip()[:200]}
        return {"available": True, "gpus": [line.strip() for line in out.stdout.splitlines() if line.strip()]}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def duckdb_parquet_selfcheck() -> dict[str, Any]:
    """写一个小 Parquet，再用 DuckDB 读回并聚合，确认存储栈可用。"""
    try:
        import duckdb
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.parquet"
            frame = pd.DataFrame({"trade_date": ["20240102"] * 3 + ["20240103"] * 2, "x": [1.0, 2.0, 3.0, 4.0, 5.0]})
            frame.to_parquet(path, index=False)
            con = duckdb.connect()
            con.execute("SET memory_limit='1GB'")
            rows = con.execute(
                "SELECT trade_date, sum(x) FROM read_parquet(?) GROUP BY 1 ORDER BY 1", [str(path)]
            ).fetchall()
            con.close()
        ok = rows == [("20240102", 6.0), ("20240103", 9.0)]
        return {"ok": ok, "rows": [list(r) for r in rows]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def collect() -> dict[str, Any]:
    c.load_env()
    try:
        import psutil

        mem = psutil.virtual_memory()
        memory = {"total_gb": round(mem.total / 2**30, 2), "available_gb": round(mem.available / 2**30, 2),
                  "percent_used": mem.percent}
        cpu = {"physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(logical=True)}
    except Exception as exc:  # noqa: BLE001
        memory, cpu = {"error": str(exc)[:120]}, {"logical_cores": os.cpu_count()}
    data_dir = os.environ.get("PERQ_DATA_DIR", "").strip()
    return {
        "python": sys.version,
        "python_ok": sys.version_info[:2] >= (3, 11),
        "platform": platform.platform(),
        "cpu": cpu,
        "memory": memory,
        "gpu": gpu_info(),
        "disk_repo": disk_usage(str(c.REPO_ROOT)),
        "data_dir_configured": bool(data_dir),
        "disk_data_dir": disk_usage(data_dir),
        "packages": package_versions(),
        "duckdb_parquet_selfcheck": duckdb_parquet_selfcheck(),
        "env_keys_present": {key: bool(os.environ.get(key, "").strip())
                             for key in ("TUSHARE_TOKEN", "PERQ_DATA_DIR", "LEGACY_DB_NAME", "LEGACY_REPORTS_DIR")},
    }


def main() -> int:
    started = c.utc_now()
    report = collect()
    c.write_json(RESULT_DIR / "env.json", report)
    c.write_run_meta(RESULT_DIR, started, {"task": "T001"})
    print(f"已写入 {RESULT_DIR / 'env.json'}")
    print(f"Python 版本合格：{report['python_ok']}；存储自检：{report['duckdb_parquet_selfcheck'].get('ok')}；"
          f"GPU：{report['gpu'].get('available')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
