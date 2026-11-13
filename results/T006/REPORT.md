# T006 数据层试跑

- 执行者：执行代理
- 执行时间（UTC）：2026-11-13T02:31Z — 03:10:03Z（三次运行，见 2 节）
- 仓库提交基线：`21d57cbad590f8c2153aabcecd479bbdaf257189`
- 数据目录：`PERQ_DATA_DIR = E:\perq_data`；试跑落在 `E:\perq_data\_trial`，样本落在 `E:\perq_data\_sample`

## 1. 状态

**完成。** 三个步骤跑通；采集试跑覆盖 **121 个数据集**，288 次调用，最终**所有分区均已落盘，无残留 failed 记录**。

## 2. 执行的命令

```bash
git pull --ff-only                                    # HEAD == origin/main == 21d57cb
python tools/verify_integrity.py                      # 通过，46 个受保护文件

# 第 1 步
.venv/Scripts/python.exe -m pytest -q                 # 53 passed in 486.40s

# 第 2 步
.venv/Scripts/python.exe scripts/load_sample.py --out "E:\perq_data\_sample"
.venv/Scripts/python.exe scripts/build_curated.py --data-dir "E:\perq_data\_sample" --sample --task T006
.venv/Scripts/python.exe scripts/build_labels.py --data-dir "E:\perq_data\_sample" --task T006 --suffix _sample

# 第 3 步（主跑）
.venv/Scripts/python.exe scripts/backfill.py --trial 2 --task T006 --data-dir "E:\perq_data\_trial"
# 第 3 步（补跑一轮，把主跑失败的分区补齐）
.venv/Scripts/python.exe scripts/backfill.py --trial 2 --task T006 --data-dir "E:\perq_data\_trial"
```

三点与任务单字面不同，都写在 4.1–4.3：加了 `--data-dir`；第 3 步跑了两轮；pytest 这次没有再手动加 `--basetemp`（新提交的 `pytest.ini` 已内置）。

## 3. 结果摘要

### 3.1 测试

```
53 passed in 486.40s (0:08:06)
```

比 T005 的 51 个多 2 个（新一轮提交新增了数据层断言）。

### 3.2 整理层与质量门禁（样本）

```
日线主表 75287 行，20180102 至 20261111
数据质量门禁：通过
  通过  duplicate_keys = 0（阈值 0）
  通过  nonpositive_price_rows = 0（阈值 0）
  通过  high_below_low_rows = 0（阈值 0）
  通过  close_outside_limit_rows = 0（阈值 0）
  通过  adj_factor_missing_rate = 0.0（阈值 0.001）
  通过  limit_missing_rate = 0.002842（阈值 0.02）
  通过  ret_mismatch_rate = 0.0（阈值 0.001）
  通过  fin_avail_before_period_end = 0（阈值 0）
```

**8 项门禁全部通过。** 覆盖率门禁在 `--sample` 下按设计跳过，实测最低年中位数 0.0072（样本仅 44 只股票，必然低）。

新增的行业字段：`industry_rows = 44`、`industry_missing_rate = 0.0`、`industry_backfilled_rate = 0.14164`（14.16% 的股票-日行业归属靠回填得到，不是当日快照）。

整理层规模：日历 3126 天、股票 5923 只、日线主表 75,287 行（原始 75,638，剔除上市前/非交易日 351，孤儿行 0）。

### 3.3 标签

```
rows 75287，entry_ok_rate 0.99154
h5 : labelled 74450，mean_fwd 0.001818，exit_delayed 0.00171，exit_blocked 0.00418，terminal 18
h10: labelled 74250，mean_fwd 0.003478，exit_delayed 0.00257，exit_blocked 0.00451，terminal 37
h20: labelled 73851，mean_fwd 0.007389，exit_delayed 0.00348，exit_blocked 0.00454，terminal 59
```

与旧基线（76ac0a8）逐项一致，样本数据没变，标签逻辑也没变。

