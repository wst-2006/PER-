"""原始层存储：每个接口一个目录，每个分区一个 Parquet 文件，外加清单与列类型登记。

规则：
- 重新拉取同一分区时，旧文件移入 _superseded/，不删除。
- 每次写入在清单里追加一行（分区、行数、校验值、时间、状态）。
- 列类型按接口登记，保证不同分区的同名列类型一致。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FETCHED_AT = "_fetched_at"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.manifest_dir = self.raw / "_manifest"
        self.schema_dir = self.raw / "_schema"

    # ---------- 路径 ----------
    def dataset_dir(self, name: str) -> Path:
        return self.raw / name

    def path(self, name: str, key: str) -> Path:
        return self.dataset_dir(name) / f"{key}.parquet"

    def glob(self, name: str) -> str:
        return (self.dataset_dir(name) / "*.parquet").as_posix()

    def has_data(self, name: str) -> bool:
        return self.dataset_dir(name).is_dir() and any(self.dataset_dir(name).glob("*.parquet"))

    # ---------- 清单 ----------
    def _manifest_path(self, name: str) -> Path:
        return self.manifest_dir / f"{name}.jsonl"

    def entries(self, name: str) -> dict[str, dict[str, Any]]:
        """每个分区的最新一条清单记录。"""
        path = self._manifest_path(name)
        latest: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return latest
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                latest[entry["key"]] = entry
        return latest

    def _append_manifest(self, name: str, entry: dict[str, Any]) -> None:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        with self._manifest_path(name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- 列类型 ----------
    def _schema_path(self, name: str) -> Path:
        return self.schema_dir / f"{name}.json"

    def schema(self, name: str) -> dict[str, str]:
        path = self._schema_path(name)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _coerce(self, name: str, df: Any) -> Any:
        """把列类型对齐到登记的类型；全空的列写成浮点空值且不登记。"""
        import pandas as pd

        schema = self.schema(name)
        out = df.copy()
        changed = False
        for column in out.columns:
            series = out[column]
            known = schema.get(column)
            all_null = bool(series.isna().all())
            if known is None:
                if all_null:
                    out[column] = pd.Series([float("nan")] * len(out), index=out.index, dtype="float64")
                    continue
                kind = "float64" if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series) else "string"
                schema[column] = kind
                known = kind
                changed = True
            if known == "float64":
                out[column] = pd.to_numeric(series, errors="coerce").astype("float64")
            else:
                out[column] = series.astype("string")
        if changed:
            self.schema_dir.mkdir(parents=True, exist_ok=True)
            self._schema_path(name).write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
        return out

    # ---------- 写入 ----------
    def write(self, name: str, key: str, df: Any, *, note: str | None = None,
              truncation_suspected: bool = False) -> dict[str, Any]:
        fetched_at = _now()
        rows = 0 if df is None else int(len(df))
        entry: dict[str, Any] = {"key": key, "rows": rows, "fetched_at": fetched_at, "status": "ok",
                                 "truncation_suspected": bool(truncation_suspected)}
        if note:
            entry["note"] = note
        target = self.path(name, key)
        if target.exists():
            archive = self.raw / "_superseded" / name
            archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(archive / f"{key}.{fetched_at.replace(':', '')}.parquet"))
        if rows > 0:
            out = self._coerce(name, df)
            out[FETCHED_AT] = fetched_at
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".parquet.tmp")
            out.to_parquet(tmp, index=False)
            tmp.replace(target)
            entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            entry["bytes"] = target.stat().st_size
        else:
            entry["status"] = "empty"
        self._append_manifest(name, entry)
        return entry

    def record_failure(self, name: str, key: str, error: str) -> dict[str, Any]:
        entry = {"key": key, "rows": 0, "fetched_at": _now(), "status": "failed", "error": error[:300]}
        self._append_manifest(name, entry)
        return entry

    # ---------- 汇总 ----------
    def summary(self, name: str) -> dict[str, Any]:
        entries = self.entries(name)
        by_status: dict[str, int] = {}
        for entry in entries.values():
            by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        keys = sorted(k for k, e in entries.items() if e["status"] == "ok")
        return {
            "partitions": len(entries), "by_status": by_status,
            "rows": int(sum(e.get("rows", 0) for e in entries.values())),
            "bytes": int(sum(e.get("bytes", 0) for e in entries.values())),
            "first_key": keys[0] if keys else None, "last_key": keys[-1] if keys else None,
            "truncation_suspected": sorted(k for k, e in entries.items() if e.get("truncation_suspected")),
        }
