# T003 旧系统数据盘点（只读）

- 执行者：执行代理
- 执行时间（UTC）：2026-11-12T04:32:50Z — 04:41:46Z，耗时 8 分 56 秒
- 仓库提交基线：`dd601f0`（T002 之后）

## 1. 状态

**完成。** 数据库与目录两部分都返回 `status: completed`。

```json
{"database": {"status": "completed"}, "reports_dir": {"status": "completed"}}
```

## 2. 执行的命令

```bash
git pull --ff-only                                     # Already up to date
python tools/verify_integrity.py                       # 通过
.venv/Scripts/python.exe scripts/legacy_inventory.py   # 本次任务主体

# 任务单要求回答的两个问题，脚本未直接给出，另做只读查询（见第 5 节）
# 1) data_sync_runs 的状态分布与最近成功同步日期 —— 单条 SELECT，readonly=True
# 2) reports/db_backups 的文件明细 —— ls，不读文件内容
```

数据库会话由脚本设为 `set_session(readonly=True, autocommit=True)`，我补的那条查询同样设了 `readonly=True`。全程未执行任何写入、DDL、VACUUM。

## 3. 结果摘要

### 3.1 数据库整体

| 项 | 值 |
|---|---|
| 服务端 | PostgreSQL **18.4** on x86_64-windows, 64-bit |
| 库大小 | **29.11 GB**（31,256,647,359 字节） |
| public 模式基表数 | **69** |
| 空表数 | **18 / 69** |
| 数据日期范围 | 日线类表 20240102 — **20260716**，613 个交易日 |

### 3.2 体积与行数（前 12）

| 表 | 行数 | 体积 |
|---|---|---|
| `factor_snapshots` | 6,125,855 | **22,073.7 MB** |
| `tushare_raw_records` | 3,666,628 | 2,820.4 MB |
| `factor_forward_returns` | 3,567,235 | 813.8 MB |
| `daily_quotes` | 3,310,316 | 779.9 MB |
| `daily_basic` | 3,311,408 | 773.8 MB |
| `stock_limit_daily` | 3,977,271 | 718.4 MB |
| `moneyflow_daily` | 3,144,752 | 673.1 MB |
| `stock_adj_factors` | 3,017,885 | 521.5 MB |
| `ml_dual_scores` | 1,332,344 | 316.4 MB |
| `index_daily` | 956,669 | 209.6 MB |
| `raw_income` | 98,824 | 18.3 MB |
| `raw_dividend` | 99,053 | 13.1 MB |

`factor_snapshots` 一张表就占全库 71%。

### 3.3 日线表的年度覆盖

| 表 | 起止 | 交易日数 | 2024 / 2025 / 2026 |
|---|---|---|---|
| `daily_quotes` | 20240102–20260716 | 613 | 1,293,893 / 1,313,898 / 702,525 |
| `daily_basic` | 20240102–20260716 | 613 | 1,293,893 / 1,313,898 / 703,617 |
| `moneyflow_daily` | 20240102–20260716 | 613 | 1,233,188 / 1,248,108 / 663,456 |
| `stock_adj_factors` | **20240401**–20260716 | 555 | 991,433 / 1,321,761 / 704,691 |
| `stock_limit_daily` | **20240401**–20260716 | 555 | 1,263,949 / 1,746,811 / 966,511 |

`index_daily` 起于 20240401，止于 20260716，598 个交易日。

### 3.4 其他表

- `stocks` 8,124 行（含退市），`raw_stock_basic` 5,525 行。
- `tushare_raw_records` 的 `ann_date` 覆盖 2011-05-19 — 2026-07-17，`end_date` 覆盖 2020-12-31 — 2027-12-31。
- `data_sync_runs` 1,626 行，状态分布：`success` 1,612、`partial_success` 14。
- `research_runs` 8 行，`trade_date` 只在 20260710–20260716 三天。
- `ml_experiment_runs` 457 行，`end_date` 20240101–20260713。

### 3.5 `reports/` 目录

总计 **31.03 GB**。

| 子目录（第一层） | 体积 | 文件数 |
|---|---|---|
| `ml_lab` | **31.032 GB** | 2,667 |
| `db_backups` | ~0 GB（202 KB） | 10 |
| `docx_qa` | ~0 GB | 3 |
| `factor_lab` / `factor_lab_check` / `factor_lab_versioned` | ~0 GB | 2 / 3 / 3 |
| `web_runtime` | ~0 GB | 2 |

`ml_lab` 下的前几名：`training_frame_v8_events` 1.639 GB / 524 文件、`training_frame_v9_events` 1.623 GB / 524、`training_frame_v8` 1.597 GB / 524。

## 4. 与预期不符之处

### 4.1 旧库只有 2.5 年日线，不能作长历史校验源

任务单的意图是"决定哪些可以作为新数据的交叉校验源"。实测旧库日线只覆盖 **2024-01-02 至 2026-07-16（613 个交易日）**，而 T002 已证明 Tushare 能回溯到 1991 年。旧库只能校验 2024 年以后这一段，无法覆盖更早历史。

### 4.2 5 张日线表里有 2 张晚 3 个月起步