### 3.4 采集试跑

| 轮次 | 调用 | 用时 | 计划分区 | 成功 | 空 | 失败 |
|---|---|---|---|---|---|---|
| 主跑 | 288 | 1228 秒（20 分 28 秒） | 280 | 230 | 43 | 7 |
| 补跑 | 214 | 509 秒 | — | — | — | **0** |

主跑累计 **273,752 行**，121 个数据集，`truncation_suspected` 全为 0，`stopped_by_budget=false`。

补跑之后核对 `_manifest`：**7 个失败分区全部转为 `ok`，当前无任何 `failed` 记录**。

数据落盘：`E:\perq_data\_trial` **65 MB**；`E:\perq_data\_sample` **169 MB**。E 盘剩余 445 GB。

## 4. 与预期不符之处

### 4.1 第 1 次尝试整段被网络打断（已重跑，非任务单要求）

第一次跑第 3 步时，跑到 180 秒、280 次调用后**整段断网**，此后 45 个数据集全部报：

```
ProxyError: HTTPConnectionPool(host='127.0.0.1', port=54407): Max retries exceeded with url:
http://api.waditu.com/dataapi/daily (Caused by ProxyError('Unable to connect to proxy',
RemoteDisconnected('Remote end closed connection without response')))
```

诊断：本机 DNS 把 `api.waditu.com` 解析到 `198.18.0.63`、`api.github.com` 解析到 `198.18.0.64`（fake-ip 段），而 `www.baidu.com` 解析正常。是代理客户端停止转发，不是 Tushare 或脚本问题。用户恢复代理后，我把该轮残留目录改名留档（`_trial_p1`），在干净目录上重跑。

### 4.2 主跑仍有 7 个失败，全部是网络层

| 数据集 | 失败分区 | `last_error` 原文 |
|---|---|---|
| `sw_member_801950_N` | 20261113 | `ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port=56407): Read timed out. (read timeout=30)` |
| `adj_factor` | 20261113 | `ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=56407): Read timed out.` |
| `margin` | 20261113 | `ReadTimeout: ... Read timed out. (read timeout=30)` |
| `balancesheet_vip_rt1` | 20260930 | `ProxyError: ... Max retries exceeded with url: http://api.waditu.com/dataapi/balancesheet_vip` |
| `balancesheet_vip_rt4` | 20260930 | 同上 |
| `index_daily_000905_SH` | 2026 | `ReadTimeout: ... Read timed out. (read timeout=30)` |
| `ths_daily` | 20261113 | `ProxyError: ... Max retries exceeded with url: http://api.waditu.com/dataapi/ths_daily` |

补跑后 7 个分区全部成功。**没有一条是"参数错误"或"返回日期与请求不一致"**，所以第 7.4 节不需要查文档。

### 4.3 补跑不是"纯粹重试"

`backfill.pending` 会把"还没拉过的分区"也算进待办，`--trial 2` 再从待办里取首尾各 2 个。因此补跑除了重试那 7 个失败分区，还顺手拉了下一批"次早/次近"的分区（214 次调用）。结果是 `_trial` 里多数数据集从 4 个分区变成 7 个。这不影响参数验证，但**交付的 `backfill_summary_trial.json` 是主跑那一轮**，补跑的摘要另存在 `logs/backfill_second_pass.json`（不入库）。

### 4.4 43 个空分区，四类原因

| 类别 | 数据集数 | 分区数 | 说明 |
|---|---|---|---|
| 当天盘中 | 20 | 20 | 均为 `20261113`：运行时刻 10:39–11:00（盘中），当天数据未生成 |
| 自然日假期 | 5 | 7 | `dividend`/`repurchase`/`share_float`/`stk_holdertrade`/`stk_holdernumber` 的 2015-01-01～02 |
| 报告期无该版本 | 6 | 12 | `income`/`balancesheet`/`cashflow` 的 `rt4`（调整合并）、`rt5`（调整前合并）在最新两季为空；`balancesheet_vip_rt4` 另有 20130331 一个缺口 |
| 指数尚未发布 | 2 | 4 | `000688.SH`（科创50，2020 起）、`899050.BJ`（北证50，2022 起）的 2014、2015 |

