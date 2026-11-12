# T001 环境搭建与环境报告

**目的**：建立干净的运行环境，并让设计方知道这台机器的真实条件。

**前置**：无。

## 步骤

1. 在仓库根目录创建虚拟环境并安装依赖：
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
   Python 需要 3.11 或更高。
2. 把 `.env.example` 复制为 `.env`，由用户填写。你不要把其中任何取值写到别处。
   - `PERQ_DATA_DIR` 指向一个空间充足的新目录（建议剩余空间 100 GB 以上的盘），不要放在仓库里，也不要放进旧项目目录。
3. 运行：
   ```powershell
   python tools/verify_integrity.py
   python -m pytest -q
   python scripts/env_report.py
   ```

## 提交

- `results/T001/env.json`、`results/T001/run_meta.json`、`results/T001/REPORT.md`
- 提交信息：`T001: 环境报告`

## 验收标准

- `verify_integrity` 通过；pytest 全部通过。
- `env.json` 中 `python_ok` 为 true，`duckdb_parquet_selfcheck.ok` 为 true。
- 任何一项不满足：不要自行修改受保护文件去"修好"，把原始输出贴进报告。
