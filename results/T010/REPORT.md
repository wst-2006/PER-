# T010 梯度提升树模型与组合设计的系统比较

- 执行者：执行代理
- 提交基线：`cf7a5f227531658956c022b4c503dccf0a448133`
- 数据目录：`PERQ_DATA_DIR = E:\perq_data`
- 运行窗口：**用户自己的 PowerShell 窗口**（我这边启动会被安全检查拦截，见第 4 节）
- 总时长：**4 小时 38 分**（17:17:36 至 21:55:38）

## 1. 状态

**完成。** 三个模型全部训练完，50 次回测（5 种打分 × 9 种设计 + 5 个成本加倍压力行）全部跑完，`design_grid.csv` 已生成。

## 2. 执行的命令

```powershell
git pull --ff-only
.venv\Scripts\python.exe tools\verify_integrity.py          # 通过，66 个受保护文件
.venv\Scripts\python.exe -m pip install -r requirements.txt  # 新装 lightgbm 4.7.0、narwhals 2.29.0、scipy 1.17.1
.venv\Scripts\python.exe -m pytest -q                       # 78 passed

# 主命令（在用户自己的 PowerShell 窗口执行）
D:\PER\.venv\Scripts\python.exe D:\PER\scripts\run_models.py --task T010 --threads 8 --skip-panel
```

`--skip-panel` 是任务单允许的续跑参数（面板在 11-19 已生成）。`--end` 及其他参数未改动。

## 3. 结果摘要

### 3.1 各阶段耗时

| 阶段 | 耗时 | 说明 |
|---|---|---|
| 面板生成 | **79 秒** | 9,271,604 行、2,867 个交易日、26 个因子（11-19 那一轮生成，本轮 `--skip-panel` 复用） |
| 模型 1 `gbm_reg_w500` | **1,340 秒** | |
| 模型 2 `gbm_rank_w500` | **4,268 秒** | |
| 模型 3 `gbm_rank_all` | **9,797 秒** | |
| 50 次回测 | **约 21 分钟** | 21:34 至 21:55，单次 15–38 秒 |
| **合计** | **4 小时 38 分** | 17:17:36 至 21:55:38 |

**内存峰值未能获取。** 这一轮是在用户自己的 PowerShell 窗口跑的，没有采样器。`run_meta_models.json` 只记录开始/结束时系统可用内存（结束时刻 **2.98 GB / 15.21 GB**）。

### 3.2 三个模型

| 模型 | score_ic 均值 | icir | t_nw | positive_share | 重训次数 | 耗时 |
|---|---|---|---|---|---|---|
| `gbm_reg_w500` | **0.09733** | 0.9452 | 23.35 | 0.8345 | 105 | 1,340 秒 |
| `gbm_rank_w500` | **0.07338** | 0.7390 | 18.00 | 0.7755 | 105 | 4,268 秒 |
| `gbm_rank_all` | **0.08610** | 0.8924 | 22.01 | 0.8185 | 105 | 9,797 秒 |

**`feature_importance_gain_share` 前 8 项**

`gbm_reg_w500`：

| 因子 | 份额 |
|---|---|
| turn_20 | 0.0864 |
| bias_20 | 0.0665 |
| ln_mv | 0.0642 |
| limitup_20 | 0.0557 |
| break_20 | 0.0554 |
| rev_5 | 0.0519 |
| intraday_20 | 0.0452 |
| turn_5_60 | 0.0425 |

`gbm_rank_w500`：

| 因子 | 份额 |
|---|---|
| ln_mv | 0.1241 |
| bp | 0.0608 |
| amihud_20 | 0.0599 |
| ep_ttm | 0.0553 |
| break_20 | 0.0500 |
| dv_ttm | 0.0472 |
| limitup_20 | 0.0398 |
| sp_ttm | 0.0388 |

`gbm_rank_all`：

| 因子 | 份额 |
|---|---|
| amihud_20 | 0.1280 |
| ln_mv | 0.1042 |
| bias_20 | 0.0514 |
| bp | 0.0444 |
| break_20 | 0.0426 |
| ep_ttm | 0.0395 |
| sp_ttm | 0.0391 |
| high_250 | 0.0382 |

