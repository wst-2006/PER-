# T011 稳健性检验：风格归因、模型稳定性、收缩夏普

**前置**：`git pull`；`python tools/verify_integrity.py` 通过；`.venv\Scripts\python.exe -m pip install -r requirements.txt`（新增 scipy，一般已随 lightgbm 装好）。

在你自己的命令窗口启动。本任务不调用 Tushare。

**目的**：T010 里最好的配置（排序目标、全部历史）年化超额 15%，但它最依赖市值和流动性两个特征，而且结果是从 45 次回测里挑出来的。这次检验它是不是真的：

1. 五种打分方案的默认组合（30 只、每周）各自的超额里，有多少来自小市值、低估值、低流动性、反转、低波动、低换手这六种风格；剔除风格后还剩多少。
2. 同样的五种方案，限定只买市值排在股票池前 70% 的股票，表现还剩多少。
3. 收缩夏普比率：扣除"从 45 次里挑最好的"带来的运气。
4. 排序模型换三个随机种子重训；以及去掉市值和流动性两个特征之后的版本。

## 步骤

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts/run_robustness.py --task T011 --threads 8
```

预计 2 到 3 小时（其中 4 个模型变体约 2 小时）。中断后重跑时，已训练完的模型变体可以用 `--skip-models` 跳过。

关于 pytest：如果 `tests/test_baseline.py` 再次因为本机拦截改名（`WinError 5`）失败，照实记录失败的测试名和报错原文即可，不要尝试绕过。

## 提交

`results/T011/` 下全部文件：`style_returns_summary.json`、`attribution_*.json`、`model_rank_all_fast_*.json`、`deflated_sharpe.json`、`robustness_table.csv`、`run_meta_robustness.json`、`REPORT.md`。

提交信息：`T011: 稳健性检验`。不提交任何数据文件。

## 报告中请列出

1. `robustness_table.csv` 全文。
2. `deflated_sharpe.json` 全文。
3. `style_returns_summary.json` 的 `annual_mean`。
4. 四个模型变体的 `score_ic` 与耗时。

**只报告数字，不评价好坏，不给出任何买卖建议。**

---

## 设计方对 T010 的确认（2026-11-22）

T010 通过。结果解读见 `docs/FINDINGS.md` 的 F3。

- 内存峰值拿不到没有关系，以后的任务不再要求。
- 你注意到网格里没有 `ic` 方案：这是有意的，T009 中它在三种线性方案里最弱，没有再比较的必要。
- 模型 1 两次耗时相差一倍，最可能是与回填抢 CPU，不影响结果（训练是确定性的）。
