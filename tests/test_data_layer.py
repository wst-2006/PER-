"""数据层测试：全部使用手工构造的小数据，不联网。"""
from __future__ import annotations

import json

import duckdb
import pandas as pd
import pytest

from perq.data import backfill, catalog as cat, client, curated, quality
from perq.data.store import RawStore

NO_SLEEP = lambda _s: None  # noqa: E731
DAYS = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-01", "2024-12-31")]


# ---------------- client.fetch_all ----------------

class PagedPro:
    """模拟单次上限 1000 行的接口。"""

    def __init__(self, total, honor_offset=True, fail_on_page=None):
        self.total, self.honor_offset, self.fail_on_page, self.calls = total, honor_offset, fail_on_page, 0

    def query(self, api, **params):
        self.calls += 1
        if self.fail_on_page == self.calls:
            raise Exception("server error")
        offset = params.get("offset", 0) if self.honor_offset else 0
        rows = list(range(offset, min(self.total, offset + 1000)))
        return pd.DataFrame({"x": rows})


def test_fetch_all_pages_until_complete():
    res = client.fetch_all(PagedPro(2300), "any", {}, sleep=NO_SLEEP)
    assert res["ok"] and len(res["df"]) == 2300 and res["pages"] == 3 and not res["truncation_suspected"]
    assert res["df"]["x"].tolist() == list(range(2300))


def test_fetch_all_no_extra_call_when_not_round():
    pro = PagedPro(730)
    res = client.fetch_all(pro, "any", {}, sleep=NO_SLEEP)
    assert len(res["df"]) == 730 and pro.calls == 1


def test_fetch_all_exact_cap_then_empty_page():
    pro = PagedPro(1000)
    res = client.fetch_all(pro, "any", {}, sleep=NO_SLEEP)
    assert len(res["df"]) == 1000 and pro.calls == 2 and not res["truncation_suspected"]


def test_fetch_all_flags_when_offset_ignored_or_page_fails():
    ignored = client.fetch_all(PagedPro(5000, honor_offset=False), "any", {}, sleep=NO_SLEEP)
    assert ignored["truncation_suspected"] and len(ignored["df"]) == 1000
    failed = client.fetch_all(PagedPro(5000, fail_on_page=2), "any", {}, sleep=NO_SLEEP)
    assert failed["ok"] and failed["truncation_suspected"] and "翻页失败" in failed["note"]


# ---------------- store ----------------

def test_store_schema_is_stable_across_partitions(tmp_path):
    store = RawStore(tmp_path)
    # 第一个分区里 delist_date 全空、pe 全空；第二个分区里它们有值
    store.write("t", "a", pd.DataFrame({"ts_code": ["x"], "delist_date": [None], "pe": [None], "close": [1]}))
    store.write("t", "b", pd.DataFrame({"ts_code": ["y"], "delist_date": ["20240101"], "pe": [3.5], "close": [2.5]}))
    # 第三个分区里数值列以字符串形式出现、字符串列全空
    store.write("t", "c", pd.DataFrame({"ts_code": ["z"], "delist_date": [None], "pe": ["4.5"], "close": ["oops"]}))
    assert store.schema("t") == {"ts_code": "string", "close": "float64", "delist_date": "string", "pe": "float64"}
    got = duckdb.sql(f"SELECT ts_code, delist_date, pe, close FROM read_parquet('{store.glob('t')}', union_by_name=true) ORDER BY 1").fetchall()
    assert got[0] == ("x", None, None, 1.0) and got[1] == ("y", "20240101", 3.5, 2.5) and got[2][:3] == ("z", None, 4.5)
    assert got[2][3] is None


def test_store_supersedes_instead_of_overwriting(tmp_path):
    store = RawStore(tmp_path)
    store.write("t", "k", pd.DataFrame({"v": [1.0]}))
    store.write("t", "k", pd.DataFrame({"v": [2.0]}))
    assert pd.read_parquet(store.path("t", "k"))["v"].tolist() == [2.0]
    assert len(list((tmp_path / "raw" / "_superseded" / "t").glob("k.*.parquet"))) == 1
    assert store.entries("t")["k"]["rows"] == 1 and store.summary("t")["partitions"] == 1