`stock_basic_P` 的 1 个空分区是"暂停上市 0 只"；`sw_member_801780_N` 的 1 个空分区是"银行板块无剔除记录"（与 T005 结论一致）。

### 4.5 2021 年 `limit_missing_rate` 单年超阈值

全样本 0.002842（阈值 0.02）通过，但 `by_year` 里 2021 年 = **0.02467**，已超 0.02。其余年份 0 或 0.00096。

### 4.6 财务时点数据的两组异常计数

| 表 | 行数 | `avail_over_400_days_after_period_end` | `stock_periods_original_version_missing` |
|---|---|---|---|
| `fin_income` | 2817 | **202** | **64** / 2763 |
| `fin_balancesheet` | 2224 | 119 | 43 / 2182 |
| `fin_cashflow` | 2456 | 150 | 41 / 2425 |

`avail_before_period_end = 0`（没有任何"报告期未结束就可获得"的记录）。

### 4.7 约 400 次调用，实际 288 次

任务单写"约 400 次调用"。实际 121 个数据集中 68 个是快照（各 1 个分区）、53 个是时间序列（各 4 个分区）= 280 个分区，加 8 次分页调用 = 288。差异来自"每个数据集只取首尾 2 个分区"这条规则本身。

### 4.8 `run_meta.json` 只反映最后一次运行

`run_meta.json` 记的是补跑（03:01:32–03:10:03），不是主跑。主跑的起止时间只能从 `backfill_summary_trial.json` 的 `elapsed_seconds = 1228` 反推（约 02:39–03:00Z）。这是脚本共用同一路径所致。

### 4.10 `pytest.ini` 的固定临时目录，第二次运行会失败

新提交把 `--basetemp=.pytest_tmp` 写进了 `pytest.ini`。第一次运行（3.1 的 53 passed）正常；**同一环境下再跑一次**就失败了：`29 passed, 24 errors`，错误全部发生在用例 setup 阶段：

```
path = '\\\\?\\D:\\PER\\.pytest_tmp', ignore_errors = False
...
    _try_trash(abs_path, recursive=True)
C:\Users\...\sandbox-center-runtime-kit\shim\sitecustomize.py:1362
```

原因：pytest 启动时会清空 basetemp，而本机把 `shutil.rmtree` 换成了"进回收站"的实现，删除量超阈值即拒绝，于是所有需要临时目录的用例在 setup 就报错。**不是测试代码或被测代码的问题**，前一次同样的代码是 53 passed。

绕过方式（第 8 节用的就是它）：每次给一个全新的 basetemp，并临时清空 ini 里的 addopts：

```bash
.venv/Scripts/python.exe -m pytest -q -o addopts="" --basetemp="logs/pt_$(date +%s)"
```

### 4.11 pytest 耗时不稳

同一批 53 个测试：486.40 秒（本次）与 571.88 / 464.14 秒（上一基线的两次）。波动近 25%，原因未查证，运行期可用内存 2.06 GB。

## 5. 我做了哪些任务单没有要求的事

1. **第 3 步加了 `--data-dir "E:\perq_data\_trial"`**。原因：`E:\perq_data\raw` 里已有上一轮试跑写入的分区，若沿用默认目录，"每个数据集最早/最近 2 个分区"就会变成"最早/最近 2 个尚未拉取的分区"，试跑口径失真。
2. **第 3 步跑了两轮**（主跑 + 补跑），用于确认 7 个失败是网络抖动还是参数问题。
3. **把中断那一轮的残留目录改名为 `_trial_p1`、把上一轮样本目录改名为 `_sample_r1`**，没有删除任何数据，只是改名留档。
4. 删除了一个 0 字节的 Git 残留锁文件 `.git/refs/remotes/origin/main.lock`（由我一次被超时打断的 `git pull` 留下，导致后续 `fetch` 报 `unable to update local ref`）。
5. 丢弃了基于旧任务单（76ac0a8）产生的一条未推送提交，按新任务单整体重做。
6. 未修改任何受保护文件；未安装新包；未提交任何数据文件。

