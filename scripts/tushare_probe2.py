"""T005：补充探测（由设计方提供，受保护）。

回答 T002 留下的问题：各信息族的历史起点、历史行业成分能否取到、分钟线有无免费替代来源。
只记录元数据，不保存逐行数据。

用法：
    python scripts/tushare_probe2.py
输出：
    results/T005/probe2.json
    results/T005/run_meta.json
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
import tushare_probe as base  # noqa: E402

RESULT_DIR = c.REPO_ROOT / "results" / "T005"
STOCK = "000001.SZ"
# 用来定位"从哪一年开始有数据"的固定交易日
YEAR_DATES = ["20150105", "20160104", "20170103", "20180102", "20190102", "20200102", "20210104", "20220104", "20230103"]
# 需要标定起点的按日接口
DEPTH_APIS = [
    ("limit_list_d", "useful"), ("cyq_perf", "useful"), ("top_list", "useful"), ("top_inst", "useful"),
    ("margin_detail", "useful"), ("block_trade", "optional"), ("stk_factor_pro", "optional"),
    ("limit_step", "optional"), ("hm_detail", "optional"), ("ths_daily", "useful"),
    ("stk_surv", "useful"), ("hk_hold", "optional"), ("moneyflow_ths", "optional"), ("moneyflow_dc", "optional"),
]


def build_probes() -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []

    def add(group: str, api: str, need: str, **params: Any) -> None:
        probes.append({"group": group, "api": api, "need": need, "params": params})

    for api, need in DEPTH_APIS:
        for trade_date in YEAR_DATES:
            add("历史起点", api, need, trade_date=trade_date)
    add("历史起点", "fut_daily", "useful", trade_date="20150105", exchange="CFFEX")
    add("历史起点", "opt_daily", "optional", trade_date="20160104", exchange="SSE")
    add("历史起点", "shibor", "useful", start_date="20150101", end_date="20150131")
    add("历史起点", "report_rc", "useful", report_date="20150105")
    add("历史起点", "broker_recommend", "useful", month="202001")
    add("历史起点", "stk_holdernumber", "useful", ts_code=STOCK, start_date="20100101", end_date="20151231")
    add("历史起点", "stk_holdertrade", "useful", ann_date="20150105")
    add("历史起点", "repurchase", "useful", ann_date="20150105")
    add("历史起点", "share_float", "useful", ann_date="20150105")
    add("历史起点", "sw_daily", "useful", trade_date="20150105")
    add("历史起点", "index_weight", "useful", index_code="000852.SH", start_date="20261001", end_date="20261031")
    add("历史起点", "index_weight", "useful", index_code="000905.SH", start_date="20150101", end_date="20150131")

    # 历史行业成分：is_new='N' 是否返回已剔除的历史归属
    add("历史行业", "index_member_all", "core", ts_code=STOCK, is_new="N")
    add("历史行业", "index_member_all", "core", ts_code="000004.SZ", is_new="N")
    add("历史行业", "index_member_all", "core", l1_code="801780.SI", is_new="N")
    add("历史行业", "index_member_all", "core", l1_code="801780.SI", is_new="Y")
    add("历史行业", "index_classify", "core", src="SW2014")
    add("历史行业", "index_member", "optional", ts_code=STOCK)

    # 历史 ST 状态与全市场曾用名
    add("ST历史", "namechange", "core", start_date="20150101", end_date="20150331")
    add("ST历史", "stock_st", "optional", trade_date="20200102")
    add("ST历史", "bak_basic", "optional", trade_date="20200102")

    # 概念板块成分是否带纳入、剔除日期
    add("概念", "ths_member", "useful", ts_code="885800.TI")
    add("概念", "dc_member", "optional", trade_date="20261110")
    add("概念", "kpl_concept_cons", "optional", trade_date="20261110")
    return probes


def probe_baostock() -> dict[str, Any]:
    """免费分钟线来源探测。未安装 baostock 时如实记录，不算失败。"""
    out: dict[str, Any] = {"installed": False, "queries": []}
    try:
        import baostock as bs
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return out
    out["installed"] = True
    try:
        login = bs.login()
        out["login"] = {"error_code": login.error_code, "error_msg": login.error_msg}
        fields = "date,time,code,open,high,low,close,volume,amount"
        for code in ("sh.600000", "sz.300750"):
            for day in ("2015-01-05", "2019-01-02", "2023-01-03", "2026-11-10"):
                for freq in ("5", "30"):
                    try:
                        rs = bs.query_history_k_data_plus(code, fields, start_date=day, end_date=day,
                                                          frequency=freq, adjustflag="3")
                        rows = 0
                        while rs.error_code == "0" and rs.next():
                            rs.get_row_data()
                            rows += 1
                        out["queries"].append({"code": code, "day": day, "freq": freq, "rows": rows,
                                               "error_code": rs.error_code, "error_msg": rs.error_msg})
                    except Exception as exc:  # noqa: BLE001
                        out["queries"].append({"code": code, "day": day, "freq": freq, "rows": 0,
                                               "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        bs.logout()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out


def first_year_with_data(results: list[dict[str, Any]]) -> dict[str, str | None]:
    """每个按日接口最早出现数据的探测日。"""
    out: dict[str, str | None] = {}
    for api, _need in DEPTH_APIS:
        dates = sorted(r["params"]["trade_date"] for r in results
                       if r["api"] == api and r["group"] == "历史起点" and r["ok"] and r["rows"] > 0)
        out[api] = dates[0] if dates else None
    return out


def run(pro: Any, sleep=None, with_baostock: bool = True) -> dict[str, Any]:
    kw = {"sleep": sleep} if sleep is not None else {}
    results = []
    for probe in build_probes():
        res = c.call(pro, probe["api"], **kw, **probe["params"])
        entry = {"group": probe["group"], "api": probe["api"], "need": probe["need"], "params": probe["params"],
                 "ok": res["ok"], "error": res["error"], "rate_limited_times": res["rate_limited_times"]}
        entry.update(base.summarize(res["df"]) if res["ok"] else {"rows": 0, "columns": [], "date_ranges": {}, "extras": {}})
        if res["ok"] and res["df"] is not None and len(res["df"]) and "out_date" in res["df"].columns:
            entry["extras"]["out_date_non_null"] = int(res["df"]["out_date"].notna().sum())
        results.append(entry)
        flag = "OK " if res["ok"] and entry["rows"] else ("空 " if res["ok"] else "失败")
        print(f"[{flag}] {probe['api']:<18} rows={entry['rows']:<6} {probe['params']}", flush=True)
    return {
        "status": "completed",
        "probe_count": len(results),
        "first_date_with_data": first_year_with_data(results),
        "probes": results,
        "baostock": probe_baostock() if with_baostock else {"skipped": True},
    }


def main() -> int:
    started = c.utc_now()
    report = run(c.get_pro())
    c.write_json(RESULT_DIR / "probe2.json", report)
    c.write_run_meta(RESULT_DIR, started, {"task": "T005"})
    print(f"\n已写入 {RESULT_DIR / 'probe2.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