def test_store_records_empty_and_failed(tmp_path):
    store = RawStore(tmp_path)
    assert store.write("t", "e", pd.DataFrame({"v": []}))["status"] == "empty"
    store.record_failure("t", "f", "boom")
    assert not store.has_data("t")
    assert store.summary("t")["by_status"] == {"empty": 1, "failed": 1}


# ---------------- catalog ----------------

def test_catalog_names_unique_and_partitions():
    datasets = cat.build_catalog()
    names = [d.name for d in datasets]
    assert len(names) == len(set(names))
    by = {d.name: d for d in datasets}
    assert [k for k, _ in cat.partitions(by["daily"], ["20141231", "20150105", "20150106"], "20150105")] == ["20150105"]
    periods = cat.partitions(by["income_vip_rt5"], [], "20130701")
    assert [k for k, _ in periods] == ["20121231", "20130331", "20130630"]
    assert periods[0][1] == {"report_type": "5", "period": "20121231"}
    cal_days = cat.partitions(by["dividend"], [], "20150103")
    assert [k for k, _ in cal_days] == ["20150101", "20150102", "20150103"] and cal_days[0][1] == {"ann_date": "20150101"}
    years = cat.partitions(by["index_daily_000300_SH"], [], "20150310")
    assert years == [("2014", {"ts_code": "000300.SH", "start_date": "20140101", "end_date": "20141231"}),
                     ("2015", {"ts_code": "000300.SH", "start_date": "20150101", "end_date": "20150310"})]
    snap = cat.partitions(by["trade_cal"], [], "20261112")
    assert snap[0][0] == "20261112" and snap[0][1]["end_date"] == "20271231"


# ---------------- backfill ----------------

class BackfillPro:
    def __init__(self, deny=(), ignore_date=()):
        self.deny, self.ignore_date, self.calls = set(deny), set(ignore_date), []

    def query(self, api, **params):
        self.calls.append((api, dict(params)))
        if api in self.deny:
            raise Exception("抱歉，您没有接口访问权限")
        if api == "trade_cal":
            days = pd.date_range("2024-12-20", "2025-01-10")
            return pd.DataFrame({"cal_date": days.strftime("%Y%m%d"), "is_open": [int(d.weekday() < 5) for d in days]})
        if api == "daily":
            day = "20200101" if api in self.ignore_date else params["trade_date"]
            return pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "trade_date": [day, day], "close": [1.0, 2.0]})
        if api == "adj_factor":
            return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [params["trade_date"]], "adj_factor": [1.0]})
        return pd.DataFrame()


def test_backfill_resumes_and_refreshes_tail(tmp_path):
    store = RawStore(tmp_path)
    pro = BackfillPro()
    first = backfill.run(pro, store, today="20250108", only=["daily", "adj_factor"], sleep=NO_SLEEP, log=lambda m: None)
    # 假日历从 2024-12-20 开始，到 2025-01-08 共 14 个工作日
    assert first["datasets"]["daily"]["fetched"] == 14 and first["datasets"]["daily"]["rows"] == 28
    assert first["datasets"]["trade_cal"]["fetched"] == 1
    n = len(pro.calls)
    again = backfill.run(pro, store, today="20250108", only=["daily", "adj_factor"], sleep=NO_SLEEP, log=lambda m: None)
    assert again["datasets"]["daily"]["planned"] == 0 and len(pro.calls) == n
    # 第二天：新增 1 个交易日；尾部 3 个分区因为是前一天拉的而刷新……但清单时间是真实的今天，
    # 所以这里只验证"新的一天会多出新分区"
    nxt = backfill.run(pro, store, today="20250109", only=["daily"], sleep=NO_SLEEP, log=lambda m: None)
    assert nxt["datasets"]["daily"]["planned"] >= 1
    assert store.summary("daily")["last_key"] == "20250109"


