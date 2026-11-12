"""对任务脚本的离线测试：不联网，不需要 token。"""
from __future__ import annotations

import json
import os

import pandas as pd
import pytest

import _common as c
import sample_pull
import tushare_probe
from fake_tushare import FakePro

NO_SLEEP = lambda _seconds: None  # noqa: E731


def test_call_scrubs_token_and_never_raises(monkeypatch):
    pro = FakePro(deny={"daily"})
    monkeypatch.setenv("TUSHARE_TOKEN", pro.token)
    res = c.call(pro, "daily", sleep=NO_SLEEP, trade_date="20261109")
    assert res["ok"] is False
    assert pro.token not in res["error"]
    assert "****" in res["error"]


def test_call_retries_after_rate_limit():
    pro = FakePro(rate_limit_once={"daily"})
    waits = []
    res = c.call(pro, "daily", sleep=waits.append, trade_date="20261109")
    assert res["ok"] is True
    assert res["rate_limited_times"] == 1
    assert c.RATE_LIMIT_WAIT_SECONDS in waits
    assert [api for api, _ in pro.calls] == ["daily", "daily"]


def test_call_recognizes_real_tushare_rate_limit_text():
    """T002 实测到的限流文案。"""
    class LimitedOnce:
        def __init__(self):
            self.n = 0

        def query(self, api, **params):
            self.n += 1
            if self.n == 1:
                raise Exception("抱歉，您访问接口(stk_mins)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。")
            return pd.DataFrame({"x": [1]})

    waits = []
    res = c.call(LimitedOnce(), "stk_mins", sleep=waits.append)
    assert res["ok"] is True and res["rate_limited_times"] == 1 and c.RATE_LIMIT_WAIT_SECONDS in waits


def test_call_gives_up_after_repeated_rate_limit():
    class AlwaysLimited:
        def query(self, api, **params):
            raise Exception("抱歉，您每分钟最多访问该接口200次")

    res = c.call(AlwaysLimited(), "daily", sleep=NO_SLEEP, max_rate_retries=2)
    assert res["ok"] is False and res["rate_limited_times"] == 2


def test_probe_reports_denied_empty_and_ok(monkeypatch):
    pro = FakePro(deny={"stk_limit", "anns_d"})
    monkeypatch.setenv("TUSHARE_TOKEN", pro.token)
    report = tushare_probe.run(pro, today="20261111", sleep=NO_SLEEP)
    assert report["status"] == "completed"
    assert report["recent_trade_date"] == "20261110" and report["previous_trade_date"] == "20261109"
    assert "stk_limit" in report["summary"]["core"]["not_usable"]
    assert "daily" in report["summary"]["core"]["usable"]
    assert "anns_d" in report["summary"]["uncertain"]["not_usable"]
    # 返回空表的接口算不可用，并单独标记 empty
    mins = [p for p in report["probes"] if p["api"] == "stk_mins"]
    assert mins and all(p["ok"] and p["empty"] for p in mins)
    assert "stk_mins" in report["summary"]["optional"]["not_usable"]
    # 结果里不得出现 token，也不得出现逐行数据
    text = json.dumps(report, ensure_ascii=False)
    assert pro.token not in text
    assert all(set(p) >= {"api", "params", "ok", "rows", "columns"} and "df" not in p for p in report["probes"])


def test_probe_detects_financial_revisions():
    report = tushare_probe.run(FakePro(), today="20261111", sleep=NO_SLEEP)
    income = next(p for p in report["probes"] if p["api"] == "income")
    assert income["extras"]["max_versions_per_period"] == 2
    assert income["extras"]["update_flag_counts"] == {"0": 2, "1": 1}
    assert income["date_ranges"]["f_ann_date"] == {"min": "20251025", "max": "20260425"}


def test_probe_fails_cleanly_without_calendar():
    report = tushare_probe.run(FakePro(deny={"trade_cal"}), today="20261111", sleep=NO_SLEEP)
    assert report["status"] == "failed" and report["stage"] == "resolve_recent_dates"


def test_probe_list_has_no_duplicates_and_covers_core():
    probes = tushare_probe.build_probes("20261110", "20261109")
    keys = [(p["api"], json.dumps(p["params"], sort_keys=True)) for p in probes]
    assert len(keys) == len(set(keys))
    core = {p["api"] for p in probes if p["need"] == "core"}
    assert {"stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic", "stk_limit",
            "suspend_d", "namechange", "income_vip", "index_member_all"} <= core


