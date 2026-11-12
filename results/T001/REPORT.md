# T001 环境搭建与环境报告

- 执行者：执行代理
- 执行时间（UTC）：2026-11-12T03:43:58Z 起
- 仓库提交基线：`76482af9d93e9226ef2ba20f88bb6df40b6707af`

## 1. 状态

**完成。**

验收标准逐项核对：

| 验收项 | 要求 | 实际 | 结论 |
|---|---|---|---|
| `verify_integrity` | 通过 | 通过，19 个受保护文件一致 | 满足 |
| `pytest -q` | 全部通过（预期 12 个） | 12 passed | 满足 |
| `env.json` 的 `python_ok` | true | true | 满足 |
| `env.json` 的 `duckdb_parquet_selfcheck.ok` | true | true | 满足 |

附注：`python_ok` 与存储自检均满足，但 `import tushare` 在本环境失败（详见第 4、6 节），不影响 T001 验收，会影响 T002。

## 2. 执行的命令

按执行顺序，原样记录：

```bash
# 前置
git clone https://github.com/wst-2006/PER-.git PER          # 克隆到 D:\PER，成功
git pull --ff-only                                          # 失败，见第 4 节
python tools/verify_integrity.py                            # 用系统 PATH 上的 python 执行，通过

# T001 步骤 1：虚拟环境与依赖
"C:/Users/Xiansheng Wu/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt

# T001 步骤 2：.env（由 .env.example 复制，密钥项留空由用户填写）

# T001 步骤 3：校验与报告
.venv/Scripts/python.exe tools/verify_integrity.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/env_report.py

# 提交前复核
.venv/Scripts/python.exe tools/verify_integrity.py
.venv/Scripts/python.exe -m pytest -q
```

说明：未执行 `activate`，改为直接调用 `.venv/Scripts/python.exe`，效果等价且不依赖 shell 状态。

## 3. 结果摘要

来源：`results/T001/env.json`、`results/T001/run_meta.json`。

| 项目 | 值 |
|---|---|
| Python | 3.11.2 (MSC v.1934 64 bit) |
| 平台 | Windows-10-10.0.26300-SP0 |
| CPU | 16 物理核 / 32 逻辑核 |
| 内存 | 总量 15.21 GB，运行时空闲 3.37–3.48 GB（已用 77.1%） |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU，8188 MiB，驱动 610.62 |
| 仓库盘 D: | 总 26.0 GB，剩 6.0 GB |
| 数据盘 E: | 总 931.5 GB，剩 444.8 GB |
| DuckDB + Parquet 自检 | ok，读回结果 `20240102→6.0`、`20240103→9.0` |

依赖实际版本（`pip install -r requirements.txt` 解析结果）：

| 包 | 版本 | 包 | 版本 |
|---|---|---|---|
| tushare | 1.4.29 | pandas | 3.1.1 |
| numpy | 2.4.6 | pyarrow | 26.0.1 |
| duckdb | 2.0.0 | psutil | 7.2.2 |
| psycopg2 | 2.9.13 | pytest | 9.2.0 |

`env_keys_present`：`PERQ_DATA_DIR` = true，`LEGACY_REPORTS_DIR` = true，`TUSHARE_TOKEN` = false，`LEGACY_DB_NAME` = false（后两项待用户填写）。

## 4. 与预期不符之处

### 4.1 `git pull` 失败（网络）

```
fatal: unable to access 'https://github.com/wst-2006/PER-.git/': CONNECT tunnel failed, response 502
```

另一次变体：

```
fatal: unable to access 'https://github.com/wst-2006/PER-.git/': schannel: server closed abruptly (missing close_notify)
```

该机器到 github.com 的连接间歇性中断（同一时刻 `api.github.com` 正常返回 200）。改用 GitHub API 核对远端状态：

```
local : 76482af9d93e9226ef2ba20f88bb6df40b6707af
remote: 76482af9d93e9226ef2ba20f88bb6df40b6707af
```

远端 `main` 与本地 HEAD 一致，无遗漏提交。工作区基于 `76482af` 执行。

### 4.2 `import tushare` 失败

`env_report.py` 的依赖版本采集对 tushare 记录为 `null`：

```
"tushare": null,
"tushare__error": "ModuleNotFoundError: No module named 'six'"
```

单独复现的完整报错：

```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "D:\PER\.venv\Lib\site-packages\tushare\__init__.py", line 108, in <module>
    from tushare.trader.trader import TraderAPI
  File "D:\PER\.venv\Lib\site-packages\tushare\trader\trader.py", line 10, in <module>
    import six
ModuleNotFoundError: No module named 'six'
```

`pip show tushare` 的声明依赖：

```
Requires: bs4, lxml, pandas, requests, simplejson, tqdm, websocket-client
```