def test_backfill_tail_refresh_logic(tmp_path):
    store = RawStore(tmp_path)
    ds = next(d for d in cat.build_catalog() if d.name == "daily")
    days = ["20250102", "20250103", "20250106", "20250107", "20250108"]
    for day in days:
        store.write("daily", day, pd.DataFrame({"ts_code": ["a"], "trade_date": [day]}))
    # 把清单里的拉取时间改成"昨天"
    path = store._manifest_path("daily")
    path.write_text("\n".join(json.dumps({**json.loads(line), "fetched_at": "2025-01-08T10:00:00Z"})
                              for line in path.read_text().splitlines()) + "\n")
    todo = [k for k, _ in backfill.pending(ds, store, days, "20250109")]
    assert todo == ["20250106", "20250107", "20250108"]          # 只有尾部 3 个需要刷新
    assert backfill.pending(ds, store, days, "20250108") == []    # 同一天内不重复拉


def test_backfill_rejects_data_for_wrong_date_and_skips_denied(tmp_path):
    store = RawStore(tmp_path)
    pro = BackfillPro(deny={"adj_factor"}, ignore_date={"daily"})
    rep = backfill.run(pro, store, today="20250108", only=["daily", "adj_factor"], sleep=NO_SLEEP, log=lambda m: None)
    daily = rep["datasets"]["daily"]
    assert daily["fetched"] == 0 and daily["failed"] == 14 and "不一致" in daily["last_error"]
    assert not store.has_data("daily")
    assert rep["datasets"]["adj_factor"]["failed"] == 14 and "权限" in rep["datasets"]["adj_factor"]["last_error"]
    # 失败的分区下次会重试
    ds = next(d for d in cat.build_catalog() if d.name == "daily")
    assert len(backfill.pending(ds, store, backfill.load_open_days(store), "20250108")) == 14


def test_backfill_respects_call_budget(tmp_path):
    rep = backfill.run(BackfillPro(), RawStore(tmp_path), today="20250108", only=["daily"], max_calls=3,
                       sleep=NO_SLEEP, log=lambda m: None)
    assert rep["stopped_by_budget"] and rep["calls"] == 3


# ---------------- curated + quality：构造一个小市场 ----------------

