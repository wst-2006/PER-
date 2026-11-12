# T007 全量回填与整理层

**前置**：设计方已确认 T006 的结果，并明确通知可以开始。`git pull` 后 `verify_integrity` 通过。

**目的**：把 2015 年以来的全部数据拉进原始层，重建整理层，跑数据质量检查。

## 步骤

1. 全量回填。预计 6 万到 8 万次调用，需要 8 到 15 小时，建议晚上跑：
   ```powershell
   .venv\Scripts\python.exe scripts/backfill.py --task T007
   ```
   - 可以随时中断（Ctrl+C 或关机）。重新执行同一条命令会从断点继续，已拉取的分区不会重复拉。
   - 每 200 个分区打印一行进度。
   - 某个数据集连续失败 15 次会被跳过，其他数据集继续。
   - 跑完后**再执行一次**同一条命令，把第一遍里零星失败的分区补上。两次的 `backfill_summary.json` 以最后一次为准。
2. 运行前关闭不必要的程序，然后重建整理层并做质量检查：
   ```powershell
   .venv\Scripts\python.exe scripts/build_curated.py --task T007
   ```
   如果报内存不足，改用 `--memory 1500MB --threads 2` 重试。

## 提交

- `results/T007/` 下的 `backfill_summary.json`、`curated_summary.json`、`dq_report.json`、`run_meta.json`、`REPORT.md`
- 提交信息：`T007: 全量回填与数据质量报告`
- 不提交任何数据文件。

## 报告中请列出

1. 回填总调用次数、总耗时、中断过几次。
2. 原始层占用的磁盘空间（`PERQ_DATA_DIR\raw` 目录大小）和整理层大小。
3. 仍然失败或被跳过的数据集及错误原文。
4. 八项质量门禁的结果。**门禁不通过不算任务失败**，照实报告，由设计方判断是数据问题还是阈值问题。

## 严格禁止

- 不要手工删除或修改 `PERQ_DATA_DIR` 下的任何文件。
- 不要为了让门禁通过而修改阈值或代码。