`stock_adj_factors`、`stock_limit_daily`、`index_daily` 都从 **20240401** 开始，比 `daily_quotes` 晚一个季度。若用旧库做复权或涨跌停的交叉校验，2024Q1 这三个月没有对照数据。

### 4.3 18 张空表

69 张表里 18 张 0 行，包括 `backtest_runs`、`backtest_trade`、`backtest_daily_result`、`backtest_summary`、`positions`、`trade_logs`、`selection_runs`、`selection_results`、`strategy_candidate_pool`、`factor_scores`、`feature_snapshot` 等。说明旧系统**没有落过实盘持仓与交易记录，回测结果也没有入库**。

### 4.4 `db_backups` 不是数据库备份

目录名容易误解：里面是 8 个 JSON 导出 + `manifest.json` + `schema.json`，**没有 pg_dump 或二进制备份**，总量 202 KB。不能用于恢复 29 GB 的库。

### 4.5 `db_backups` 里多数文件是空数组

`manifest.json` 记录的行数：`positions` 0、`trade_logs` 0、`review_journals` 0、`selection_runs` 0、`selection_results` 0、`ml_v8_manual_reviews` 0；只有 `ml_v8_candidate_signals` 30、`ml_v8_candidate_outcomes` 35 有内容（对应 2 字节的 `[]` 文件）。

### 4.6 任务单没提但值得记的一条

旧库存在 `v8` 与 `v9` 两代命名（`ml_v8_candidate_*`、`v9_event_*` 的同步任务），说明旧系统迭代过快，同名概念有多套表。

## 5. 我做了哪些任务单没有要求的事

1. **额外执行了 1 条只读 SELECT**（`data_sync_runs` 的 `status` 分组统计、最近 6 条记录、`max(trade_date)`）。原因：任务单要求回答"最近一次成功同步是哪一天"，但脚本只给 `min/max/distinct`，没给按状态过滤的结果。会话仍为 `readonly=True`，只读。
2. **用 `ls` 列出了 `reports/db_backups` 的文件名/大小/时间**，并读了 `manifest.json`（1 KB，元数据，不含密钥），用于回答任务单第一个问题。未读其他备份文件内容。
3. 为撰写报告，用 python 读取 `legacy_inventory.json` 做汇总（只读）。
4. 未修改任何受保护文件；未安装任何包；未删除、移动、改名旧项目任何文件；未对数据库做任何写入。

## 6. 问题与建议

### 6.1 旧库作为"交叉校验源"的价值有限

只有 2.5 年、且 18 张空表、2 张日线表缺 2024Q1。建议只把 `daily_quotes` / `daily_basic` / `moneyflow_daily` 在 2024-01 至 2026-07 这段当作抽样核对用，不要当作主数据源或长历史基准。

### 6.2 `factor_snapshots` 占 22 GB，是清理的最大头

如果设计方后续要回收空间，这一张表占全库 71%、6.1M 行。**我没有做任何清理**，也不建议在没有设计方明确指令前动它。

### 6.3 `db_backups` 不具备恢复能力

若设计方原以为旧库有备份，需要纠正：这里只有小体量 JSON 导出。真正的 29 GB 库没有备份文件。

### 6.4 复权与涨跌停数据的起点要在设计里写清

2024Q1 这三个月在 `stock_adj_factors` / `stock_limit_daily` 里是空的。做回测时若用 2024 年初的数据，复权因子与涨跌停价会缺失，需要显式处理。

### 6.5 `partial_success` 的 14 条同步任务值得抽查

1,626 条同步记录里有 14 条 `partial_success`。任务单没要求，我也没有去查明细；如果设计方关心数据完整性，可以指定我去查这 14 条的 `message` 字段。

## 7. 任务单要求额外回答的两个问题

### 7.1 `reports/db_backups/` 里有哪些文件

路径：`C:\Users\Xiansheng Wu\Desktop\量化\reports\db_backups\20260717T005954Z\`，全部文件时间戳均为 **2026-07-17 08:59**。

| 文件 | 大小 |
|---|---|
| `manifest.json` | 1,101 B |
| `schema.json` | 104,466 B |
| `ml_v8_candidate_signals.json` | 68,802 B |
| `ml_v8_candidate_outcomes.json` | 15,696 B |
| `ml_v8_manual_reviews.json` | 2 B |
| `positions.json` | 2 B |
| `review_journals.json` | 2 B |
| `selection_results.json` | 2 B |
| `selection_runs.json` | 2 B |
| `trade_logs.json` | 2 B |

合计 202 KB。2 字节的文件内容为 `[]`（空数组）。目录里没有 SQL 备份或二进制备份。

### 7.2 旧数据库最近一次成功同步是哪一天

**2026-07-16。**

- `data_sync_runs` 中 `status='success'` 的最大 `trade_date` = `20260716`；
- 最后写入的一条记录 `created_at` = `2026-07-16 14:39:21`，任务类型 `v9_event_foundation`，`affected_rows` 22,171，状态 `success`；
- 与日线表的最大 `trade_date` `20260716` 一致。

## 8. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：19 个受保护文件与清单一致。
```

`python -m pytest -q`：

```
............                                                             [100%]
12 passed in 11.64s
```