@pytest.fixture()
def market(tmp_path):
    """三只股票：
    A 正常股票，2024-06-03 除权（10 送 10：价格减半，复权因子翻倍），2024-03-04 一字涨停，3 月 11 至 15 日停牌
    B 2024-02-01 才上市，但上游在上市前给了 5 行脏数据；2024-05-06 起戴帽 ST，2024-11-01 进入退市整理
    C 成交额很小，永远不进股票池；2024-08-05 的涨跌停价缺失
    """
    store = RawStore(tmp_path)
    store.write("trade_cal", "20250101", pd.DataFrame({
        "cal_date": pd.date_range("2023-06-01", "2024-12-31").strftime("%Y%m%d"),
        "is_open": [int(d.weekday() < 5) for d in pd.date_range("2023-06-01", "2024-12-31")]}))
    store.write("stock_basic_L", "20250101", pd.DataFrame({
        "ts_code": ["A.SZ", "C.SH"], "name": ["甲", "丙"], "market": ["主板", "科创板"], "exchange": ["SZSE", "SSE"],
        "list_status": ["L", "L"], "list_date": ["20200101", "20230601"], "delist_date": [None, None]}))
    store.write("stock_basic_D", "20250101", pd.DataFrame({
        "ts_code": ["B.BJ"], "name": ["乙退"], "market": [None], "exchange": ["BSE"],
        "list_status": ["D"], "list_date": ["20240201"], "delist_date": ["20241220"]}))
    store.write("sw_member_801080_Y", "20250101", pd.DataFrame({
        "ts_code": ["A.SZ", "B.BJ"], "l1_code": ["801080.SI", "801750.SI"], "l1_name": ["电子", "计算机"],
        "l2_code": ["801081.SI", "801101.SI"], "l2_name": ["半导体", "软件"], "in_date": ["20240701", "20240201"],
        "out_date": [None, None], "is_new": ["Y", "Y"]}))
    store.write("sw_member_801780_N", "20250101", pd.DataFrame({
        "ts_code": ["A.SZ"], "l1_code": ["801780.SI"], "l1_name": ["银行"], "l2_code": ["801783.SI"], "l2_name": ["股份行"],
        "in_date": ["20200101"], "out_date": ["20240701"], "is_new": ["N"]}))
    store.write("namechange", "2024", pd.DataFrame({
        "ts_code": ["B.BJ", "B.BJ", "B.BJ"], "name": ["乙股份", "ST乙", "乙退"],
        "start_date": ["20240201", "20240506", "20241101"], "end_date": ["20240505", "20241031", None]}))

    rows, adj, lim, basic = [], [], [], []
    for code, base, amount in (("A.SZ", 20.0, 2.0e5), ("B.BJ", 8.0, 9.0e4), ("C.SH", 5.0, 1.0e3)):
        prev = base
        for i, day in enumerate(DAYS):
            if code == "A.SZ" and "20240311" <= day <= "20240315":
                continue                                    # 停牌：没有日线
            if code == "B.BJ" and day < "20240125":
                continue                                    # 上市前只有 5 行脏数据（1 月 25 至 31 日）
            if code == "B.BJ" and day > "20241220":
                continue
            factor = 2.0 if (code == "A.SZ" and day >= "20240603") else 1.0
            pre_close = prev / 2 if (code == "A.SZ" and day == "20240603") else prev
            up, down = round(pre_close * 1.1, 2), round(pre_close * 0.9, 2)
            close = round(pre_close * (1.003 if i % 2 else 0.998), 2)
            open_, high, low = pre_close, max(pre_close, close), min(pre_close, close)
            if code == "A.SZ" and day == "20240304":         # 一字涨停
                open_ = high = low = close = up
            rows.append({"ts_code": code, "trade_date": day, "open": open_, "high": high, "low": low, "close": close,
                         "pre_close": pre_close, "pct_chg": (close / pre_close - 1) * 100, "vol": 1000.0, "amount": amount})
            adj.append({"ts_code": code, "trade_date": day, "adj_factor": factor})
            if not (code == "C.SH" and day == "20240805"):
                lim.append({"ts_code": code, "trade_date": day, "up_limit": up, "down_limit": down})
            basic.append({"ts_code": code, "trade_date": day, "turnover_rate": 1.0, "total_mv": 1e6, "circ_mv": 5e5})
            prev = close
    for name, data in (("daily", rows), ("adj_factor", adj), ("stk_limit", lim), ("daily_basic", basic)):
        frame = pd.DataFrame(data)
        for day, part in frame.groupby("trade_date"):
            store.write(name, day, part.reset_index(drop=True))

    # 财务：A 的 2023 年报先公布（3 月 20 日），4 月 25 日更正；同一公告日有 0/1 两行重复
    store.write("income_vip_rt1", "20231231", pd.DataFrame({
        "ts_code": ["A.SZ", "A.SZ", "A.SZ", "C.SH"], "end_date": ["20231231"] * 4, "report_type": ["1"] * 4,
        "ann_date": ["20240320", "20240320", "20240320", "20240410"],
        "f_ann_date": ["20240320", "20240320", "20240425", "20240405"],
        "update_flag": ["0", "1", "1", "0"], "revenue": [100.0, 101.0, 90.0, 7.0]}))
    summary = curated.build_all(tmp_path, memory_limit="1GB", threads=2)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW bars AS SELECT * FROM read_parquet('{(tmp_path / 'curated' / 'bars').as_posix()}/*/*.parquet', hive_partitioning=true)")
    con.execute(f"CREATE VIEW fin AS SELECT * FROM read_parquet('{(tmp_path / 'curated' / 'fin_income.parquet').as_posix()}')")
    return tmp_path, summary, con


def test_curated_drops_rows_before_listing(market):
    _, summary, con = market
    assert con.execute("SELECT min(trade_date) FROM bars WHERE ts_code = 'B.BJ'").fetchone()[0] == "20240201"
    assert summary["bars"]["dropped_before_listing_or_off_calendar"] == 5 and summary["bars"]["orphan_rows"] == 0
    assert con.execute("SELECT DISTINCT board FROM bars WHERE ts_code = 'B.BJ'").fetchone()[0] == "北交所"


