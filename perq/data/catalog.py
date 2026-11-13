"""采集清单：每个数据集对应一个接口、一种分区方式、起始日期与刷新规则。

分区方式（mode）：
- snapshot  整表快照，每次运行存一份，分区键为运行日期
- date      按交易日，一天一个分区
- cal       按自然日（公告类数据在周末也会发布）
- period    按财务报告期（季度末）
- year      按年份区间（start_date/end_date）

tier 1 为已由 T002 确认可用的核心数据；tier 2 等 T005 结果确认后启用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

BARS_START = "20150101"
FIN_START_PERIOD = "20121231"
BASIC_FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs"
# 申万 2021 版一级行业。历史成分按"当前成分（Y）+ 已剔除成分（N）"两次拉取后合并。
SW_L1_CODES = ["801010", "801030", "801040", "801050", "801080", "801110", "801120", "801130", "801140", "801150",
               "801160", "801170", "801180", "801200", "801210", "801230", "801710", "801720", "801730", "801740",
               "801750", "801760", "801770", "801780", "801790", "801880", "801890", "801950", "801960", "801970",
               "801980"]
INDEX_CODES = ["000001.SH", "000300.SH", "000905.SH", "000852.SH", "932000.CSI", "399001.SZ", "399006.SZ",
               "000688.SH", "899050.BJ", "000016.SH", "399303.SZ"]


@dataclass(frozen=True)
class Dataset:
    name: str
    api: str
    mode: str
    tier: int = 1
    date_param: str = "trade_date"
    start: str = BARS_START
    params: dict[str, Any] = field(default_factory=dict)
    check_column: str | None = None      # 返回数据里应当等于分区日期的列；None 表示不校验
    refresh_tail: int = 0                # 最近多少个分区在新的一天里重新拉取
    group: str = "行情"
    priority: str = "A"                 # A：基线必需，先拉；B：其余，随后拉。两类最终都会拉全


def _d(name: str, **kw: Any) -> Dataset:
    kw.setdefault("api", name)
    return Dataset(name=name, **kw)


def build_catalog() -> list[Dataset]:
    out: list[Dataset] = []
    # ---- 快照 ----
    out.append(_d("trade_cal", mode="snapshot", params={"exchange": "SSE", "start_date": "19901201"}, group="基础"))
    for status in ("L", "D", "P"):
        out.append(_d(f"stock_basic_{status}", api="stock_basic", mode="snapshot",
                      params={"exchange": "", "list_status": status, "fields": BASIC_FIELDS}, group="基础"))
    for code in SW_L1_CODES:
        for is_new in ("Y", "N"):
            out.append(_d(f"sw_member_{code}_{is_new}", api="index_member_all", mode="snapshot",
                          params={"l1_code": f"{code}.SI", "is_new": is_new}, group="行业指数"))
    for src in ("SW2021", "SW2014"):
        out.append(_d(f"index_classify_{src}", api="index_classify", mode="snapshot", params={"src": src}, group="行业指数"))

    # ---- 按交易日 ----
    for name in ("daily", "adj_factor", "daily_basic", "stk_limit", "moneyflow"):
        out.append(_d(name, mode="date", check_column="trade_date", refresh_tail=3))
    out.append(_d("suspend_d", mode="date", check_column="trade_date", refresh_tail=3, group="基础"))
    out.append(_d("cyq_perf", mode="date", start="20180101", check_column="trade_date", refresh_tail=3, group="筹码"))
    out.append(_d("limit_list_d", mode="date", start="20200101", check_column="trade_date", refresh_tail=3, group="涨停生态"))
    for name in ("top_list", "top_inst", "margin_detail", "block_trade"):
        out.append(_d(name, mode="date", check_column="trade_date", refresh_tail=3, group="资金行为"))
    out.append(_d("margin", mode="date", check_column="trade_date", refresh_tail=3, group="宏观衍生品"))
    out.append(_d("fut_daily_cffex", api="fut_daily", mode="date", params={"exchange": "CFFEX"},
                  check_column="trade_date", refresh_tail=3, group="宏观衍生品"))
    out.append(_d("sw_daily", mode="date", check_column="trade_date", refresh_tail=3, group="行业指数"))
    out.append(_d("index_dailybasic", mode="date", check_column="trade_date", refresh_tail=3, group="行业指数"))

    # ---- 按自然日（公告日） ----
    for name in ("dividend", "repurchase", "share_float", "stk_holdertrade", "stk_holdernumber"):
        out.append(_d(name, mode="cal", date_param="ann_date", check_column="ann_date", refresh_tail=10, group="公司事件"))
    out.append(_d("report_rc", mode="cal", date_param="report_date", start="20140101",
                  check_column="report_date", refresh_tail=10, group="分析师预期"))

    # ---- 按报告期 ----
    for api in ("income_vip", "balancesheet_vip", "cashflow_vip"):
        for report_type in ("1", "4", "5"):
            out.append(_d(f"{api}_rt{report_type}", api=api, mode="period", date_param="period", start=FIN_START_PERIOD,
                          params={"report_type": report_type}, check_column="end_date", refresh_tail=8, group="财务"))
    for api in ("forecast_vip", "express_vip"):
        out.append(_d(api, mode="period", date_param="period", start=FIN_START_PERIOD,
                      check_column="end_date", refresh_tail=8, group="财务"))
    out.append(_d("disclosure_date", mode="period", date_param="end_date", start=FIN_START_PERIOD,
                  check_column="end_date", refresh_tail=8, group="财务"))

    # ---- 按年份区间 ----
    for code in INDEX_CODES:
        out.append(_d(f"index_daily_{code.replace('.', '_')}", api="index_daily", mode="year", start="20140101",
                      params={"ts_code": code}, refresh_tail=1, group="行业指数"))
    out.append(_d("shibor", mode="year", refresh_tail=1, group="宏观衍生品"))
    # 曾用名（ST 与退市整理的历史）：按公告日期区间取，2005 年起，覆盖 2015 年时仍然生效的名称
    out.append(_d("namechange", mode="year", start="20050101", refresh_tail=1, group="基础"))

    # ---- T005 确认了起点的数据 ----
    for name, group, start in (("ths_daily", "关系网络", "20150101"), ("hk_hold", "资金行为", "20170101"),
                               ("stk_surv", "分析师预期", "20220101"), ("hm_detail", "涨停生态", "20230101"),
                               ("stock_st", "基础", "20160101")):
        out.append(_d(name, mode="date", start=start, check_column="trade_date", refresh_tail=3, group=group))
    out.append(_d("opt_daily_sse", api="opt_daily", mode="date", start="20150209", params={"exchange": "SSE"},
                  check_column="trade_date", refresh_tail=3, group="宏观衍生品"))
    # ---- tier 2：历史太短或可由日线自行计算，暂不拉取 ----
    for name, group, start in (("limit_step", "涨停生态", "20230701"), ("moneyflow_ths", "资金行为", "20200101")):
        out.append(_d(name, mode="date", tier=2, start=start, check_column="trade_date", refresh_tail=3, group=group))
    core = {"daily", "adj_factor", "daily_basic", "stk_limit", "moneyflow", "namechange"}
    from dataclasses import replace
    return [d if (d.mode in ("snapshot", "period") or d.name in core or d.name.startswith("index_daily_"))
            else replace(d, priority="B") for d in out]


# ---------- 分区生成 ----------

def quarter_ends(start: str, today: str) -> list[str]:
    out = []
    for year in range(int(start[:4]), int(today[:4]) + 1):
        for md in ("0331", "0630", "0930", "1231"):
            period = f"{year}{md}"
            if start <= period <= today:
                out.append(period)
    return out


def calendar_days(start: str, today: str) -> list[str]:
    cursor = datetime.strptime(start, "%Y%m%d").date()
    end = datetime.strptime(today, "%Y%m%d").date()
    out = []
    while cursor <= end:
        out.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return out


def partitions(ds: Dataset, open_days: list[str], today: str) -> list[tuple[str, dict[str, Any]]]:
    """返回 [(分区键, 调用参数)]，按时间从旧到新。"""
    if ds.mode == "snapshot":
        params = dict(ds.params)
        if ds.api == "trade_cal":
            params["end_date"] = f"{int(today[:4]) + 1}1231"
        return [(today, params)]
    if ds.mode == "date":
        return [(day, {**ds.params, ds.date_param: day}) for day in open_days if ds.start <= day <= today]
    if ds.mode == "cal":
        return [(day, {**ds.params, ds.date_param: day}) for day in calendar_days(ds.start, today)]
    if ds.mode == "period":
        return [(period, {**ds.params, ds.date_param: period}) for period in quarter_ends(ds.start, today)]
    if ds.mode == "year":
        out = []
        for year in range(int(ds.start[:4]), int(today[:4]) + 1):
            lo = max(ds.start, f"{year}0101")
            hi = min(today, f"{year}1231")
            out.append((str(year), {**ds.params, "start_date": lo, "end_date": hi}))
        return out
    raise ValueError(f"未知分区方式：{ds.mode}")


def today_str() -> str:
    return date.today().strftime("%Y%m%d")
