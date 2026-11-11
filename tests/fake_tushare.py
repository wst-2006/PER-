"""离线测试用的假 Tushare 客户端。只存在于 tests/，核心代码不得引用。"""
from __future__ import annotations

import pandas as pd


class FakePro:
    def __init__(self, *, deny=(), rate_limit_once=(), token="secret-token-123456"):
        self.deny = set(deny)
        self.rate_limit_once = set(rate_limit_once)
        self.token = token
        self.calls: list[tuple[str, dict]] = []

    def query(self, api, **params):
        self.calls.append((api, params))
        if api in self.rate_limit_once:
            self.rate_limit_once.discard(api)
            raise Exception("抱歉，您每分钟最多访问该接口200次")
        if api in self.deny:
            raise Exception(f"抱歉，您没有访问该接口的权限 token={self.token}")
        handler = getattr(self, f"_{api}", None)
        return handler(**params) if handler else pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20261109"]})

    def _trade_cal(self, **params):
        days = pd.bdate_range("2014-01-01", "2026-11-11").strftime("%Y%m%d")
        frame = pd.DataFrame({"exchange": "SSE", "cal_date": days, "is_open": "1"})
        start, end = params.get("start_date", "00000000"), params.get("end_date", "99999999")
        return frame[(frame.cal_date >= start) & (frame.cal_date <= end)].reset_index(drop=True)

    def _stock_basic(self, **params):
        status = params.get("list_status", "L")
        if status == "L":
            codes = ([f"60{i:04d}.SH" for i in range(40)] + [f"00{i:04d}.SZ" for i in range(40)]
                     + [f"30{i:04d}.SZ" for i in range(40)] + [f"68{i:04d}.SH" for i in range(40)]
                     + [f"83{i:04d}.BJ" for i in range(10)])
            names = [("*ST样本" if i % 37 == 0 else "样本") + str(i) for i in range(len(codes))]
            return pd.DataFrame({"ts_code": codes, "name": names, "list_status": "L",
                                 "list_date": "20100101", "delist_date": None})
        if status == "D":
            return pd.DataFrame({"ts_code": [f"00{i:04d}.SZ" for i in range(900, 912)], "name": "退市样本",
                                 "list_status": "D", "list_date": "20000101",
                                 "delist_date": ["20170101"] * 4 + ["20220601"] * 6 + [None] * 2})
        return pd.DataFrame(columns=["ts_code", "name", "list_status", "list_date", "delist_date"])

    def _income(self, **params):
        return pd.DataFrame({
            "ts_code": ["000001.SZ"] * 3, "end_date": ["20251231", "20251231", "20250930"],
            "ann_date": ["20260320", "20260320", "20251025"], "f_ann_date": ["20260320", "20260425", "20251025"],
            "update_flag": ["0", "1", "0"],
        })

    def _stk_mins(self, **params):
        return pd.DataFrame(columns=["ts_code", "trade_time", "close"])