def test_curated_return_survives_ex_rights_day(market):
    _, _, con = market
    close_prev, = con.execute("SELECT close FROM bars WHERE ts_code='A.SZ' AND trade_date='20240531'").fetchone()
    close, ret, ret_pc = con.execute("SELECT close, ret, ret_pc FROM bars WHERE ts_code='A.SZ' AND trade_date='20240603'").fetchone()
    assert close < close_prev * 0.6                      # 原始价格腰斩
    assert abs(ret) < 0.01 and abs(ret - ret_pc) < 1e-6  # 复权后的收益是正常的小幅波动


def test_curated_suspension_and_limit_flags(market):
    _, _, con = market
    gap, = con.execute("SELECT gap_days FROM bars WHERE ts_code='A.SZ' AND trade_date='20240318'").fetchone()
    assert gap == 5
    flags = con.execute("SELECT open_at_up, locked_up, close_at_up, touched_up FROM bars WHERE ts_code='A.SZ' AND trade_date='20240304'").fetchone()
    assert flags == (True, True, True, True)
    assert con.execute("SELECT count(*) FROM bars WHERE ts_code='A.SZ' AND locked_up").fetchone()[0] == 1
    missing = con.execute("SELECT limit_missing, open_at_up, open_at_down FROM bars WHERE ts_code='C.SH' AND trade_date='20240805'").fetchone()
    assert missing == (True, False, False)


def test_curated_st_delisting_and_universe(market):
    _, _, con = market
    q = lambda day: con.execute(f"SELECT is_st, is_delisting, in_universe, listed_tdays FROM bars WHERE ts_code='B.BJ' AND trade_date='{day}'").fetchone()  # noqa: E731
    assert q("20240201")[:3] == (False, False, False) and q("20240201")[3] == 0     # 上市首日
    assert q("20240430")[:3] == (False, False, False)                                 # 上市未满 120 个交易日
    assert q("20240506")[:2] == (True, False)                                         # 戴帽
    assert q("20241101")[:2] == (False, True) and q("20241101")[2] is False           # 退市整理期
    a = con.execute("SELECT min(trade_date) FROM bars WHERE ts_code='A.SZ' AND in_universe").fetchone()[0]
    assert a == "20240119"                                                            # 第 15 根日线之后才有足够的成交记录
    # 停牌 5 天后复牌：最近 20 个交易日里有 15 根日线，刚好达到下限，仍在股票池内
    assert con.execute("SELECT bars_in_20, in_universe FROM bars WHERE ts_code='A.SZ' AND trade_date='20240318'").fetchone() == (15, True)
    assert con.execute("SELECT count(*) FROM bars WHERE ts_code='C.SH' AND in_universe").fetchone()[0] == 0  # 成交额太小


def test_financial_versions_and_point_in_time(market):
    _, _, con = market
    rows = con.execute("SELECT avail_date, update_flag, revenue FROM fin WHERE ts_code='A.SZ' ORDER BY 1").fetchall()
    assert rows == [("20240320", "1", 101.0), ("20240425", "1", 90.0)]   # 同日重复取标记为 1 的，保留两个版本
    # 实际公告日早于公告日时取较晚的一个
    assert con.execute("SELECT avail_date FROM fin WHERE ts_code='C.SH'").fetchone()[0] == "20240410"
    asof = lambda day: con.execute(f"SELECT ts_code, revenue FROM ({curated.fin_asof_sql('fin', day)}) ORDER BY 1").fetchall()  # noqa: E731
    assert asof("20240319") == []
    assert asof("20240320") == [("A.SZ", 101.0)]
    assert asof("20240424") == [("A.SZ", 101.0), ("C.SH", 7.0)]          # 更正尚未公布，不能用到 90
    assert asof("20240425") == [("A.SZ", 90.0), ("C.SH", 7.0)]