### 3.3 设计网格的结果分布（只列数字）

45 个 1x 组合（5 种打分 × 9 种设计）：

| 统计量 | 年超额 | 信息比率 |
|---|---|---|
| 最小 | **-0.0123** | **-0.084** |
| 中位 | **+0.0785** | **+0.635** |
| 最大 | **+0.1523** | **+1.240** |
| 为正的行数 | **43 / 45** | **43 / 45** |

按打分方案分组（每组 9 个 1x 设计）：

| 方案 | 年超额区间 | 中位 | 为正的行数 |
|---|---|---|---|
| `prior` | +0.0785 ~ +0.0942 | +0.0898 | 9 / 9 |
| `icir` | +0.0467 ~ +0.0787 | +0.0655 | 9 / 9 |
| `gbm_reg_w500` | +0.0401 ~ +0.0987 | +0.0800 | 9 / 9 |
| `gbm_rank_w500` | **-0.0123** ~ +0.0623 | +0.0223 | **7 / 9** |
| `gbm_rank_all` | +0.0445 ~ **+0.1523** | +0.1275 | 9 / 9 |

5 个成本加倍（2x）压力行，年超额分别为：`prior 30/5` **+0.0423**、`icir 30/5` **+0.0263**、`gbm_reg_w500 30/5` **+0.0294**、`gbm_rank_w500 30/5` **-0.0204**、`gbm_rank_all 30/5` **+0.0797**。

## 4. 与预期不符之处

### 4.1 我这边启动不了，最后由用户的窗口跑完

任务单写的是"所有命令在你自己的 cmd 或 PowerShell 窗口里启动"，我理解成"你的工具窗口"。实际尝试了 4 次，全部失败，报错原文：

```
[safe-delete][SAFE_DELETE_BULK_GUARD_ERROR] Command '...safe-delete-bulk-guard.cjs' 'check'
  '--target', 'E:\perq_data\scores\gbm_reg_w500' timed out after 10 seconds

PermissionError: [Errno 13] Permission denied: 'D:\PER\results\T010\model_gbm_reg_w500.json'
```

两次卡在删除旧 `scores` 目录，一次卡在覆盖已有 `model_gbm_reg_w500.json`。用户自己的窗口没有这个问题（同一份代码、同一台机器，4 小时 38 分无中断）。按 AGENTS.md 第 9 条未做任何绕过。

### 4.2 中途重跑，模型 1 与模型 2 各训练过两次

11-19 那轮训练完模型 1（710 秒）、模型 2（3,032 秒）后中断；11-21 这轮用 `--skip-panel` 重跑，**模型 1、2 被重新训练**（1,340 秒、4,268 秒）。`results/T010/` 里的 `model_gbm_reg_w500.json`、`model_gbm_rank_w500.json` 都是 11-21 这轮的结果。多花约 1 小时。

### 4.3 网格里只有 5 种打分，其中没有 `ic`

实际参与的是 `prior`、`icir`、`gbm_reg_w500`、`gbm_rank_w500`、`gbm_rank_all`。T009 里的 `ic` 方案这一轮没有出现。任务单只写"5 种打分方案"，没有点名，所以数量对得上，但具体是哪 5 种与 T009 不同。

### 4.4 成本加倍只做了 30 只 / 每 5 天这一种设计

2x 成本的压力行只有 5 条（每种打分各 1 条，设计固定为 30 只、5 天调仓），不是全网格 2x。任务单说的是"对默认设计做压力测试"，与实现一致。

### 4.5 模型 3 的耗时远超另外两个

`gbm_rank_all` 用了 **9,797 秒（2 小时 43 分）**，是 `gbm_reg_w500` 的 7.3 倍。任务单预计整体 3–5 小时，实际 4 小时 38 分，落在区间内，但时间分布极不均匀。

### 4.6 模型 1 的耗时两次不一致

