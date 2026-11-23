# T013 候选方案检验：多种子、仓位控制、小市值上限

**前置**：你在自己的窗口 `git pull --ff-only` 之后，工具窗口内 `python tools/verify_integrity.py` 通过。

**目的**：T012 中"回归目标 + 新旧因子合并"的模型是第一个在剔除小市值、剔除风格之后仍然显著的结果。这次检验它是否稳健，并在上面加入两种风险控制：

1. 换三个随机种子训练，看结论是否依赖种子。
2. 总仓位控制：市场波动放大时按比例减仓、指数跌破 120 日均线时减半，以及两者结合。
3. 小市值上限：市值最小 20% 的股票最多占持股数的 30%。
4. 调仓间隔 5 天与 10 天。
5. 按累计试验次数（约 90 次）计算收缩夏普。

## 步骤（由用户在自己的窗口运行）

```powershell
D:\PER\.venv\Scripts\python.exe D:\PER\scripts\run_candidate.py --task T013 --threads 8
```

依次执行：重算第二批因子（约 5 分钟）→ 重建合并面板（约 5 分钟）→ 训练 3 个模型（约 40 分钟）→ 20 次回测与归因（约 30 分钟）。预计合计 1 到 1.5 小时。

中断后可以跳过已完成的阶段，例如 `--skip factors,panel`。阶段名：`factors`、`panel`、`models`。

## 提交（由执行代理完成）

`results/T013/` 下全部文件：`factors_v2_summary.json`、`panel_v1_v2.json`、`model_cand_*.json`（3 个）、`backtest_*.json`（20 个）、`candidate_grid.csv`、`deflated_sharpe.json`、`run_meta_candidate.json`、`REPORT.md`。

提交信息：`T013: 候选方案检验`。不提交任何数据文件。

## 报告中请列出

1. 3 个模型的 `score_ic` 与耗时。
2. `candidate_grid.csv` 全文。
3. `deflated_sharpe.json` 全文。
4. `factors_v2_summary.json` 中 `repurchase_60`、`insider_net_90`、`unlock_30` 三项的覆盖率（应接近 1）。
5. `run_meta_candidate.json` 中各阶段耗时。

**只报告数字，不评价好坏，不给出任何买卖建议。**

关于 pytest：全量测试在工具窗口里会挂住的问题，这次不必再试。设计方在提交前已在自己的环境里跑过全量测试。

---

## 设计方对 T012 的确认（2026-11-23）

T012 通过。结果解读见 `docs/FINDINGS.md` 的 F5。你指出的 `repurchase_60` 没有 IC：原因是事件类因子在"没有发生"时记成了缺失，于是这个因子只剩一个取值，标准化后整列为空。已改为记 0，本任务会重算。
