# T002 Tushare 权限与历史深度探测

- 执行者：执行代理
- 执行时间（UTC）：2026-11-12T03:57:20Z — 03:58:43Z，耗时 82.7 秒
- 仓库提交基线：`4a78c3cadfc4fb3d65b9b8b6bc60a70f61e2b5db`（T001 之后）

## 1. 状态

**完成。** 脚本正常结束，`probe.json` 的 `status` 为 `completed`，交易日历成功取得，共 129 次探测。

探测口径：`today=20261112`，最近已收盘交易日 `20261111`，前一交易日 `20261110`。tushare 1.4.29。

| 结果类别 | 数量 | 说明 |
|---|---|---|
| 有数据 | 116 | 调用成功且返回行数 > 0 |
| 空表 | 4 | 调用成功但 0 行 |
| 失败 | 9 | 8 个无接口权限 + 1 个被限流 |

按任务单的 `need` 分桶：

| 桶 | 可用接口数 | 不可用 |
|---|---|---|
| core | 19 / 19 | 无 |
| useful | 31 / 31 | 无 |
| optional | 28 / 33 | fund_portfolio（空表）、news、stk_auction、stk_auction_o、yc_cb（无权限） |
| uncertain | 1 / 5 | anns_d、irm_qa_sh、irm_qa_sz、research_report（无权限） |

**core 与 useful 两类接口全部可用**，这是本次最关键的一条。

## 2. 执行的命令

```bash
git pull --ff-only                                   # 成功，Already up to date
python tools/verify_integrity.py                     # 通过
.venv/Scripts/python.exe -m pip install "six>=1.16"  # 经用户批准后安装，成功（six 1.17.0）
.venv/Scripts/python.exe -c "import tushare, six"    # 导入成功，tushare 1.4.29
.venv/Scripts/python.exe scripts/tushare_probe.py    # 本次任务主体
git config user.name / user.email                    # 仓库级提交身份，见第 5 节
```

## 3. 结果摘要

### 3.1 历史深度

| 探测项 | 结果 |
|---|---|
| 交易日历（SSE，20140101 起） | 4698 个交易日，`cal_date` 20140101–20261111 |
| `daily` 单股 000001.SZ（请求 1990–2005） | 3541 行，最早 **19910403**，最晚 20051230 |
| `daily_basic` 单股（请求 1990–2010） | 5613 行 |
| `moneyflow` 单股（请求 2005–2014） | 1140 行 |
| `stk_limit` 单股（请求 2005–2014） | 1944 行 |
| `income` 单股 000001.SZ | 178 行，`end_date` 19941231–20260930 |
| 全市场日线（20140102 / 20160104 / 20190102） | 2354 / 2592 / 3579 行 |
| 全市场日线（20261111） | 5574 行 |

日线可回溯到 1991 年，远早于 2014 年的请求下界。

### 3.2 财务数据的公告日与修订版本

| 接口 | 行数 | `f_ann_date` 非空率 | `update_flag` 分布 | 同一 (ts_code, end_date) 最大行数 |
|---|---|---|---|---|
| `income_vip` period=20141231 | 5030 | 1.0 | {0: 4376, 1: 654} | 3 |
| `income_vip` period=20251231 | 7236 | 1.0 | {1: 5883, 0: 1353} | 4 |
| `balancesheet_vip` 20141231 | 7000 | 1.0 | {0: 3791, 1: 3209} | 2 |
| `balancesheet_vip` 20251231 | 6616 | 1.0 | {0: 6070, 1: 546} | 4 |
| `fina_indicator_vip` 20141231 | 9788 | 无此列 | {1: 5149, 0: 4639} | 2 |
| `fina_indicator_vip` 20251231 | 6848 | 无此列 | {1: 5631, 0: 1217} | 2 |
| `income` 单股 | 178 | 1.0 | {0: 98, 1: 80} | 2 |

结论：**能拿到实际公告日（`f_ann_date` 非空率 100%），也能拿到修订版本**（同一报告期最多 4 行，`update_flag` 区分 0/1）。`fina_indicator_vip` 与单股 `fina_indicator` 没有 `f_ann_date` 列，只有 `ann_date`。

一个能说明修订能力的具体例子：`income_vip` period=20141231 的 `ann_date` 最晚到 20180425，但 `f_ann_date` 最晚到 **20260314** —— 说明 2014 年年报在 2026 年仍有修订记录，且修订时间与原始公告时间是可分开的。

### 3.3 其他关键可用性