即 tushare 1.4.29 在 `tushare/trader/trader.py` 直接 `import six`，却没有把 `six` 写进自己的依赖声明。`six` 不是标准库，故 `import tushare` 必然失败。**T002 与 T004 的 `c.get_pro()` 会在此处崩溃**。未自行安装 `six`，等设计方指示（见第 6 节）。

### 4.3 Python 版本与设计方测试版本不同

任务单要求 3.11 及以上，实际用 **3.11.2**（`C:\Users\Xiansheng Wu\AppData\Local\Programs\Python\Python311\python.exe`），而 `requirements.txt` 注释写"设计方在 3.13 上测试"。

选择理由：该机 PATH 上的 `python` 指向 WorkBuddy 自带的便携版 3.13.12，用它建 venv 有额外不确定性；系统里另有一个正式安装的 CPython 3.11.2。两者都满足 `>=3.11`，选了正式安装的那个。若设计方要求严格对齐 3.13，请告知，我重建 venv（不涉及受保护文件）。

### 4.4 依赖解析出的大版本

`requirements.txt` 对 pandas、duckdb、pytest 只有下限，实际解析到 **pandas 3.1.1 / duckdb 2.0.0 / pytest 9.2.0**。当前 12 个测试全过，但 `tests/` 只覆盖离线逻辑（用 `tests/fake_tushare.py`），没有覆盖真实 Tushare 返回结构，因此 pandas 3 的兼容性尚未被验证。

### 4.5 内存空闲偏低

运行期间系统已用内存 77.1%，空闲 3.37–3.48 GB，与 `AGENTS.md` 第五节"日常占用很高"一致。T002/T004 是逐接口小请求，不构成压力；后续按日/按股分块的设计仍然必要。

### 4.6 数据目录无法在普通账户下创建

`E:\` 根目录 ACL 为 `Everyone: ReadAndExecute`，仅 `SYSTEM`、`BUILTIN\Administrators` 有完全控制。普通权限下 `mkdir E:\perq_data` 返回 `Permission denied`。最终由用户以管理员身份创建目录并授权，之后写入测试通过。

## 5. 我做了哪些任务单没有要求的事

1. **在 `.env` 里预填了 `LEGACY_REPORTS_DIR`**（任务单写"由用户填写"）。依据是旧项目 `C:\Users\Xiansheng Wu\Desktop\量化\reports` 真实存在，且是 T003 要盘点的目录。值写错请直接改 `.env`，我不再动。
2. **为定位 `import tushare` 失败，单独执行了 `import tushare` 与 `pip show tushare`**，只读取，未安装任何包。
3. **为验证 C 盘可写性，建过一个空的 `C:\perq_data` 目录**，测试后已 `rmdir` 删除，未留下任何文件。
4. **因 `git pull` 失败，改用 `gh api` 比对远端与本地提交号**，只读。
5. 未修改任何受保护文件；未安装 `requirements.txt` 之外的任何包；未读写旧项目的任何文件内容（只列过目录名）；未接触旧数据库。

## 6. 问题与建议

### 6.1 阻塞项：tushare 缺 `six`（建议设计方修 `requirements.txt`）

`import tushare` 在 3.11 下必然失败，T002/T004 无法启动。建议在 `requirements.txt` 增加：

```
six>=1.16
```

我没有自行安装，理由：`AGENTS.md` 第二节第 6 条"不擅自引入新依赖"。是否安装、以及是否同时把 tushare 固定到某个版本，请设计方决定。

### 6.2 建议给 pandas 加上限

`pandas>=2.2` 在 Python 3.11 上解析到 3.1.1。目前测试通过，但 tushare 1.4.29 与 pandas 3 的组合尚未在真实接口上验证过（见 4.2，连导入都没过）。建议设计方明确基线版本，例如 `pandas>=2.2,<3`，以免后续出现"设计方 2.x 通过、执行方 3.x 报错"的分叉。

### 6.3 环境事实：数据目录需管理员创建

`E:` 是固定盘但根 ACL 只给 `Everyone` 读执行权限。后续若要在新盘上部署，需要一次性提权建目录并授权，普通权限跑脚本无法自建。已解决，仅作记录。

### 6.4 环境事实：仓库盘空间小

`D:` 总 26 GB、剩 6.0 GB，仓库和数据目录分开放在 `D:` 与 `E:` 是正确的。但 `samples/` 默认落在仓库内（`D:\PER\samples`），T004 的压缩包限 50 MB，`D:` 目前空间够用；若后续中间产物也想放仓库盘，需先换盘。

### 6.5 建议：Python 版本建议写死在文档里

`requirements.txt` 只写"3.11 及以上"，注释里说在 3.13 上测试。执行侧用了 3.11.2。若某些库行为随小版本变化，建议设计方指定一个基线版本号，避免每次都要解释。

## 7. 提交前校验原文

`python tools/verify_integrity.py`：

```
通过：19 个受保护文件与清单一致。
```

`python -m pytest -q`：

```
............                                                             [100%]
12 passed in 11.24s
```