## 6. 问题与建议

### 6.1 当天盘中产生的空分区，当天不会被补（建议优先处理）

`RawStore.write` 对 0 行写 `status="empty"`；`pending` 只重拉"无记录"或 `status="failed"`；`refresh_tail` 要求 `fetched_at` 的日期早于今天。三者叠加的结果是：**盘中跑出来的 `empty` 分区，当天收盘后重跑也不会补**，要等次日进入 tail 窗口。本次 20 个数据集在 `20261113` 都是这样。建议把 `empty` 纳入当天重试，或在流程里显式判断"数据未就绪"。

### 6.2 逐年门禁 vs 全样本门禁

4.5 的 2021 年 2.47% 被全样本平均掩盖。若回测要按年切分，建议门禁也按年判定。

### 6.3 财务"可获得日"口径需确认

4.6 的两组数字（202 条晚 400 天、64 个原始版本缺失）不在门禁里，但直接决定标签有无前视偏差。建议设计方核对 `avail_date` 的推导规则在 2015 年以前是否仍成立。

### 6.4 翻页逻辑仍未真正被验证

`fetch_all` 的翻页条件是"返回行数恰为 500 的整数倍"，本次 288 次调用 `truncation_suspected` 全 0，说明一次都没触发。Tushare 多数接口不支持 `offset`，T007 全量时若某个分区恰好返回 5000/6000/8000 行，会走到未验证的路径。

### 6.5 `cal` 模式会稳定制造空分区

5 张公告类表按自然日分区，2015 年至今约 4300 天，全量回填会各产生约 4300 条分区记录，其中大部分是周末与假期。建议先按交易日历过滤，或允许 `empty` 不进 manifest。

### 6.6 补跑会改变试跑口径

4.3 已述。若设计方希望"重试只补失败分区"，`pending` 需要区分"失败重试"与"未拉取"两种待办。

### 6.7 `pytest.ini` 的固定 basetemp 建议改掉

4.10 已述：同一台机器上第二次运行会整体报错（24 个 errors），看起来像代码坏了，实际是临时目录清理被拦。建议改成每次唯一的目录（例如 `--basetemp=.pytest_tmp/{时间戳}`），或在文档里写明"每次跑之前先换目录"。这条不解决，后续每个任务都会遇到一次假失败。

## 7. 任务单要求列出的五项

### 7.1 八项门禁是否全部通过

**全部通过**（原文见 3.2）。`dq_report_sample.json` 的 `pass = true`；覆盖率门禁在 `--sample` 下按设计跳过。

### 7.2 `failed > 0` 或 `skipped_after_failures` 为真的数据集

主跑有 **7 个**数据集各失败 1 次，`skipped_after_failures` 全为 `false`，明细与错误原文见 4.2。补跑后全部转为成功，当前 `_manifest` 中无 `failed` 记录。

### 7.3 `truncation_suspected > 0` 的数据集

**没有。** 121 个数据集全部为 0。

### 7.4 是否需要查文档纠正参数

**不需要。** 7 个失败全部是 `ReadTimeout` / `ConnectionError` / `ProxyError`，没有"参数错误"，`_date_mismatch` 在 288 次调用中一次都没报警（即没有"返回日期与请求分区不一致"）。未改动任何代码。

### 7.5 申万一级行业代码核对（新增）

**31 个代码全部正确，无错写、无漏写。**