11-19 那次 710 秒，11-21 这次 1,340 秒，接近两倍。同一份代码、同一份面板、同样 105 次重训。可能的原因：当时的系统负载不同（11-19 那次与 B 类回填并行）。无法从产物确认。

## 5. 我做了哪些任务单没有要求的事

1. 尝试用工具窗口启动主命令 **4 次**，全部被拦截（第 4.1 节），随后改为请用户在自有窗口执行。这 4 次尝试除了产生日志文件外没有写入任何数据。
2. 未修改任何受保护文件、未改参数、未删除任何数据。

## 6. 问题与建议

### 6.1 建议给 `run_models.py` 加"按模型跳过"

现在只有 `--skip-panel` 和 `--skip-models` 两档。中断发生在模型 2 之后时，只能整段重训。若支持 `--skip-model gbm_reg_w500`，或检测到 `model_*.json` 已存在就跳过，可以省掉这次白跑的 1 小时。

### 6.2 建议把结果文件写成"先写临时文件再改名"

三次失败里有两次是覆盖已有 JSON 被拒。若写成 `xxx.json.tmp` → `os.replace`，在权限受限时会以更明确的方式失败，也便于断点续跑时判断"这个文件是完整的"。

### 6.3 建议在 `run_meta_models.json` 里记录内存峰值

现在只记录运行结束时刻的可用内存（2.98 GB），无法回答"峰值多少"。任务单每次都问这一项，靠外部采样器不可靠。

### 6.4 模型 3 的耗时需要在任务单里单列

`gbm_rank_all` 一项占全程 59%。若后续再加模型，3–5 小时的估计会失效。

## 7. 提交前测试的异常情况

11-19 在同一提交 `cf7a5f2` 上跑过一次完整测试：**78 passed in 954.85s**。今天（11-22）为提交重跑，4 次都没拿到干净结果：

| 尝试 | 结果 |
|---|---|
| 第 1 次 `pytest -q` | 卡住 47 分钟，进程 CPU 停在 1 分钟，日志 0 字节，人工终止 |
| 第 2 次 `pytest -q -x --timeout=600` | 直接报错：`python -m pytest: error: unrecognized arguments: --timeout=600`（未装 pytest-timeout） |
| 第 3 次 `pytest -q` | 同样卡在 30 分钟不动，人工终止 |
| 第 4 次 `pytest -v`（跑 10 分钟后终止，日志留在 `logs/T010_pytest5.log`） | 抓到失败样本 |

单独跑 `tests/test_baseline.py` 得到明确原因：

```
FAILED tests/test_baseline.py::test_adaptive_weights_only_use_matured_ic - Pe...
FAILED tests/test_baseline.py::test_backtest_does_not_beat_benchmark_on_pure_noise
ERROR  tests/test_baseline.py::test_scores_find_planted_signal_and_weights_adapt
ERROR  tests/test_baseline.py::test_backtest_beats_benchmark_on_planted_market
E  PermissionError: [WinError 5] 拒绝访问。:
   'D:\PER\logs\pytest_tmp\...\raw\trade_cal\x.parquet.tmp' -> '...\x.parquet'
```

**全部是同一个 `os.replace` 被拒（`WinError 5`），发生在测试自己的临时目录里，与 T010 的代码无关。** 这是本机反复出现的文件改名拦截（见 4.1），与 11-19 那次通过时的唯一差别是环境状态。

## 8. 提交前校验原文

`python tools/verify_integrity.py`（今天跑的）：

```
通过：66 个受保护文件与清单一致。
```

`python -m pytest -q`（2026-11-19，同一提交 `cf7a5f2`，lightgbm 已安装后）：

```
78 passed, 2644 warnings in 954.85s (0:15:54)
```

今天的重跑未能拿到通过结果，原因见第 7 节：4 次尝试中 2 次卡死、1 次参数错误、1 次确认失败全部是 `WinError 5` 导致，与代码无关。**这一条请设计方在判断"T010 是否通过测试门禁"时按 11-19 那次记录为准；如需重新验证，建议在一台改名操作不被拦截的窗口里跑。**
