"""T003：旧系统数据盘点（由设计方提供，受保护）。只读。

1. 旧 PostgreSQL：每张表的行数、字段、交易日范围；日线表按年份的覆盖情况。
2. 旧 reports 目录：两层目录的体积与文件数。

对数据库只执行 SELECT，并把会话设为只读。不导出任何逐行数据。

用法：
    python scripts/legacy_inventory.py
输出：
    results/T003/legacy_inventory.json
    results/T003/run_meta.json
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

RESULT_DIR = c.REPO_ROOT / "results" / "T003"
DATE_COLUMNS = ("trade_date", "signal_date", "ann_date", "end_date", "cal_date")
PER_YEAR_TABLES = ("daily_quotes", "daily_basic", "moneyflow_daily", "stock_adj_factors", "stock_limit_daily")
STATEMENT_TIMEOUT_MS = 180_000


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def inventory_database() -> dict[str, Any]:
    required = ("LEGACY_DB_HOST", "LEGACY_DB_PORT", "LEGACY_DB_NAME", "LEGACY_DB_USER", "LEGACY_DB_PASSWORD")
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        return {"status": "skipped", "reason": f".env 缺少：{', '.join(missing)}"}
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ["LEGACY_DB_HOST"], port=int(os.environ["LEGACY_DB_PORT"]),
            dbname=os.environ["LEGACY_DB_NAME"], user=os.environ["LEGACY_DB_USER"],
            password=os.environ["LEGACY_DB_PASSWORD"], connect_timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "stage": "connect", "error": c.scrub(f"{type(exc).__name__}: {exc}")[:300]}

    conn.set_session(readonly=True, autocommit=True)
    tables: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute("SELECT version()")
            server_version = cur.fetchone()[0]
            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0])
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            names = [row[0] for row in cur.fetchall()]
            for name in names:
                entry: dict[str, Any] = {"table": name}
                try:
                    cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (name,)
                    )
                    columns = cur.fetchall()
                    entry["columns"] = [{"name": col, "type": dtype} for col, dtype in columns]
                    cur.execute("SELECT pg_total_relation_size(%s::regclass)", (f"public.{quote_ident(name)}",))
                    entry["bytes"] = int(cur.fetchone()[0])
                    cur.execute(f"SELECT count(*) FROM public.{quote_ident(name)}")
                    entry["rows"] = int(cur.fetchone()[0])
                    column_names = {col for col, _ in columns}
                    ranges = {}
                    for date_col in DATE_COLUMNS:
                        if date_col in column_names:
                            cur.execute(
                                f"SELECT min({quote_ident(date_col)})::text, max({quote_ident(date_col)})::text, "
                                f"count(DISTINCT {quote_ident(date_col)}) FROM public.{quote_ident(name)}"
                            )
                            lo, hi, distinct = cur.fetchone()
                            ranges[date_col] = {"min": lo, "max": hi, "distinct": int(distinct)}
                    entry["date_ranges"] = ranges
                    if name in PER_YEAR_TABLES and {"trade_date", "ts_code"} <= column_names:
                        cur.execute(
                            f"SELECT left(trade_date::text, 4) AS y, count(*), count(DISTINCT trade_date), "
                            f"count(DISTINCT ts_code) FROM public.{quote_ident(name)} GROUP BY 1 ORDER BY 1"
                        )
                        entry["per_year"] = [
                            {"year": y, "rows": int(n), "trade_dates": int(d), "stocks": int(s)}
                            for y, n, d, s in cur.fetchall()
                        ]
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = c.scrub(f"{type(exc).__name__}: {exc}")[:300]
                tables.append(entry)
                print(f"{name:<40} rows={entry.get('rows', '?')}", flush=True)
    finally:
        conn.close()
    return {"status": "completed", "server_version": server_version, "database_bytes": database_bytes,
            "table_count": len(tables), "tables": tables}


def dir_stats(path: Path) -> tuple[int, int]:
    total, files = 0, 0
    for root, _dirs, names in os.walk(path):
        for fname in names:
            try:
                total += (Path(root) / fname).stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def inventory_reports() -> dict[str, Any]:
    raw = os.environ.get("LEGACY_REPORTS_DIR", "").strip()
    if not raw:
        return {"status": "skipped", "reason": ".env 缺少 LEGACY_REPORTS_DIR"}
    root = Path(raw)
    if not root.is_dir():
        return {"status": "failed", "error": "LEGACY_REPORTS_DIR 不是一个存在的目录"}
    entries = []
    for level1 in sorted(p for p in root.iterdir() if p.is_dir()):
        size1, files1 = dir_stats(level1)
        children = []
        for level2 in sorted(p for p in level1.iterdir() if p.is_dir()):
            size2, files2 = dir_stats(level2)
            children.append({"name": level2.name, "gb": round(size2 / 2**30, 3), "files": files2})
        children.sort(key=lambda item: item["gb"], reverse=True)
        entries.append({"name": level1.name, "gb": round(size1 / 2**30, 3), "files": files1, "children": children[:40]})
    total = sum(item["gb"] for item in entries)
    return {"status": "completed", "total_gb": round(total, 2), "directories": entries}


def main() -> int:
    started = c.utc_now()
    c.load_env()
    report = {"database": inventory_database(), "reports_dir": inventory_reports()}
    c.write_json(RESULT_DIR / "legacy_inventory.json", report)
    c.write_run_meta(RESULT_DIR, started, {"task": "T003"})
    print(f"已写入 {RESULT_DIR / 'legacy_inventory.json'}；"
          f"数据库：{report['database']['status']}；目录：{report['reports_dir']['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
