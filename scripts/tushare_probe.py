"""T002：Tushare 权限与历史深度探测（由设计方提供，受保护）。

逐个接口实测：能否调用、返回多少行、有哪些字段、日期范围。
只记录元数据，不保存任何逐行数据。失败是有效信息，照实记录即可。

用法：
    python scripts/tushare_probe.py
输出：
    results/T002/probe.json
    results/T002/run_meta.json
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

RESULT_DIR = c.REPO_ROOT / "results" / "T002"

# 历史深度检查用的固定交易日
OLD_DATES = {"2014": "20140102", "2016": "20160104", "2019": "20190102"}
CRASH_DATE = "20150708"  # 2015 年大面积停牌日，用来检验停牌数据的历史完整性
SAMPLE_STOCK = "000001.SZ"
ST_HISTORY_STOCK = "000004.SZ"
INDEXES = ["000001.SH", "000300.SH", "000905.SH", "000852.SH", "932000.CSI", "399006.SZ", "000688.SH", "899050.BJ"]


def resolve_recent_dates(pro: Any, today: str) -> dict[str, Any]:
    """找出最近两个已收盘的交易日。失败时返回带错误信息的字典。"""
    start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
    res = c.call(pro, "trade_cal", exchange="SSE", start_date=start, end_date=today, is_open="1")
    if not res["ok"] or res["df"] is None or len(res["df"]) < 3:
        return {"ok": False, "error": res["error"] or "trade_cal 返回行数不足"}
    days = sorted(res["df"]["cal_date"].astype(str).tolist())
    # 当天数据可能尚未入库，所以取倒数第二、第三个交易日
    return {"ok": True, "recent": days[-2], "previous": days[-3]}


def build_probes(recent: str, previous: str) -> list[dict[str, Any]]:
    """probe = {group, api, params, need}；need: core 必需 / useful 有用 / optional 可选 / uncertain 接口名不确定"""
    month_start = recent[:6] + "01"
    probes: list[dict[str, Any]] = []

    def add(group: str, api: str, need: str, **params: Any) -> None:
        probes.append({"group": group, "api": api, "need": need, "params": params})

    # 基础
    basic_fields = "ts_code,name,area,industry,market,exchange,list_status,list_date,delist_date"
    for status in ("L", "D", "P"):
        add("基础", "stock_basic", "core", exchange="", list_status=status, fields=basic_fields)
    add("基础", "trade_cal", "core", exchange="SSE", start_date="20140101", end_date=recent)
    add("基础", "namechange", "core", ts_code=ST_HISTORY_STOCK)
    add("基础", "suspend_d", "core", trade_date=recent)
    add("基础", "suspend_d", "core", trade_date=CRASH_DATE)
    add("基础", "stock_st", "uncertain", trade_date=recent)
    add("基础", "bak_basic", "optional", trade_date=recent)

    # 行情：最近日 + 三个历史日
    for api, need in (("daily", "core"), ("adj_factor", "core"), ("daily_basic", "core"),
                      ("stk_limit", "core"), ("moneyflow", "useful")):
        add("行情", api, need, trade_date=recent)
        for old in OLD_DATES.values():
            add("行情", api, need, trade_date=old)

    # 分钟线（通常需要单独权限）
    add("分钟", "stk_mins", "optional", ts_code=SAMPLE_STOCK, freq="1min",
        start_date=f"{recent[:4]}-{recent[4:6]}-{recent[6:]} 09:00:00",
        end_date=f"{recent[:4]}-{recent[4:6]}-{recent[6:]} 15:30:00")
    add("分钟", "stk_mins", "optional", ts_code=SAMPLE_STOCK, freq="1min",
        start_date="2019-01-02 09:00:00", end_date="2019-01-02 15:30:00")

    # 交易行为
    add("交易行为", "limit_list_d", "useful", trade_date=recent)
    add("交易行为", "limit_list_d", "useful", trade_date=OLD_DATES["2019"])
    add("交易行为", "top_list", "useful", trade_date=previous)
    add("交易行为", "top_inst", "useful", trade_date=previous)
    add("交易行为", "margin_detail", "useful", trade_date=previous)
    add("交易行为", "block_trade", "optional", trade_date=previous)
    add("交易行为", "hk_hold", "optional", trade_date=previous)
    add("交易行为", "moneyflow_hsgt", "optional", start_date=month_start, end_date=recent)
    add("交易行为", "cyq_perf", "optional", trade_date=previous)
    add("交易行为", "stk_factor_pro", "optional", trade_date=previous)

    # 财务：全市场按报告期（vip）+ 单只股票（检查修订版本）
    for api in ("income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip"):
        add("财务", api, "core", period="20141231")
        add("财务", api, "core", period="20251231")
    for api in ("income", "balancesheet", "cashflow", "fina_indicator"):
        add("财务", api, "core", ts_code=SAMPLE_STOCK)
    add("财务", "forecast_vip", "useful", period="20251231")
    add("财务", "express_vip", "useful", period="20251231")
    add("财务", "forecast_vip", "useful", period="20141231")
    add("财务", "dividend", "useful", ts_code=SAMPLE_STOCK)
    add("财务", "disclosure_date", "useful", end_date="20251231")

    # 行业与指数
    add("行业指数", "index_classify", "core", src="SW2021")
    add("行业指数", "index_member_all", "core", ts_code=SAMPLE_STOCK)
    add("行业指数", "index_member_all", "core", l1_code="801780.SI")
    add("行业指数", "sw_daily", "useful", trade_date=recent)
    add("行业指数", "sw_daily", "useful", trade_date=OLD_DATES["2016"])
    for index_code in INDEXES:
        add("行业指数", "index_daily", "core", ts_code=index_code, start_date="20140101", end_date=recent)
    add("行业指数", "index_weight", "useful", index_code="000852.SH", start_date=month_start, end_date=recent)
    add("行业指数", "index_weight", "useful", index_code="000852.SH", start_date="20160101", end_date="20160131")
    add("行业指数", "index_dailybasic", "optional", trade_date=recent)

    # 股东与事件
    add("股东事件", "stk_holdernumber", "useful", ts_code=SAMPLE_STOCK)
    add("股东事件", "stk_holdertrade", "useful", ann_date=previous)
    add("股东事件", "repurchase", "useful", ann_date=previous)
    add("股东事件", "share_float", "useful", ann_date=previous)
    add("股东事件", "report_rc", "optional", report_date=previous)
    add("股东事件", "anns_d", "uncertain", ann_date=previous)

    # ---- 以下为研究蓝图（docs/ALPHA_BLUEPRINT.md）新增信息族的探测 ----
    month = previous[:6]
    dt_start = f"{previous[:4]}-{previous[4:6]}-{previous[6:]} 09:00:00"
    dt_end = f"{previous[:4]}-{previous[4:6]}-{previous[6:]} 18:00:00"

    # 历史深度：单只股票最早能取到哪一年
    add("历史深度", "daily", "core", ts_code=SAMPLE_STOCK, start_date="19900101", end_date="20051231")
    add("历史深度", "daily_basic", "core", ts_code=SAMPLE_STOCK, start_date="19900101", end_date="20101231")
    add("历史深度", "moneyflow", "useful", ts_code=SAMPLE_STOCK, start_date="20050101", end_date="20141231")
    add("历史深度", "stk_limit", "core", ts_code=SAMPLE_STOCK, start_date="20050101", end_date="20141231")

    # 集合竞价与筹码
    add("微观结构", "stk_auction_o", "optional", trade_date=previous)
    add("微观结构", "stk_auction", "optional", trade_date=previous)
    add("筹码", "cyq_chips", "useful", ts_code=SAMPLE_STOCK, trade_date=previous)
    add("筹码", "cyq_perf", "useful", trade_date=OLD_DATES["2019"])

    # 涨停生态与游资
    add("涨停生态", "limit_step", "useful", trade_date=previous)
    add("涨停生态", "limit_cpt_list", "useful", trade_date=previous)
    add("涨停生态", "kpl_list", "optional", trade_date=previous, tag="涨停")
    add("涨停生态", "kpl_concept", "optional", trade_date=previous)
    add("涨停生态", "hm_list", "optional")
    add("涨停生态", "hm_detail", "optional", trade_date=previous)
    add("涨停生态", "ths_hot", "optional", trade_date=previous, market="热股")
    add("涨停生态", "dc_hot", "optional", trade_date=previous, market="A股市场")

    # 概念板块与板块资金
    add("关系网络", "ths_index", "useful", exchange="A", type="N")
    add("关系网络", "ths_member", "useful", ts_code="885800.TI")
    add("关系网络", "ths_daily", "useful", trade_date=previous)
    add("关系网络", "dc_index", "optional", trade_date=previous)
    add("关系网络", "moneyflow_ths", "optional", trade_date=previous)
    add("关系网络", "moneyflow_dc", "optional", trade_date=previous)
    add("关系网络", "moneyflow_ind_ths", "optional", trade_date=previous)

    # 分析师预期与机构行为
    add("预期与机构", "report_rc", "useful", ts_code=SAMPLE_STOCK)
    add("预期与机构", "broker_recommend", "useful", month=month)
    add("预期与机构", "stk_surv", "useful", trade_date=previous)
    add("预期与机构", "top10_holders", "useful", ts_code=SAMPLE_STOCK)
    add("预期与机构", "top10_floatholders", "useful", ts_code=SAMPLE_STOCK)
    add("预期与机构", "fund_portfolio", "optional", ts_code="510300.SH")
    add("预期与机构", "hsgt_top10", "optional", trade_date=OLD_DATES["2019"])
    add("预期与机构", "hk_hold", "optional", trade_date=OLD_DATES["2019"])
    add("预期与机构", "fund_share", "optional", trade_date=previous)

    # 公司事件与财务补充
    add("公司事件", "pledge_stat", "useful", ts_code=SAMPLE_STOCK)
    add("公司事件", "fina_mainbz_vip", "optional", period="20251231", type="P")
    add("公司事件", "fina_audit", "optional", ts_code=SAMPLE_STOCK)
    add("公司事件", "stk_holdertrade", "useful", ts_code=SAMPLE_STOCK)

    # 宏观与衍生品（只用于市场状态）
    add("宏观衍生品", "shibor", "useful", start_date=month_start, end_date=recent)
    add("宏观衍生品", "yc_cb", "optional", ts_code="1001.CB", curve_type="0", trade_date=previous)
    add("宏观衍生品", "cn_pmi", "optional", start_m="202401", end_m=month)
    add("宏观衍生品", "cn_m", "optional", start_m="202401", end_m=month)
    add("宏观衍生品", "sf_month", "optional", start_m="202401", end_m=month)
    add("宏观衍生品", "fut_daily", "useful", trade_date=previous, exchange="CFFEX")
    add("宏观衍生品", "opt_daily", "optional", trade_date=previous, exchange="SSE")
    add("宏观衍生品", "margin", "useful", trade_date=previous)

    # 文本（独立课题，先了解权限）
    add("文本", "news", "optional", src="sina", start_date=dt_start, end_date=dt_end)
    add("文本", "major_news", "optional", start_date=dt_start, end_date=dt_end)
    add("文本", "irm_qa_sz", "uncertain", trade_date=previous)
    add("文本", "irm_qa_sh", "uncertain", trade_date=previous)
    add("文本", "research_report", "uncertain", trade_date=previous)
    return probes


def summarize(df: Any) -> dict[str, Any]:
    """只提取元数据。"""
    if df is None:
        return {"rows": 0, "columns": [], "date_ranges": {}, "extras": {}}
    extras: dict[str, Any] = {}
    columns = list(df.columns)
    if len(df):
        if "update_flag" in columns:
            extras["update_flag_counts"] = {str(k): int(v) for k, v in df["update_flag"].value_counts().items()}
        if "f_ann_date" in columns:
            extras["f_ann_date_non_null_ratio"] = round(float(df["f_ann_date"].notna().mean()), 4)
        if "ann_date" in columns:
            extras["ann_date_non_null_ratio"] = round(float(df["ann_date"].notna().mean()), 4)
        if "ts_code" in columns:
            codes = df["ts_code"].astype(str)
            extras["distinct_ts_code"] = int(codes.nunique())
            extras["by_exchange_suffix"] = {str(k): int(v) for k, v in codes.str[-2:].value_counts().items()}
        if "end_date" in columns and "ts_code" in columns:
            # 同一报告期出现多行，说明能取到修订前后的多个版本
            dup = df.groupby(["ts_code", "end_date"]).size()
            extras["max_versions_per_period"] = int(dup.max())
    return {"rows": int(len(df)), "columns": columns, "date_ranges": c.date_range_of(df), "extras": extras}


def run(pro: Any, today: str | None = None, sleep=None) -> dict[str, Any]:
    today = today or datetime.now().strftime("%Y%m%d")
    call_kwargs = {"sleep": sleep} if sleep is not None else {}
    dates = resolve_recent_dates(pro, today)
    if not dates["ok"]:
        return {"status": "failed", "stage": "resolve_recent_dates", "error": dates["error"], "probes": []}

    results = []
    for probe in build_probes(dates["recent"], dates["previous"]):
        res = c.call(pro, probe["api"], **call_kwargs, **probe["params"])
        entry = {
            "group": probe["group"], "api": probe["api"], "need": probe["need"], "params": probe["params"],
            "ok": res["ok"], "error": res["error"], "elapsed_seconds": res["elapsed"],
            "rate_limited_times": res["rate_limited_times"],
        }
        entry.update(summarize(res["df"]) if res["ok"] else {"rows": 0, "columns": [], "date_ranges": {}, "extras": {}})
        entry["empty"] = bool(res["ok"] and entry["rows"] == 0)
        results.append(entry)
        flag = "OK " if res["ok"] and entry["rows"] else ("空 " if res["ok"] else "失败")
        print(f"[{flag}] {probe['api']:<20} rows={entry['rows']:<7} {probe['params']}", flush=True)

    def bucket(need: str) -> dict[str, list[str]]:
        apis = sorted({r["api"] for r in results if r["need"] == need})
        usable = sorted({r["api"] for r in results if r["need"] == need and r["ok"] and r["rows"] > 0})
        return {"usable": usable, "not_usable": sorted(set(apis) - set(usable))}

    return {
        "status": "completed",
        "today": today,
        "recent_trade_date": dates["recent"],
        "previous_trade_date": dates["previous"],
        "summary": {need: bucket(need) for need in ("core", "useful", "optional", "uncertain")},
        "probe_count": len(results),
        "probes": results,
    }


def main() -> int:
    started = c.utc_now()
    pro = c.get_pro()
    try:
        import tushare as ts

        tushare_version = ts.__version__
    except Exception:  # noqa: BLE001
        tushare_version = None
    report = run(pro)
    report["tushare_version"] = tushare_version
    c.write_json(RESULT_DIR / "probe.json", report)
    c.write_run_meta(RESULT_DIR, started, {"task": "T002"})
    print(f"\n已写入 {RESULT_DIR / 'probe.json'}；状态：{report['status']}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
