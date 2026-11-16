# T009 基线闭环回测

- 执行者：执行代理
- 仓库提交基线：`3228df9`
- 数据目录：`PERQ_DATA_DIR = E:\perq_data`
- 运行方式：PowerShell 启动；与后台 B 类回填并行（两者读写的文件不重叠）

## 1. 状态

**完成。** 两条命令均 `rc=0`：单因子评估重跑成功（`limitup_20`、`break_20` 已有完整年度结果），三种合成方案的打分与 2016–2024 回测全部产出。

## 2. 执行的命令

```powershell
git pull --ff-only                                   # 拉到 3228df9
.venv\Scripts\python.exe tools\verify_integrity.py   # 通过，61 个受保护文件

# 两条命令都套了计时/量内存的外壳（见第 5 节），被执行的是原脚本本体
.venv\Scripts\python.exe logs\_measure.py scripts\build_factors.py --skip-build --task T009
.venv\Scripts\python.exe logs\_measure.py scripts\run_baseline.py --task T009
```

`--start` / `--end` / 其他参数均未改动。

## 3. 结果摘要

### 3.1 耗时与峰值内存

| 步骤 | 耗时 | 进程峰值工作集 |
|---|---|---|
| `build_factors --skip-build` | **150 秒** | **1,395 MB** |
| `run_baseline` | **175 秒** | **1,411 MB** |

两条都远快于任务单估计（第一条约 3 分钟符合；第二条估计 15–30 分钟，实测 175 秒）。后台 B 类回填同时在跑，未见明显争抢。

### 3.2 合成参数

`horizon = 5`、`window = 250`、`min_history = 60`、`days = 2417`，权重每 20 个交易日采样一次。

## 4. 任务单要求列出的四项

### 4.1 `limitup_20` 与 `break_20`（修复后）

```csv
limitup_20,涨停生态,-1,-0.03463,-0.4176,-10.59,-0.02581,-7.72,1.0,-0.007192,-0.00308,-0.988,0.082
break_20,涨停生态,-1,-0.00661,-0.0765,-1.97,-0.00236,-0.7,0.6,-0.002639,7.4e-05,-0.152,0.0885
```

对照 T008（修复前）：`limitup_20` 的 IC 由 -0.05672 变为 **-0.03463**，`t_nw` 由 -2.58 变为 **-10.59**；`break_20` 的 IC 由 -0.03301 变为 **-0.00661**，`t_nw` 由 -2.75 变为 **-1.97**。两行的 `years_same_sign` 分别为 1.0 和 0.6。

### 4.2 `composite_summary.json` 的 `schemes` 原文

```json
{
 "prior": {
  "ic_mean": 0.08769,
  "icir": 0.8896,
  "t_nw": 23.12,
  "positive_share": 0.8158
 },
 "ic": {
  "ic_mean": 0.08948,
  "icir": 0.8895,
  "t_nw": 23.03,
  "positive_share": 0.8162
 },
 "icir": {
  "ic_mean": 0.0889,
  "icir": 0.9215,
  "t_nw": 23.86,
  "positive_share": 0.822
 }
}
```

### 4.3 `baseline_table.csv` 全文与三份明细

```csv
scheme,annual_return,benchmark_annual_return,annual_excess,information_ratio,max_drawdown,max_relative_drawdown,years_with_positive_excess,annual_turnover_two_sided,fees_share_of_start_nav_per_year,average_positions
prior,0.0648,-0.0229,0.0898,0.815,-0.4819,-0.1916,0.778,36.1,0.03184,30.4
ic,0.0341,-0.0229,0.0583,0.512,-0.4647,-0.2024,0.667,37.13,0.03161,30.8
icir,0.054,-0.0229,0.0787,0.763,-0.4637,-0.1481,0.889,40.82,0.03633,30.6
```

三份 JSON 的共同参数：`rebalance = 5`（每 5 个交易日）、`buffer = 2.0`、`start = 20160104`、`end = 20241231`、`n = 30`。

**`prior` 的 `by_year`**

| 年 | 策略 | 基准 | 超额 |
|---|---|---|---|
| 2016 | +0.2515 | -0.1080 | +0.4030 |
| 2017 | -0.0747 | -0.1502 | +0.0889 |
| 2018 | -0.3413 | -0.3599 | +0.0291 |
| 2019 | +0.2945 | +0.2595 | +0.0278 |
| 2020 | +0.0641 | +0.1862 | **-0.1030** |
| 2021 | +0.4087 | +0.2158 | +0.1587 |
| 2022 | +0.0219 | -0.1402 | +0.1886 |
| 2023 | +0.1585 | +0.0371 | +0.1171 |
| 2024 | +0.0018 | +0.0335 | **-0.0307** |

`unfilled_by_reason`：`{"sell:carried:suspended": 1127, "buy:failed:cash": 39, "sell:carried:limit_down": 75, "sell:forced:delisted": 1}`