做法：读取 `_trial` 里 `index_classify_SW2021` 快照（511 行，其中 L1 31 个、L2 134 个、L3 346 个），把 `level == 'L1'` 的 31 个 `index_code` 与 `perq/data/catalog.py` 的 `SW_L1_CODES` 做集合比较：

- 官方有、清单没有：**空集**
- 清单有、官方没有：**空集**
- 交集：31 个，一一对应

官方 31 个一级行业（按代码排序）：农林牧渔 801010、基础化工 801030、钢铁 801040、有色金属 801050、电子 801080、家用电器 801110、食品饮料 801120、纺织服饰 801130、轻工制造 801140、医药生物 801150、公用事业 801160、交通运输 801170、房地产 801180、商贸零售 801200、社会服务 801210、综合 801230、建筑材料 801710、建筑装饰 801720、电力设备 801730、国防军工 801740、计算机 801750、传媒 801760、通信 801770、银行 801780、非银金融 801790、汽车 801880、机械设备 801890、煤炭 801950、石油石化 801960、环保 801970、美容护理 801980。

补充观察：62 个 `sw_member_*` 快照数据集合计返回 7,921 行；其中 `sw_member_801780_N`（银行的历史剔除成员）为 0 行，与 T005 的结论一致 —— 银行板块在 `index_member_all` 里没有任何剔除记录。其余 30 个行业的 `_N` 均有非空 `out_date`。

