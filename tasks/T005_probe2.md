# T005 补充探测

**目的**：回答 T002 留下的三个问题：各信息族的历史起点在哪一年；历史行业成分（已被剔除的归属）能否取到；分钟线有没有免费的替代来源。

**前置**：`git pull`，`python tools/verify_integrity.py` 通过。

## 步骤

1. 重新安装依赖（`requirements.txt` 已补上 `six`）：
   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. 本任务**允许**额外安装一个包，用于探测免费分钟线来源：
   ```powershell
   .venv\Scripts\python.exe -m pip install baostock
   ```
   安装失败不影响任务，脚本会如实记录"未安装"。
3. 运行：
   ```powershell
   .venv\Scripts\python.exe -m pytest -q
   .venv\Scripts\python.exe scripts/tushare_probe2.py
   ```
   约 160 次 Tushare 调用加 16 次 baostock 查询。限流文案的识别已修复，被限流时会自动等待 61 秒。

## 提交

- `results/T005/probe2.json`、`results/T005/run_meta.json`、`results/T005/REPORT.md`
- 提交信息：`T005: 补充探测结果`

## 报告中请额外回答

1. `index_member_all` 加 `is_new='N'` 后，返回的行里 `out_date` 有没有非空值？如果参数名不对，请查 Tushare 官方文档里"申万行业成分"接口的正确写法并写进报告。
2. Tushare 官方文档里，`stk_mins`（分钟线）和 `stk_auction_o`（集合竞价）单独开通的价格各是多少？只需抄录文档或权限页上的公开信息，不要下单。
3. baostock 的 5 分钟线在 2015、2019、2023 年各返回多少行？

## 说明

- 调用失败和空表都是有效结果，照实记录。
- 不要因为某个接口失败去改脚本。