- 账号：**10121 积分，未购买任何单独权限包**（用户提供，见第 7 节）。
- 停牌：`suspend_d trade_date=20150708` 返回 1348 行（SZ 984 / SH 364），2015 年大面积停牌日的历史完整。
- 指数：8 个指数 `index_daily` 全部可用，其中 000688.SH 1662 行、899050.BJ 1099 行（起始晚于 2014）；其余 6 个均 3125 行（2014 年以来全序列）。
- 分钟线：`stk_mins` 单股单日 1min 返回 241 行（240 分钟 + 1），**有权限**，但受 1 次/分钟限额约束（见 4.1）。
- 板块与资金：`ths_index` 395、`ths_member` 519、`dc_index` 1058、`moneyflow_ths` 5218、`moneyflow_dc` 6032 均可用。
- 分析师与机构：`report_rc`、`broker_recommend`、`stk_surv`、`top10_holders`、`top10_floatholders` 均可用。
- 涨停生态：`limit_list_d`、`limit_step`、`limit_cpt_list`、`kpl_list`、`kpl_concept`、`hm_list`、`hm_detail` 均可用。
- 宏观：`shibor`、`cn_pmi`、`cn_m`、`sf_month`、`fut_daily`、`opt_daily`、`margin` 均可用。
- pandas 兼容性：在 pandas 3.1.1 下 116 次调用成功，T001 报告中担心的 pandas 3 不兼容**未出现**。

## 4. 与预期不符之处

### 4.1 被 Tushare 限流 1 次，但脚本没有识别出来

`stk_mins` 的第二次调用（2019-01-02）失败，原文：

```
Exception: 抱歉，您访问接口(stk_mins)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。
```

`_common.RATE_LIMIT_MARKERS` 目前是 `("每分钟最多访问", "每小时最多访问", "每天最多访问", "访问频率")`，而 Tushare 实际返回的是「频率超限」。两者不匹配，因此：

- 这一次没有被当作限流重试，直接记为失败；
- `probe.json` 里所有 129 条记录的 `rate_limited_times` 全部为 **0**，限流事实只在 `error` 文本里留下痕迹。

如果只看 `rate_limited_times`，会得出"一次都没被限流"的错误结论。实际是 1 次。

### 4.2 8 个接口无权限（原文相同，仅接口名不同）

```
anns_d          Exception: 抱歉，您没有接口(anns_d)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。
stk_auction_o   Exception: 抱歉，您没有接口(stk_auction_o)访问权限，...
stk_auction     Exception: 抱歉，您没有接口(stk_auction)访问权限，...
yc_cb           Exception: 抱歉，您没有接口(yc_cb)访问权限，...
news            Exception: 抱歉，您没有接口(news)访问权限，...
irm_qa_sz       Exception: 抱歉，您没有接口(irm_qa_sz)访问权限，...
irm_qa_sh       Exception: 抱歉，您没有接口(irm_qa_sh)访问权限，...
research_report Exception: 抱歉，您没有接口(research_report)访问权限，...
```

全部是**权限类**报错，没有出现"接口名不存在"或"参数错误"。因此按任务单要求不需要查文档纠正写法——接口名本身是有效的，只是账号没有权限。

影响面：蓝图里的**集合竞价（微观结构）**、**公告全文（anns_d）**、**新闻与研报文本**、**投资者问答**、**国债收益率曲线**这几个族群，当前账号全部拿不到。

### 4.3 4 张空表

| 接口 | 参数 | 判断 |
|---|---|---|
| `stock_basic` | `list_status=P` | 暂停上市股票当前为 0 只。合理，不代表接口有问题 |
| `limit_list_d` | `trade_date=20190102` | 该接口**历史深度不足**，2026 年有 104 行、2019 年 0 行 |
| `index_weight` | `000852.SH`，20261101–20261111 | 当月 0 行，但 2016-01 有 1000 行 → 指数成分权重**按月发布且有滞后**，当月取不到 |
| `fund_portfolio` | `ts_code=000001.SZ` | **参数用错**：这是基金持仓接口，`ts_code` 应为基金代码，传股票代码必然空表 |

### 4.4 耗时远低于预期

任务单预计 5–10 分钟，实际 **82.7 秒**。原因是 `_common.call` 的最小间隔 0.35 秒 × 129 次 ≈ 45 秒，加上网络往返，且只有 1 次限流（见 4.1，且未被自动等待）。

### 4.5 运行期内存

