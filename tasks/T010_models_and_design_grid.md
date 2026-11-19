# T010 梯度提升树模型与组合设计的系统比较

**前置**：`git pull`；`python tools/verify_integrity.py` 通过。

本任务新增一个依赖。先安装再跑测试：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

所有命令在**你自己的 cmd 或 PowerShell 窗口**里启动（T007 证明这样最稳定）。本任务不调用 Tushare。

**目的**：

1. 用滚动训练的梯度提升树替代线性合成，比较三种模型。
2. 对 5 种打分方案 × 9 种组合设计（持股 20、30、50 只 × 每 5、10、20 天调仓）全部回测，报告结果的分布而不是挑一个最好的。
3. 对默认设计做交易成本加倍的压力测试。

## 步骤

```powershell
.venv\Scripts\python.exe scripts/run_models.py --task T010 --threads 8
```

预计 3 到 5 小时，建议晚上跑。程序会依次打印：面板生成、三个模型各自的训练进度摘要、50 次回测各一行。

- 中途中断时，可以跳过已完成的阶段重跑：面板已生成就加 `--skip-panel`，三个模型都已训练完就再加 `--skip-models`。
- 内存不足时把 `--threads` 降到 4。
- **不要修改任何其他参数**，尤其是 `--end`。

## 提交

`results/T010/` 下：`panel_summary.json`、`model_*.json`（3 个）、`backtest_*.json`（5 个）、`design_grid.csv`、`run_meta_models.json`、`REPORT.md`。

提交信息：`T010: 模型与组合设计比较`。不提交任何数据文件。

## 报告中请列出

1. 三个 `model_*.json` 各自的 `score_ic`、`retrains`、`seconds`，以及 `feature_importance_gain_share` 的前 8 项。
2. `design_grid.csv` 全文。
3. 每个阶段的耗时与内存峰值。

**只报告数字，不评价好坏，不给出任何买卖建议。**

---

## 设计方对 T007b 与 T009 的确认（2026-11-19）

1. **T007b 通过。** `moneyflow_ths` 和 `limit_step` 是设计方有意放在第 2 层、暂不拉取的（历史太短，或可由日线自行计算）。你补拉了，数据无害，保留即可；但这属于任务单之外的操作，以后遇到"看起来漏了"的情况先报告、再动手。你指出的两个问题已修复：回填结束时会打印整个采集清单里各层仍待拉取的分区数；每次运行另存一份带时间戳的摘要（`backfill_runs/` 目录），不再被覆盖。
2. **T009 通过。** 报告 6.2 节的 215 条警告：早期某些因子整段历史都没有 IC（例如一年动量在头一年全为空），求均值时产生警告；代码随后把这些空值记为权重 0，所以权重从来不是空值。已改为显式处理，不再产生警告。结果解读见 `docs/FINDINGS.md` 的 F2。
