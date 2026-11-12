# T004 小样本导出

- 执行者：执行代理
- 执行时间（UTC）：2026-11-12T04:44:07Z — 04:49:54Z，耗时 5 分 47 秒
- 仓库提交基线：`7d0784924882f19de13d1e8e53d70e647c47a8bb`（T003 之后）

## 1. 状态

**完成。** `sample_manifest.json` 的 `status` 为 `completed`，**失败调用 0 次**。

| 验收项 | 要求 | 实际 | 结论 |
|---|---|---|---|
| `status` | `completed` 或 `completed_with_failures` | `completed` | 满足 |
| 压缩包大小 | ≤ 50 MB | **20.3 MB**（21,289,458 字节） | 满足 |
| 数据是否入库 | 不得提交 | `samples/` 被 `.gitignore` 第 10 行排除，`git status` 只显示 `results/T004/` | 满足 |

## 2. 执行的命令

```bash
git pull --ff-only                                     # 失败，schannel 中断；改用 API 核对（见 4.1）
python tools/verify_integrity.py                       # 通过
.venv/Scripts/python.exe scripts/sample_pull.py        # 本次任务主体
git check-ignore -v samples/perq_sample.zip            # 确认被 .gitignore 排除
```

## 3. 结果摘要

| 项 | 值 |
|---|---|
| 样本股票数 | **44** |
| 分组 | main_sh 8、main_sz 8、chinext 8、star 8、bse 4、st 4、delisted 4 |
| 输出 parquet 文件 | **45** 个 |
| 总行数 | **520,462** |
| 全市场截面日期 | 20180209、20200203、20240205、20240930、20261111 |
| 最近交易日 | 20261111（当天 20261112 尚未收盘入库，脚本取倒数第二个交易日） |
| 压缩包 | `samples/perq_sample.zip`，**20.3 MB** |
| 压缩包 sha256 | `f65f117135ce78c7c8456decfdfd973d4ab8a3ffcbf1e5c7ec5205ce7ec34b00` |
| 实际调用次数 | 739（3+1+220+396+44+25+1+44+5），失败 0 |

主要文件的行数与列数：

| 文件 | 行数 | 列数 |
|---|---|---|
| `stock_daily` | 75,638 | 11 |
| `stock_adj_factor` | 77,992 | 3 |
| `stock_daily_basic` | 75,074 | 18 |
| `stock_stk_limit` | 75,569 | 4 |
| `stock_moneyflow` | 72,661 | 20 |
| `stock_balancesheet` | 3,201 | 152 |
| `stock_fina_indicator` | 3,527 | 108 |
| `stock_income` | 3,526 | 85 |
| `stock_cashflow` | 3,115 | 97 |
| `index_daily`（5 个指数） | 15,625 | 11 |
| `xs_daily_20261111`（全市场） | 5,574 | 11 |

样本股票名单（按分组，来自 `sample_manifest.json` 的 `groups`）：

- main_sh：600000.SH、600299.SH、600596.SH、600860.SH、601518.SH、603108.SH、603375.SH、603823.SH
- main_sz：000001.SZ、000650.SZ、000949.SZ、002023.SZ、002223.SZ、002418.SZ、002629.SZ、002846.SZ
- chinext：300001.SZ、300204.SZ、300409.SZ、300593.SZ、300777.SZ、300957.SZ、301152.SZ、301361.SZ
- star：688001.SH、688092.SH、688181.SH、688275.SH、688362.SH、688501.SH、688597.SH、688699.SH
- bse：920000.BJ、920126.BJ、920284.BJ、920656.BJ
- st：000010.SZ、002569.SZ、300366.SZ、600730.SH
- delisted：000004.SZ、002280.SZ、300309.SZ、600462.SH

## 4. 与预期不符之处

### 4.1 `git pull` 再次失败（网络）

```
fatal: unable to access 'https://github.com/wst-2006/PER-.git/': schannel: server closed abruptly (missing close_notify)
```

改用 API 核对：本地 `7d07849`，远端 `7d07849`，一致，无遗漏提交。这是这台机器到 github.com 的间歇性故障，与 T001 记录的是同一现象。

### 4.2 耗时短于预期

任务单预计约 10 分钟、约 700 次调用。实际 **5 分 47 秒**、739 次调用，全部成功。原因是单只股票的历史区间一次调用即可返回，没有额外分页。

### 4.3 北交所样本代码是 920 段，不是 8/4 段

`bse` 组 4 只全部是 `920xxx.BJ`（920000、920126、920284、920656）。若设计方按旧印象预期 `83xxxx`/`87xxxx`，需要注意北交所代码段已经切换，脚本行为本身是对的（取自 `stock_basic` 的在市列表）。

### 4.4 `stock_sw_member` 只有 44 行

`index_member_all` 对每只股票只返回**当前**行业归属一行（每股 1 条），不是历史区间。若要用它做"当时的行业"以避免行业变更带来的前视偏差，这份数据不够。

### 4.5 停牌表在四个截面上的差异极大

`xs_suspend_d` 行数：20180209 **362**、20200203 **17**、20240205 **10**、20240930 **13**、20261111 **7**。2020-02-03 是春节后首个交易日（疫情），停牌数反而极少，与 2018 年的 362 只形成鲜明对比。数据本身合理，但说明"停牌比例"不能当作稳定特征使用。

## 5. 我做了哪些任务单没有要求的事

1. 为撰写报告，读取 `sample_manifest.json` 做汇总，并用 `zipfile` 核对压缩包成员数（45）与字节数（只读）。
2. 用 `git check-ignore` 确认 `samples/` 被忽略（只读）。
3. 未修改任何受保护文件；未安装任何包；未把 `samples/`、任何 parquet 或 zip 加入暂存区。

## 6. 问题与建议

### 6.1 压缩包需要用户手工转交

`samples/perq_sample.zip` 在仓库目录内但被忽略，不会入库。请用户把它直接发给设计方。设计方在仓库里只能看到 `results/T004/sample_manifest.json`（行数、列名、日期范围、单文件 sha256）。

### 6.2 若要做行业历史，需要另取数据

见 4.4。建议设计方明确行业归属是"用当前值"还是"用历史值"，前者会有前视偏差。

### 6.3 财务表列数很宽

`balancesheet` 152 列、`fina_indicator` 108 列、`cashflow` 97 列、`income` 85 列。这份样本适合核对字段含义与空值分布，建议设计方先用它跑一遍空值率统计，再定字段白名单。

### 6.4 样本覆盖到 2018 年，不含更早

脚本 `START_DATE=20180101`。T002 已证明日线可回溯到 1991 年，但这份样本只到 2018 年。若设计方要验证更早年份的字段差异（例如 2007 年会计准则变更前后的报表结构），需要另取样本。

## 7. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：19 个受保护文件与清单一致。
```

`python -m pytest -q`：

```
............                                                             [100%]
12 passed in 9.14s
```