`run_meta.json`：可用内存 2.39 GB（总量 15.21 GB）。单次调用返回行数最大 10000（`fina_mainbz_vip`），未构成长时间驻留，但余量确实不宽裕。

## 5. 我做了哪些任务单没有要求的事

1. **安装了 `six` 1.17.0**。经用户明确批准后才装。`requirements.txt` 未改（受保护）。若不装，`import tushare` 直接失败，T002 无法启动（详见 T001 报告 4.2）。
2. **设置了仓库级 git 身份** `PER Executor <executor@per.local>`。原因：这台机器没有 `~/.gitconfig`，不设就无法提交。仅为仓库级配置，未改全局。若设计方要求使用其他提交身份，请告知。
3. 为撰写报告，用 python 读取 `probe.json` 做了汇总（只读，未改文件）。
4. 未修改任何受保护文件，未安装 `requirements.txt` 与 `six` 之外的包。

## 6. 问题与建议

### 6.1 `RATE_LIMIT_MARKERS` 需要补词（建议优先修）

当前标记词匹配不到 Tushare 的真实限流文案「频率超限(N次/分钟)」。建议改为包含 `"频率超限"`，或直接匹配 `"访问接口"` 与 `"超限"` 的组合。否则：

- 限流会被静默记成"失败"，掩盖真实的权限/参数问题；
- 不会触发 61 秒等待，长任务里可能连续撞墙。

这是本次唯一影响"结果正确性"的脚本问题，其余失败都是账号权限。

### 6.2 建议把 `six` 写进 `requirements.txt`

T001 里已提。`tushare 1.4.29` 自带 `import six` 但未声明依赖，任何人按 `requirements.txt` 重建 venv 都会踩同一个坑。

### 6.3 `fund_portfolio` 探测参数应改为基金代码

建议改为 `ts_code=510300.SH` 一类。当前空表不能说明该接口无权限，只说明参数类型不对。

### 6.4 `index_weight` 建议改取上一完整月份

当月为空是发布滞后，不是权限问题。建议探测口径改为「上一个已结束月份」，否则每次都会得到空表。

### 6.5 `limit_list_d` 历史深度需在设计里标定

2019 年无数据、2026 年有数据，具体起点未知。若蓝图依赖涨停生态做长期回测，需要单独探测起点年份，不能假定覆盖 2014 年以来。

### 6.6 财务修订版本足够，但 `fina_indicator` 缺 `f_ann_date`

`income`/`balancesheet`/`cashflow` 有 `f_ann_date` 且非空率 100%，构造"当时可见"的财务标签时可以直接用实际公告日；但 `fina_indicator`（含 vip）只有 `ann_date`。若标签要用财务指标，需确认 `ann_date` 在这张表里是否等同于实际公告日，否则会有前视偏差风险。

### 6.7 无权限接口对应的研究方向需要取舍

`stk_auction`/`stk_auction_o`（集合竞价）、`anns_d`（公告全文）、`news`/`research_report`/`irm_qa_*`（文本）目前全无权限。蓝图第 3 节 L1 信息源与第 12 节文本情绪，若要在本账号上落地，需要要么付费开权限，要么调整设计。

### 6.8 分钟线现实约束

`stk_mins` 限额 1 次/分钟。即使有权限，也意味着单次最多取一只股票一天。全市场分钟级研究在本账号上不现实，建议在设计里明确排除或改用其他数据源。

## 7. 任务单要求额外回答的两个问题

1. **Tushare 账号积分与单独购买的权限**：账号页面显示 **10121 积分**，**没有单独购买任何权限包**（用户于 2026-11-12 提供，非我从接口读取；我尝试过 `pro.query('user')`，返回「请指定正确的接口名」，说明不存在可用于自查积分的公开接口）。
   与探测结果对照：8 个无权限接口（`anns_d`、`stk_auction`、`stk_auction_o`、`yc_cb`、`news`、`irm_qa_sz`、`irm_qa_sh`、`research_report`）与"未购买单独权限包"这一事实一致。我不据此推断积分档位对应的具体权限边界，那需要设计方按 Tushare 官方积分说明判定。
2. **运行过程中被限流了几次**：**1 次**（`stk_mins` 的 2019 年那次调用被拒，文案见 4.1）。注意 `probe.json` 中 `rate_limited_times` 全为 0，是脚本未识别所致，不代表没被限流。

## 8. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：19 个受保护文件与清单一致。
```

`python -m pytest -q`：

```
............                                                             [100%]
12 passed in 10.35s
```