## 8. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：46 个受保护文件与清单一致。
```

`python -m pytest -q -o addopts="" --basetemp="logs/pt_<时间戳>"`（为什么不用 ini 里的固定目录，见 4.10）：

```
53 passed in 472.61s (0:07:52)
```

## 附录 A：采集试跑明细（主跑，121 个数据集）

| 数据集 | 计划 | 成功 | 空 | 失败 | 行数 |
|---|---|---|---|---|---|
| `trade_cal` | 1 | 1 | 0 | 0 | 13527 |
| `stock_basic_L` | 1 | 1 | 0 | 0 | 5579 |
| `stock_basic_D` | 1 | 1 | 0 | 0 | 344 |
| `stock_basic_P` | 1 | 0 | 1 | 0 | 0 |
| `sw_member_801010_Y` | 1 | 1 | 0 | 0 | 124 |
| `sw_member_801010_N` | 1 | 1 | 0 | 0 | 60 |
| `sw_member_801030_Y` | 1 | 1 | 0 | 0 | 465 |
| `sw_member_801030_N` | 1 | 1 | 0 | 0 | 313 |
| `sw_member_801040_Y` | 1 | 1 | 0 | 0 | 50 |
| `sw_member_801040_N` | 1 | 1 | 0 | 0 | 16 |
| `sw_member_801050_Y` | 1 | 1 | 0 | 0 | 160 |
| `sw_member_801050_N` | 1 | 1 | 0 | 0 | 78 |
| `sw_member_801080_Y` | 1 | 1 | 0 | 0 | 530 |
| `sw_member_801080_N` | 1 | 1 | 0 | 0 | 174 |
| `sw_member_801110_Y` | 1 | 1 | 0 | 0 | 108 |
| `sw_member_801110_N` | 1 | 1 | 0 | 0 | 34 |
| `sw_member_801120_Y` | 1 | 1 | 0 | 0 | 129 |
| `sw_member_801120_N` | 1 | 1 | 0 | 0 | 58 |
| `sw_member_801130_Y` | 1 | 1 | 0 | 0 | 119 |
| `sw_member_801130_N` | 1 | 1 | 0 | 0 | 55 |
| `sw_member_801140_Y` | 1 | 1 | 0 | 0 | 172 |
| `sw_member_801140_N` | 1 | 1 | 0 | 0 | 15 |
| `sw_member_801150_Y` | 1 | 1 | 0 | 0 | 530 |
| `sw_member_801150_N` | 1 | 1 | 0 | 0 | 112 |
| `sw_member_801160_Y` | 1 | 1 | 0 | 0 | 142 |
| `sw_member_801160_N` | 1 | 1 | 0 | 0 | 49 |
| `sw_member_801170_Y` | 1 | 1 | 0 | 0 | 144 |
| `sw_member_801170_N` | 1 | 1 | 0 | 0 | 37 |
| `sw_member_801180_Y` | 1 | 1 | 0 | 0 | 115 |
| `sw_member_801180_N` | 1 | 1 | 0 | 0 | 129 |
| `sw_member_801200_Y` | 1 | 1 | 0 | 0 | 117 |
| `sw_member_801200_N` | 1 | 1 | 0 | 0 | 71 |
| `sw_member_801210_Y` | 1 | 1 | 0 | 0 | 95 |
| `sw_member_801210_N` | 1 | 1 | 0 | 0 | 50 |
| `sw_member_801230_Y` | 1 | 1 | 0 | 0 | 40 |
| `sw_member_801230_N` | 1 | 1 | 0 | 0 | 149 |
| `sw_member_801710_Y` | 1 | 1 | 0 | 0 | 80 |
| `sw_member_801710_N` | 1 | 1 | 0 | 0 | 56 |
| `sw_member_801720_Y` | 1 | 1 | 0 | 0 | 170 |
| `sw_member_801720_N` | 1 | 1 | 0 | 0 | 58 |
| `sw_member_801730_Y` | 1 | 1 | 0 | 0 | 434 |
| `sw_member_801730_N` | 1 | 1 | 0 | 0 | 60 |
| `sw_member_801740_Y` | 1 | 1 | 0 | 0 | 149 |
| `sw_member_801740_N` | 1 | 1 | 0 | 0 | 45 |
| `sw_member_801750_Y` | 1 | 1 | 0 | 0 | 381 |
| `sw_member_801750_N` | 1 | 1 | 0 | 0 | 47 |
| `sw_member_801760_Y` | 1 | 1 | 0 | 0 | 153 |
| `sw_member_801760_N` | 1 | 1 | 0 | 0 | 24 |
| `sw_member_801770_Y` | 1 | 1 | 0 | 0 | 143 |
| `sw_member_801770_N` | 1 | 1 | 0 | 0 | 15 |
| `sw_member_801780_Y` | 1 | 1 | 0 | 0 | 42 |
| `sw_member_801780_N` | 1 | 0 | 1 | 0 | 0 |
| `sw_member_801790_Y` | 1 | 1 | 0 | 0 | 91 |
| `sw_member_801790_N` | 1 | 1 | 0 | 0 | 8 |
| `sw_member_801880_Y` | 1 | 1 | 0 | 0 | 337 |
| `sw_member_801880_N` | 1 | 1 | 0 | 0 | 33 |
| `sw_member_801890_Y` | 1 | 1 | 0 | 0 | 633 |
| `sw_member_801890_N` | 1 | 1 | 0 | 0 | 229 |
| `sw_member_801950_Y` | 1 | 1 | 0 | 0 | 36 |
| `sw_member_801950_N` | 1 | 0 | 0 | 1 | 0 |
| `sw_member_801960_Y` | 1 | 1 | 0 | 0 | 54 |
| `sw_member_801960_N` | 1 | 1 | 0 | 0 | 4 |
| `sw_member_801970_Y` | 1 | 1 | 0 | 0 | 141 |
| `sw_member_801970_N` | 1 | 1 | 0 | 0 | 21 |
| `sw_member_801980_Y` | 1 | 1 | 0 | 0 | 32 |
| `sw_member_801980_N` | 1 | 1 | 0 | 0 | 5 |
| `index_classify_SW2021` | 1 | 1 | 0 | 0 | 511 |
| `index_classify_SW2014` | 1 | 1 | 0 | 0 | 359 |
| `daily` | 4 | 3 | 1 | 0 | 10265 |
| `adj_factor` | 4 | 3 | 0 | 1 | 10804 |
| `daily_basic` | 4 | 3 | 1 | 0 | 10256 |
| `stk_limit` | 4 | 4 | 0 | 0 | 16495 |
| `moneyflow` | 4 | 3 | 1 | 0 | 10257 |
| `suspend_d` | 4 | 4 | 0 | 0 | 531 |
| `cyq_perf` | 4 | 3 | 1 | 0 | 11664 |
| `limit_list_d` | 4 | 3 | 1 | 0 | 268 |
| `top_list` | 4 | 3 | 1 | 0 | 147 |
| `top_inst` | 4 | 3 | 1 | 0 | 1437 |
| `margin_detail` | 4 | 3 | 1 | 0 | 6284 |
| `block_trade` | 4 | 3 | 1 | 0 | 205 |
| `margin` | 4 | 3 | 0 | 1 | 7 |
| `fut_daily_cffex` | 4 | 3 | 1 | 0 | 96 |
| `sw_daily` | 4 | 3 | 1 | 0 | 1619 |
| `index_dailybasic` | 4 | 3 | 1 | 0 | 41 |
| `dividend` | 4 | 2 | 2 | 0 | 27 |
| `repurchase` | 4 | 3 | 1 | 0 | 59 |
| `share_float` | 4 | 3 | 1 | 0 | 31604 |
| `stk_holdertrade` | 4 | 3 | 1 | 0 | 119 |
| `stk_holdernumber` | 4 | 2 | 2 | 0 | 227 |
| `report_rc` | 4 | 3 | 1 | 0 | 455 |
| `income_vip_rt1` | 4 | 4 | 0 | 0 | 26046 |
| `income_vip_rt4` | 4 | 2 | 2 | 0 | 10067 |
| `income_vip_rt5` | 4 | 2 | 2 | 0 | 130 |
| `balancesheet_vip_rt1` | 4 | 3 | 0 | 1 | 27109 |
| `balancesheet_vip_rt4` | 4 | 0 | 3 | 1 | 0 |
| `balancesheet_vip_rt5` | 4 | 2 | 2 | 0 | 251 |
| `cashflow_vip_rt1` | 4 | 4 | 0 | 0 | 18405 |
| `cashflow_vip_rt4` | 4 | 2 | 2 | 0 | 5253 |
| `cashflow_vip_rt5` | 4 | 3 | 1 | 0 | 53 |
| `forecast_vip` | 4 | 4 | 0 | 0 | 5144 |
| `express_vip` | 4 | 4 | 0 | 0 | 1358 |
| `disclosure_date` | 4 | 4 | 0 | 0 | 16134 |
| `index_daily_000001_SH` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_000300_SH` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_000905_SH` | 4 | 3 | 0 | 1 | 695 |
| `index_daily_000852_SH` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_932000_CSI` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_399001_SZ` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_399006_SZ` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_000688_SH` | 4 | 2 | 2 | 0 | 450 |
| `index_daily_899050_BJ` | 4 | 2 | 2 | 0 | 450 |
| `index_daily_000016_SH` | 4 | 4 | 0 | 0 | 939 |
| `index_daily_399303_SZ` | 4 | 4 | 0 | 0 | 939 |
| `shibor` | 4 | 4 | 0 | 0 | 960 |
| `namechange` | 4 | 4 | 0 | 0 | 3790 |
| `ths_daily` | 4 | 2 | 1 | 1 | 2071 |
| `hk_hold` | 4 | 3 | 1 | 0 | 4050 |
| `stk_surv` | 4 | 3 | 1 | 0 | 910 |
| `hm_detail` | 4 | 3 | 1 | 0 | 510 |
| `stock_st` | 4 | 4 | 0 | 0 | 500 |
| `opt_daily_sse` | 4 | 3 | 1 | 0 | 796 |
