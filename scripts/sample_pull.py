"""T004：小样本导出（由设计方提供，受保护）。

按固定规则挑选约 44 只股票（覆盖各板块、已退市、ST），从 Tushare 拉取 2018 年以来的各表数据，
外加 5 个完整交易日的全市场截面，写成 Parquet 并打成一个压缩包。

压缩包**不提交到仓库**（仓库公开，数据不得再分发），由用户直接发给设计方。
仓库里只提交 results/T004/sample_manifest.json（行数与哈希）。

用法：
    python scripts/sample_pull.py
输出：
    samples/perq_sample.zip            （不入库）
    results/T004/sample_manifest.json
    results/T004/run_meta.json
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

RESULT_DIR = c.REPO_ROOT / "results" / "T004"
SAMPLE_DIR = c.REPO_ROOT / "samples"
START_DATE = "20180101"
PER_GROUP = 8
CROSS_SECTION_DATES = ["20180209", "20200203", "20240205", "20240930"]  # 另加最近一个交易日
PER_STOCK_APIS = ["daily", "adj_factor", "daily_basic", "stk_limit", "moneyflow"]
PER_STOCK_NO_RANGE_APIS = ["namechange", "income", "balancesheet", "cashflow", "fina_indicator",
                           "forecast", "express", "dividend", "stk_holdernumber"]
BASIC_FIELDS = "ts_code,name,area,industry,market,exchange,list_status,list_date,delist_date"


def pick_evenly(codes: list[str], count: int) -> list[str]:
    """从排好序的代码中等间隔取 count 个，结果完全确定。"""
    codes = sorted(codes)
    if len(codes) <= count:
        return codes
    step = len(codes) / count
    return [codes[int(i * step)] for i in range(count)]


def choose_codes(listed: Any, delisted: Any) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    code = listed["ts_code"].astype(str)
    name = listed["name"].astype(str)
    is_st = name.str.contains("ST", na=False)
    normal = listed[~is_st]
    ncode = normal["ts_code"].astype(str)
    groups["main_sh"] = pick_evenly(ncode[ncode.str.startswith("60")].tolist(), PER_GROUP)
    groups["main_sz"] = pick_evenly(ncode[ncode.str.startswith("00")].tolist(), PER_GROUP)
    groups["chinext"] = pick_evenly(ncode[ncode.str.startswith("30")].tolist(), PER_GROUP)
    groups["star"] = pick_evenly(ncode[ncode.str.startswith("68")].tolist(), PER_GROUP)
    groups["bse"] = pick_evenly(ncode[ncode.str.endswith(".BJ")].tolist(), 4)
    groups["st"] = pick_evenly(code[is_st].tolist(), 4)
    if delisted is not None and len(delisted):
        dated = delisted[delisted["delist_date"].notna()]
        recent = dated[dated["delist_date"].astype(str) >= "20190101"]
        groups["delisted"] = pick_evenly(recent["ts_code"].astype(str).tolist(), 4)
    else:
        groups["delisted"] = []
    return groups


def save(df: Any, name: str, manifest: list[dict[str, Any]]) -> None:
    path = SAMPLE_DIR / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    manifest.append({
        "file": path.name, "rows": int(len(df)), "columns": list(df.columns),
        "date_ranges": c.date_range_of(df),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })


def run(pro: Any, today: str | None = None, sleep=None) -> dict[str, Any]:
    import pandas as pd

    today = today or datetime.now().strftime("%Y%m%d")
    kw = {"sleep": sleep} if sleep is not None else {}
    manifest: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def fetch(label: str, api: str, **params: Any) -> Any:
        res = c.call(pro, api, **kw, **params)
        if not res["ok"]:
            failures.append({"label": label, "api": api, "params": params, "error": res["error"]})
            return None
        return res["df"]

    # 1. 全市场基础表
    basics = {status: fetch(f"stock_basic_{status}", "stock_basic", exchange="", list_status=status, fields=BASIC_FIELDS)
              for status in ("L", "D", "P")}
    if basics["L"] is None or len(basics["L"]) == 0:
        return {"status": "failed", "stage": "stock_basic", "failures": failures, "files": []}
    all_basic = pd.concat([df for df in basics.values() if df is not None and len(df)], ignore_index=True)
    save(all_basic, "stock_basic_all", manifest)

    cal = fetch("trade_cal", "trade_cal", exchange="SSE", start_date="20140101", end_date=today)
    if cal is None or len(cal) == 0:
        return {"status": "failed", "stage": "trade_cal", "failures": failures, "files": manifest}
    save(cal, "trade_cal", manifest)
    open_days = sorted(cal.loc[cal["is_open"].astype(str) == "1", "cal_date"].astype(str).tolist())
    recent = open_days[-2]

    # 2. 样本股票
    groups = choose_codes(basics["L"], basics["D"])
    codes = sorted({code for members in groups.values() for code in members})
    print(f"样本股票 {len(codes)} 只", flush=True)

    # 3. 逐只股票的时间序列（单只股票 2018 年以来约两千行，一次调用即可）
    for api in PER_STOCK_APIS:
        frames = []
        for code in codes:
            df = fetch(f"{api}:{code}", api, ts_code=code, start_date=START_DATE, end_date=today)
            if df is not None and len(df):
                frames.append(df)
        if frames:
            save(pd.concat(frames, ignore_index=True), f"stock_{api}", manifest)
        print(f"完成 {api}", flush=True)

    for api in PER_STOCK_NO_RANGE_APIS:
        frames = []
        for code in codes:
            df = fetch(f"{api}:{code}", api, ts_code=code)
            if df is not None and len(df):
                frames.append(df)
        if frames:
            save(pd.concat(frames, ignore_index=True), f"stock_{api}", manifest)
        print(f"完成 {api}", flush=True)

    frames = []
    for code in codes:
        df = fetch(f"suspend_d:{code}", "suspend_d", ts_code=code, start_date=START_DATE, end_date=today)
        if df is not None and len(df):
            frames.append(df)
    if frames:
        save(pd.concat(frames, ignore_index=True), "stock_suspend_d", manifest)

    # 4. 全市场截面（检验每日行数、涨跌停与停牌的全貌）
    for trade_date in CROSS_SECTION_DATES + [recent]:
        for api in ("daily", "stk_limit", "daily_basic", "adj_factor", "suspend_d"):
            df = fetch(f"xs:{api}:{trade_date}", api, trade_date=trade_date)
            if df is not None and len(df):
                save(df, f"xs_{api}_{trade_date}", manifest)
    print("完成全市场截面", flush=True)

    # 5. 行业与指数
    classify = fetch("index_classify", "index_classify", src="SW2021")
    if classify is not None and len(classify):
        save(classify, "sw_classify", manifest)
    frames = []
    for code in codes:
        df = fetch(f"index_member_all:{code}", "index_member_all", ts_code=code)
        if df is not None and len(df):
            frames.append(df)
    if frames:
        save(pd.concat(frames, ignore_index=True), "stock_sw_member", manifest)
    frames = []
    for index_code in ("000001.SH", "000300.SH", "000905.SH", "000852.SH", "932000.CSI"):
        df = fetch(f"index_daily:{index_code}", "index_daily", ts_code=index_code, start_date="20140101", end_date=today)
        if df is not None and len(df):
            frames.append(df)
    if frames:
        save(pd.concat(frames, ignore_index=True), "index_daily", manifest)

    # 6. 打包
    zip_path = SAMPLE_DIR / "perq_sample.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in manifest:
            archive.write(SAMPLE_DIR / item["file"], item["file"])
    return {
        "status": "completed" if not failures else "completed_with_failures",
        "today": today, "recent_trade_date": recent,
        "groups": groups, "stock_count": len(codes),
        "zip_file": "samples/perq_sample.zip",
        "zip_mb": round(zip_path.stat().st_size / 2**20, 2),
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "failure_count": len(failures),
        "failures": failures[:200],
        "files": manifest,
    }


def main() -> int:
    started = c.utc_now()
    pro = c.get_pro()
    report = run(pro)
    c.write_json(RESULT_DIR / "sample_manifest.json", report)
    c.write_run_meta(RESULT_DIR, started, {"task": "T004"})
    print(f"\n状态：{report['status']}；失败调用 {report.get('failure_count', 0)} 次")
    if report.get("zip_file"):
        print(f"样本压缩包：{report['zip_file']}（{report['zip_mb']} MB）。不要提交它，请交给用户转发。")
    return 0 if report["status"].startswith("completed") else 1


if __name__ == "__main__":
    sys.exit(main())