def test_quality_gates_pass_on_clean_market_and_catch_corruption(market):
    root, _, _ = market
    report = quality.run(root, memory_limit="1GB")
    assert report["pass"], report["gates"]
    assert report["info"]["financials"]["fin_income"]["stock_periods_with_multiple_versions"] == 1
    assert report["info"]["extreme_returns"]["abs_ret_over_31pct"] == 0
    # 破坏一个分区：收盘价高于涨停价、最高价低于最低价、复权因子缺失
    store = RawStore(root)
    bad = pd.read_parquet(store.path("daily", "20240702")).drop(columns=["_fetched_at"])
    bad.loc[bad.ts_code == "A.SZ", ["close", "high", "low"]] = [99.0, 1.0, 2.0]
    store.write("daily", "20240702", bad)
    for day in DAYS[100:140]:
        part = pd.read_parquet(store.path("adj_factor", day)).drop(columns=["_fetched_at"])
        store.write("adj_factor", day, part[part.ts_code != "C.SH"].reset_index(drop=True))
    curated.build_all(root, memory_limit="1GB", threads=2)
    broken = quality.run(root, memory_limit="1GB")
    assert not broken["pass"]
    failed = {name for name, gate in broken["gates"].items() if not gate["pass"]}
    assert {"close_outside_limit_rows", "high_below_low_rows", "adj_factor_missing_rate"} <= failed


def test_backfill_gives_up_on_a_dataset_after_repeated_failures(tmp_path):
    class Pro(BackfillPro):
        def query(self, api, **params):
            if api == "trade_cal":
                self.calls.append((api, params))
                days = pd.date_range("2024-11-01", "2025-01-10")
                return pd.DataFrame({"cal_date": days.strftime("%Y%m%d"), "is_open": [int(d.weekday() < 5) for d in days]})
            return super().query(api, **params)

    rep = backfill.run(Pro(deny={"daily"}), RawStore(tmp_path), today="20250108", only=["daily"],
                       sleep=NO_SLEEP, log=lambda m: None)
    stat = rep["datasets"]["daily"]
    assert stat["skipped_after_failures"] and stat["failed"] == backfill.MAX_CONSECUTIVE_FAILURES < stat["planned"]


def test_backfill_trial_takes_oldest_and_newest_partitions(tmp_path):
    store = RawStore(tmp_path)
    rep = backfill.run(BackfillPro(), store, today="20250108", only=["daily"], trial=2, sleep=NO_SLEEP, log=lambda m: None)
    assert rep["trial"] == 2 and rep["datasets"]["daily"]["fetched"] == 4
    keys = sorted(k for k in store.entries("daily"))
    assert keys == ["20241220", "20241223", "20250107", "20250108"]


def test_industry_is_assigned_as_of_date(market):
    _, summary, con = market
    q = lambda code, day: con.execute(f"SELECT sw_l1_name, sw_backfilled FROM bars WHERE ts_code='{code}' AND trade_date='{day}'").fetchone()  # noqa: E731
    assert q("A.SZ", "20240628") == ("银行", False)       # 6 月底还在旧行业
    assert q("A.SZ", "20240701") == ("电子", False)       # 7 月 1 日起换到新行业
    assert q("B.BJ", "20240301") == ("计算机", False)
    assert q("C.SH", "20240301") == (None, False)          # 没有任何行业记录
    assert summary["industry_rows"] == 3 and 0 < summary["bars"]["industry_missing_rate"] < 1


def test_catalog_covers_industry_history_and_namechange_by_year():
    by = {d.name: d for d in cat.build_catalog()}
    assert by["sw_member_801780_N"].params == {"l1_code": "801780.SI", "is_new": "N"}
    assert len([n for n in by if n.startswith("sw_member_")]) == 62
    parts = cat.partitions(by["namechange"], [], "20060301")
    assert parts == [("2005", {"start_date": "20050101", "end_date": "20051231"}),
                     ("2006", {"start_date": "20060101", "end_date": "20060301"})]
    assert by["stk_surv"].start == "20220101" and by["hk_hold"].tier == 1 and by["limit_step"].tier == 2
