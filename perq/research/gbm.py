"""梯度提升树的滚动训练（walk-forward）。

时序：
- 每隔 retrain_every 个交易日重训一次。在第 t 日重训时，训练样本的信号日落在
  [t − window, t − horizon − 1]：最后 horizon + 1 天的标签在第 t 日收盘时还没有到期，必须剔除。
- 训练出的模型用来给第 t 日至下一次重训前一日的股票打分。
- window 为 None 时使用全部历史（扩展窗口）。

目标：
- reg   回归每日截面收益百分位（−0.5 到 0.5）
- rank  LambdaRank：把每日截面按收益分成 rank_bins 档作为相关度，直接优化排在最前面的那一段的排序质量。
        只能做多的组合只关心头部，这个目标与此一致。

输出与因子合成方案相同的打分文件（scores/<名称>/），回测代码可以直接使用。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np

from ..features.factors import FACTORS
from .panel import daily_rank_target, load_panel, panel_days
from .stats import column_corr, newey_west_tstat, rank_columns

BASE_PARAMS = {
    "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 500, "feature_fraction": 0.8,
    "bagging_fraction": 0.7, "bagging_freq": 1, "lambda_l2": 10.0, "verbose": -1,
    "deterministic": True, "force_row_wise": True, "seed": 42,
}


def _train(frame: Any, features: list[str], objective: str, horizon: int, rounds: int, rank_bins: int,
           threads: int) -> Any:
    import lightgbm as lgb

    x = frame[features].to_numpy(dtype=np.float32)
    params = dict(BASE_PARAMS, num_threads=threads)
    if objective == "reg":
        params.update(objective="regression")
        data = lgb.Dataset(x, label=daily_rank_target(frame, horizon), free_raw_data=True)
    elif objective == "rank":
        pct = frame[f"y_fwd_{horizon}"].groupby(frame["trade_date"]).rank(pct=True).to_numpy()
        relevance = np.clip(np.ceil(pct * rank_bins) - 1, 0, rank_bins - 1).astype(int)
        groups = frame.groupby("trade_date", sort=False).size().to_numpy()
        params.update(objective="lambdarank", lambdarank_truncation_level=50, eval_at=[50],
                      label_gain=list(range(rank_bins)))
        data = lgb.Dataset(x, label=relevance, group=groups, free_raw_data=True)
    else:
        raise ValueError(f"未知目标：{objective}")
    return lgb.train(params, data, num_boost_round=rounds)


def walk_forward(root: str | Path, name: str, *, objective: str = "reg", horizon: int = 5,
                 window: int | None = 500, retrain_every: int = 21, day_step: int = 2, rounds: int = 300,
                 rank_bins: int = 20, start: str = "20160104", end: str = "20241231",
                 min_train_days: int = 120, threads: int = 4) -> dict[str, Any]:
    import pandas as pd

    root = Path(root)
    features = list(FACTORS)
    days = panel_days(root)
    if not days:
        raise RuntimeError("没有中性化因子面板，请先运行 build_panel")
    target = root / "scores" / name
    if target.exists():
        shutil.rmtree(target)
    pred_days = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    out_by_year: dict[str, list[Any]] = {}
    importance = np.zeros(len(features))
    retrains: list[dict[str, Any]] = []
    score_ic: list[float] = []

    for block_start in range(0, len(pred_days), retrain_every):
        block = pred_days[block_start:block_start + retrain_every]
        t = index[block[0]]
        last_train = t - horizon - 1
        first_train = 0 if window is None else max(0, t - window)
        train_days = days[first_train:last_train + 1][::-1][::day_step][::-1]   # 从最近一天往回每隔 day_step 天取一天
        if len(train_days) < min_train_days // day_step:
            continue
        train = load_panel(root, train_days[0], train_days[-1])
        train = train[train["trade_date"].isin(set(train_days)) & train[f"y_fwd_{horizon}"].notna()]
        model = _train(train, features, objective, horizon, rounds, rank_bins, threads)
        importance += model.feature_importance("gain") / max(model.feature_importance("gain").sum(), 1e-12)
        retrains.append({"date": block[0], "train_first": train_days[0], "train_last": train_days[-1],
                         "train_days": len(train_days), "train_rows": int(len(train))})
        test = load_panel(root, block[0], block[-1])
        if len(test) == 0:
            continue
        test["score"] = model.predict(test[features].to_numpy(dtype=np.float32), num_threads=threads).astype("float32")
        for day, part in test.groupby("trade_date", sort=True):
            y = part[f"y_fwd_{horizon}"].to_numpy(dtype=float)
            if np.isfinite(y).sum() >= 50:
                ry = rank_columns(y.reshape(-1, 1)).ravel()
                score_ic.append(float(column_corr(rank_columns(part[["score"]].to_numpy(dtype=float)), ry)[0]))
            else:
                score_ic.append(float("nan"))
        for year, part in test.groupby(test["trade_date"].str[:4]):
            out_by_year.setdefault(year, []).append(part[["ts_code", "trade_date", "score"]])

    for year, parts in out_by_year.items():
        folder = target / f"year={year}"
        folder.mkdir(parents=True, exist_ok=True)
        pd.concat(parts, ignore_index=True).to_parquet(folder / "part-0.parquet", index=False)
    x = np.array(score_ic, dtype=float)
    ok = x[np.isfinite(x)]
    total = importance.sum()
    return {
        "name": name, "objective": objective, "horizon": horizon, "window": window, "retrain_every": retrain_every,
        "day_step": day_step, "rounds": rounds, "retrains": len(retrains), "retrain_log": retrains,
        "score_ic": {"mean": round(float(ok.mean()), 5) if len(ok) else None,
                     "icir": round(float(ok.mean() / ok.std()), 4) if len(ok) > 1 and ok.std() > 0 else None,
                     "t_nw": round(newey_west_tstat(x, horizon), 2) if len(ok) > 2 else None,
                     "positive_share": round(float((ok > 0).mean()), 4) if len(ok) else None},
        "feature_importance_gain_share": dict(sorted(
            {f: round(float(v / total), 4) for f, v in zip(features, importance)}.items(), key=lambda kv: -kv[1]))
        if total else {},
    }