**`ic` 的 `by_year`**

| 年 | 策略 | 基准 | 超额 |
|---|---|---|---|
| 2016 | +0.3457 | -0.1080 | +0.5087 |
| 2017 | -0.0624 | -0.1502 | +0.1033 |
| 2018 | -0.3586 | -0.3599 | +0.0021 |
| 2019 | +0.2878 | +0.2595 | +0.0224 |
| 2020 | +0.0014 | +0.1862 | **-0.1558** |
| 2021 | +0.3176 | +0.2158 | +0.0837 |
| 2022 | -0.1455 | -0.1402 | **-0.0061** |
| 2023 | +0.1762 | +0.0371 | +0.1342 |
| 2024 | -0.0233 | +0.0335 | **-0.0549** |

`unfilled_by_reason`：`{"sell:carried:suspended": 2290, "buy:failed:cash": 112, "sell:carried:limit_down": 110, "buy:failed:lot": 5}`

**`icir` 的 `by_year`**

| 年 | 策略 | 基准 | 超额 |
|---|---|---|---|
| 2016 | +0.3333 | -0.1080 | +0.4947 |
| 2017 | -0.1056 | -0.1502 | +0.0525 |
| 2018 | -0.3219 | -0.3599 | +0.0594 |
| 2019 | +0.3242 | +0.2595 | +0.0513 |
| 2020 | +0.0309 | +0.1862 | **-0.1309** |
| 2021 | +0.3880 | +0.2158 | +0.1416 |
| 2022 | -0.1302 | -0.1402 | +0.0117 |
| 2023 | +0.1492 | +0.0371 | +0.1081 |
| 2024 | +0.0457 | +0.0335 | +0.0118 |

`unfilled_by_reason`：`{"sell:carried:suspended": 1532, "buy:failed:cash": 33, "sell:carried:limit_down": 78, "buy:failed:lot": 3}`

### 4.4 两条命令的耗时与内存峰值

见 3.1。

## 5. 我做了哪些任务单没有要求的事

1. **沿用 `logs/_measure.py` 外壳**（不入库，`logs/` 已被 `.gitignore` 排除）。任务单第 4 项要求耗时与内存峰值，脚本自身不输出这两项，所以用 `runpy` 在原进程内取 `PeakWorkingSetSize` 与墙钟时间。被执行的仍是脚本本体，参数未改。
2. 未调用 Tushare，未修改任何受保护文件，未改任何参数。

## 6. 与预期不符之处

### 6.1 第二条命令的耗时远低于估计

任务单估计"每种方案约 5 到 10 分钟"，三种方案加上打分合计实测 **175 秒**。三种方案的实际耗时未分别打印，只有总时长。

### 6.2 合成阶段有 215 条 `RuntimeWarning`

原文（按出现次数归并）：

```
107 × D:\PER\.venv\Lib\site-packages\numpy\lib\_nanfunctions_impl.py:1997: RuntimeWarning: Degrees of freedom <= 0 for slice.
106 × D:\PER\perq\research\composite.py:74: RuntimeWarning: Mean of empty slice
  1 × D:\PER\perq\research\composite.py:76: RuntimeWarning: divide by zero encountered in divide
```

发生位置是 `composite.py` 的 `mean = np.nanmean(hist, axis=0)` 与 `mean / std`。按代码逻辑，样本期开头 `matured_days = 0`（没有已实现的 5 日收益），`hist` 为空，于是求均值得到 NaN 并触发警告。**结果没有中断，`rc=0`，但权重在这一段是 NaN 还是被后续覆盖，报告里无法从外部确认。**

### 6.3 2020 年三种方案的超额都是负的

`prior` -0.1030、`ic` -0.1558、`icir` -0.1309；2024 年 `prior` 与 `ic` 也是负的（-0.0307、-0.0549）。仅陈述数字。

### 6.4 `ic` 方案的停牌拖累明显高于另两种

`sell:carried:suspended`：`prior` 1,127 次、`ic` 2,290 次、`icir` 1,532 次；`buy:failed:cash`：39 / 112 / 33。三者使用同一套撮合规则，差异来自换手与持仓路径。

## 7. 问题与建议

### 7.1 建议在 `composite.py` 里显式处理空历史

6.2 的 215 条警告都是同一个原因。建议在 `matured_days < min_history` 时直接跳过该日权重计算，而不是依赖 `nanmean` 返回 NaN 再由下游处理。这样可以确认早期权重不是 NaN。

### 7.2 三种方案的耗时没有分开打印

6.1 只给总时长，无法判断某个方案是否异常慢。建议在 `run_baseline.py` 里对每个 scheme 单独打卡。

## 8. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：61 个受保护文件与清单一致。
```

`python -m pytest -q`（测试期间 B 类回填同时在跑）：

```
73 passed, 2644 warnings in 764.67s (0:12:44)
```