def test_pick_evenly_is_deterministic_and_spread():
    codes = [f"{i:06d}.SZ" for i in range(100)]
    picked = sample_pull.pick_evenly(list(reversed(codes)), 8)
    assert picked == sample_pull.pick_evenly(codes, 8)
    assert len(picked) == 8 and picked[0] == "000000.SZ" and picked[-1] >= "000080.SZ"
    assert sample_pull.pick_evenly(codes[:3], 8) == codes[:3]


def test_choose_codes_covers_boards_and_excludes_undated_delistings():
    pro = FakePro()
    groups = sample_pull.choose_codes(pro._stock_basic(list_status="L"), pro._stock_basic(list_status="D"))
    assert [len(groups[k]) for k in ("main_sh", "main_sz", "chinext", "star", "bse")] == [8, 8, 8, 8, 4]
    assert groups["st"] and all(code not in groups["main_sh"] for code in groups["st"])
    # 只取 2019 年以后退市且有退市日期的
    assert set(groups["delisted"]) <= {f"00{i:04d}.SZ" for i in range(904, 910)}
    assert len(groups["delisted"]) == 4


def test_sample_pull_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(sample_pull, "SAMPLE_DIR", tmp_path / "samples")
    pro = FakePro(deny={"moneyflow"})
    report = sample_pull.run(pro, today="20261111", sleep=NO_SLEEP)
    assert report["status"] == "completed_with_failures"
    assert report["failure_count"] > 0 and all(f["api"] == "moneyflow" for f in report["failures"])
    files = {item["file"] for item in report["files"]}
    assert {"stock_basic_all.parquet", "trade_cal.parquet", "stock_daily.parquet", "stock_income.parquet"} <= files
    assert "stock_moneyflow.parquet" not in files
    assert (tmp_path / "samples" / "perq_sample.zip").exists()
    assert report["zip_sha256"] and report["stock_count"] == len({c_ for g in report["groups"].values() for c_ in g})
    basic = pd.read_parquet(tmp_path / "samples" / "stock_basic_all.parquet")
    assert set(basic["list_status"]) == {"L", "D"}


def test_date_range_of_handles_empty_and_missing():
    assert c.date_range_of(pd.DataFrame()) == {}
    frame = pd.DataFrame({"trade_date": ["20240103", None, "20240102"], "x": [1, 2, 3]})
    assert c.date_range_of(frame) == {"trade_date": {"min": "20240102", "max": "20240103"}}


def test_load_env_does_not_override(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=from_file\nNEW_KEY='quoted'\n# comment\n", encoding="utf-8")
    monkeypatch.setattr(c, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "from_env")
    monkeypatch.delenv("NEW_KEY", raising=False)
    c.load_env()
    assert os.environ["TUSHARE_TOKEN"] == "from_env" and os.environ["NEW_KEY"] == "quoted"


def test_probe2_finds_first_date_and_flags_history(monkeypatch):
    import tushare_probe2

    class Pro(FakePro):
        def _limit_list_d(self, **params):
            if params["trade_date"] < "20200101":
                return pd.DataFrame(columns=["ts_code", "trade_date"])
            return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [params["trade_date"]]})

        def _index_member_all(self, **params):
            return pd.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "in_date": ["19910403", "20140101"],
                                 "out_date": ["20131231", None], "is_new": ["N", "Y"]})

    report = tushare_probe2.run(Pro(deny={"hm_detail"}), sleep=NO_SLEEP, with_baostock=False)
    assert report["status"] == "completed"
    assert report["first_date_with_data"]["limit_list_d"] == "20200102"
    assert report["first_date_with_data"]["hm_detail"] is None
    assert report["first_date_with_data"]["cyq_perf"] == "20150105"
    member = next(p for p in report["probes"] if p["api"] == "index_member_all")
    assert member["extras"]["out_date_non_null"] == 1
    keys = [(p["api"], json.dumps(p["params"], sort_keys=True)) for p in tushare_probe2.build_probes()]
    assert len(keys) == len(set(keys))


def test_probe2_baostock_absent_is_recorded_not_raised(monkeypatch):
    import builtins

    import tushare_probe2

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "baostock":
            raise ModuleNotFoundError("No module named 'baostock'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = tushare_probe2.probe_baostock()
    assert out["installed"] is False and "baostock" in out["error"]
