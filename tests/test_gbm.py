"""滚动训练测试：剔除未到期标签、不偷看未来、能学到埋入的信号。"""
from __future__ import annotations

import pandas as pd
import pytest

from perq.research import composite, gbm, panel
from test_factors import DAYS, make_market

FAST = dict(window=None, retrain_every=21, day_step=1, rounds=40, min_train_days=60, start=DAYS[150], end=DAYS[-1], threads=2)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root = tmp_path_factory.mktemp("gbm")
    make_market(root)
    info = panel.build_panel(root, min_stocks=50)
    return root, info


def test_panel_holds_neutralized_factors_and_labels(planted):
    root, info = planted
    frame = panel.load_panel(root, DAYS[100], DAYS[110])
    assert info["rows"] > 0 and {"rev_5", "y_fwd_5", "y_fwd_20", "sw_l1"} <= set(frame.columns)
    daily_mean = frame.groupby("trade_date")["rev_5"].mean().abs().max()
    assert daily_mean < 1e-4                           # 中性化后每日截面均值为 0
    target = panel.daily_rank_target(frame, 5)
    assert -0.5 <= target.min() and target.max() <= 0.5


@pytest.mark.parametrize("objective", ["reg", "rank"])
def test_walk_forward_learns_planted_signal(planted, objective):
    root, _ = planted
    summary = gbm.walk_forward(root, f"t_{objective}", objective=objective, horizon=5, **FAST)
    assert summary["retrains"] >= 8
    assert summary["score_ic"]["mean"] > 0.02 and summary["score_ic"]["t_nw"] > 2
    top = list(summary["feature_importance_gain_share"])[:6]
    assert {"rev_5", "bias_20", "intraday_20", "ln_mv"} & set(top)            # 两个埋入的效应之一排在前面
    book = composite.ScoreBook(root, f"t_{objective}")
    assert book.day(DAYS[200]) is not None and book.day(DAYS[100]) is None    # 只给预测区间打分


def test_training_window_excludes_unmatured_labels(planted):
    root, _ = planted
    days = panel.panel_days(root)
    pos = {d: i for i, d in enumerate(days)}
    summary = gbm.walk_forward(root, "t_purge", objective="reg", horizon=5, **dict(FAST, window=80, day_step=2))
    for r in summary["retrain_log"]:
        assert pos[r["date"]] - pos[r["train_last"]] == 6          # 最后 5 + 1 天被剔除
        assert pos[r["date"]] - pos[r["train_first"]] <= 80


def test_model_scores_do_not_use_future_data(tmp_path):
    cut = DAYS[-60]
    a, b = tmp_path / "a", tmp_path / "b"
    for root, tweak in ((a, None), (b, cut)):
        make_market(root, tweak_after=tweak)
        panel.build_panel(root, min_stocks=50)
        gbm.walk_forward(root, "m", objective="reg", horizon=5, **FAST)
    ba, bb = composite.ScoreBook(a, "m"), composite.ScoreBook(b, "m")
    checked = 0
    for day in DAYS:
        if day > cut:
            break
        x, y = ba.day(day), bb.day(day)
        if x is None:
            continue
        pd.testing.assert_series_equal(x.sort_index(), y.sort_index())
        checked += 1
    assert checked > 100
