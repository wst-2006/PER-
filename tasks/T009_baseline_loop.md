# T009 第一个完整闭环：因子合成 → 组合回测

**前置**：`git pull`；`python tools/verify_integrity.py` 通过；`python -m pytest -q` 全部通过。T008 的因子表已在数据目录里。

本任务不调用 Tushare，可以和正在后台跑的 B 类回填同时进行（两者读写的文件互不重叠）。所有命令用 PowerShell 启动。

**目的**：

1. 用修正后的评估代码重跑单因子评估（修复了"多数取值为 0 的因子被截成常数"的问题）。
2. 把 26 个因子按三种方案合成为一个打分，回测"每周持有打分最高的 30 只股票"在开发区的表现，与股票池等权基准比较。三种方案中有两种是按已实现表现自动调整因子权重的。

## 步骤

```powershell
.venv\Scripts\python.exe scripts/build_factors.py --skip-build --task T009
.venv\Scripts\python.exe scripts/run_baseline.py --task T009
```

- 第一条约 3 分钟；第二条先逐日计算打分（约 5 分钟），再对三种方案各跑一次 2016—2024 年的回测（每种约 5 到 10 分钟）。
- **不要修改 `--end`、`--start` 或任何参数。**

## 提交

`results/T009/` 下：`factor_report.json`、`factor_table.csv`、`composite_summary.json`、`baseline_prior.json`、`baseline_ic.json`、`baseline_icir.json`、`baseline_table.csv`、`run_meta_*.json`、`REPORT.md`。

提交信息：`T009: 基线闭环回测`。不提交任何数据文件。

## 报告中请列出

1. `factor_table.csv` 中 `limitup_20` 和 `break_20` 两行（修复前只有 2015 年的结果）。
2. `composite_summary.json` 里三种方案的 `schemes` 一节原文。
3. `baseline_table.csv` 全文，以及三个 `baseline_*.json` 各自的 `by_year` 和 `unfilled_by_reason`。
4. 两条命令的耗时与内存峰值。

**只报告数字，不评价好坏，不给出任何买卖建议。**
