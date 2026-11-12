# T006 数据层试跑

**目的**：在全量回填之前，用很少的调用验证两件事：整理层在这台机器的真实样本上能跑通；采集清单里每个数据集的接口参数都是对的。全量回填要跑好几个小时，参数错了会白跑。

**前置**：`git pull`；`python tools/verify_integrity.py` 通过；`.env` 里的 `PERQ_DATA_DIR` 指向 **E 盘**的一个目录（D 盘只剩 6 GB，不要用）。

## 步骤

1. 测试。`samples/` 里有样本包，所以这次会多跑一个真实样本的端到端测试，约 1 到 2 分钟：
   ```powershell
   .venv\Scripts\python.exe -m pytest -q
   ```
2. 在样本上试跑整理层（把样本装进一个独立目录，不影响正式数据）：
   ```powershell
   .venv\Scripts\python.exe scripts/load_sample.py --out E:\perq_data\_sample
   .venv\Scripts\python.exe scripts/build_curated.py --data-dir E:\perq_data\_sample --sample --task T006
   .venv\Scripts\python.exe scripts/build_labels.py --data-dir E:\perq_data\_sample --task T006 --suffix _sample
   ```
   路径里的 `E:\perq_data` 换成你实际的 `PERQ_DATA_DIR`。
3. 采集试跑：每个数据集只拉最早和最近的 2 个分区，约 250 次调用：
   ```powershell
   .venv\Scripts\python.exe scripts/backfill.py --trial 2 --task T006
   ```

## 提交

- `results/T006/` 下的 `dq_report_sample.json`、`curated_summary_sample.json`、`labels_summary_sample.json`、`backfill_summary_trial.json`、`run_meta.json`、`REPORT.md`
- 提交信息：`T006: 数据层试跑`
- 不提交任何数据文件。

## 报告中请列出

1. 第 2 步的八项门禁是否全部通过。
2. 第 3 步中 `failed` 大于 0 或 `skipped_after_failures` 为真的数据集，逐个写出数据集名和 `last_error` 原文。
3. `truncation_suspected` 大于 0 的数据集。
4. 对每个失败的数据集，如果错误是"参数错误"或"返回日期与请求不一致"，请在 Tushare 官方文档里查到该接口正确的参数写法，抄进报告。不要改代码。

## 完成后

**停下，不要开始全量回填。** 等设计方看过试跑结果、修正采集清单后再执行 T007。
