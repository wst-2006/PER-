# T003 旧系统数据盘点（只读）

**目的**：弄清旧 PostgreSQL 和旧 `reports/` 目录里实际有什么，决定哪些可以作为新数据的交叉校验源、哪些可以清理。

**前置**：T001 通过；`.env` 中已填 `LEGACY_DB_*` 和 `LEGACY_REPORTS_DIR`；旧 PostgreSQL 服务已启动。

## 步骤

```powershell
python scripts/legacy_inventory.py
```

脚本对数据库只执行 SELECT，会话设为只读。大表计数可能需要几分钟。

## 提交

- `results/T003/legacy_inventory.json`、`results/T003/run_meta.json`、`results/T003/REPORT.md`
- 提交信息：`T003: 旧数据盘点`

## 严格禁止

- 不得对旧数据库执行任何写入、删除、迁移或 VACUUM。
- 不得删除、移动或改名旧项目的任何文件。
- 如果数据库连不上，记录报错原文即可，不要尝试重装或修复数据库。

## 报告中请额外回答

1. `reports/db_backups/` 里有哪些文件（文件名、大小、日期）？
2. 旧数据库最近一次成功同步是哪一天（看 `data_sync_runs` 表或日线表最大日期）？
