# T012 第二批因子：新信息能不能带来增量

**前置**：`git pull`；`python tools/verify_integrity.py` 通过；`python -m pytest -q` 全部通过（如遇本机改名拦截，照实记录即可）。

在你自己的命令窗口启动。本任务不调用 Tushare。

**目的**：到目前为止的模型只用了日线量价和估值。这次加入 24 个新因子，覆盖资金流向、融资、筹码、股东户数、分析师预期、内部人增减持、限售解禁、回购、业绩预告，以及按公告时点还原的财务质量与成长（ROE、毛利率、单季利润与收入同比、标准化未预期盈余、应计项目、资产增长、负债率），然后检验：

1. 这些新因子单独有没有效。
2. 把它们加进模型之后，相对只用旧因子的同一模型，有没有增量；增量是不是又只集中在小市值股票里。

## 步骤

```powershell
.venv\Scripts\python.exe scripts/run_v2.py --task T012 --threads 8
```

依次执行：计算新因子 → 50 个因子的单因子评估 → 生成合并面板 → 训练 4 个模型（只用旧因子 / 新旧合并 × 排序 / 回归目标）→ 8 次回测与归因。预计 1.5 到 3 小时。

中断后可以跳过已完成的阶段重跑，例如 `--skip factors,evaluate`。阶段名：`factors`、`evaluate`、`panel`、`models`。

## 提交

`results/T012/` 下全部文件：`factors_v2_summary.json`、`factor_report_v12.json`、`factor_table_v12.csv`、`panel_*.json`、`model_*.json`（4 个）、`backtest_*.json`（8 个）、`v2_comparison.csv`、`run_meta_v2.json`、`REPORT.md`。

提交信息：`T012: 第二批因子与增量检验`。不提交任何数据文件。

## 报告中请列出

1. `factors_v2_summary.json` 里的 `missing_sources` 和 `coverage`（全样本覆盖率）。
2. `factor_table_v12.csv` 中 24 个新因子的行（因子名不在 T008 的因子表里的那些）。
3. 4 个模型的 `score_ic` 与耗时。
4. `v2_comparison.csv` 全文。
5. 每个阶段的耗时。

**只报告数字，不评价好坏，不给出任何买卖建议。**

---

## 设计方对 T011 的确认（2026-11-23）

T011 通过。结果解读见 `docs/FINDINGS.md` 的 F4。你指出的两个口径问题已在脚本里补上：风格收益的定义（每种风格用哪个因子、哪一端是多头）写进了 `style_returns_summary.json`；收缩夏普的 `sr` 注明为日频。
