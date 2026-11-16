"""因子合成：把多个因子合成一个打分。所有方案都只用到当时已经知道的信息。

方案：
- prior     按先验方向等权（先验方向为 0 的因子不参与）。先验方向在看到任何结果之前就已登记在因子库里。
- ic        权重 = 过去 window 个交易日里、已经到期的每日 Rank IC 的均值。
- icir      权重 = 同一段 IC 的均值 ÷ 标准差。
后两者就是"按实盘表现自动调整因子权重"的最简单形式，作为以后更复杂方法的对照。

到期：第 d 日的 IC 要用到 d+1 至 d+1+h 的价格，所以最早在第 d+h+1 个交易日收盘后才知道。
到期之前的 IC 一律不用。历史不足 min_history 天时退回 prior 方案。
"""
from __future__ import annotations

import shutil
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from ..features.factors import FACTORS, VERSION
from .evaluate import load_year, prepare_day, year_dirs
from .stats import column_corr, newey_west_tstat, rank_columns

SCHEMES = ("prior", "ic", "icir")


def prior_weights(names: list[str]) -> np.ndarray:
    w = np.array([float(FACTORS[n][2]) for n in names])
    total = np.abs(w).sum()
    return w / total if total else w


def build_scores(root: str | Path, *, horizon: int = 5, window: int = 250, min_history: int = 60,
                 min_coverage: float = 0.5, min_stocks: int = 200, start: str | None = None,
                 end: str | None = None, version: str = VERSION) -> dict[str, Any]:
    """逐日计算三种方案的打分，写入 <数据目录>/scores/<方案>/year=YYYY/，并返回每种方案的打分 IC 摘要。"""
    import pandas as pd

    root = Path(root)
    names = list(FACTORS)
    prior = prior_weights(names)
    matured: deque[np.ndarray] = deque(maxlen=window)        # 已到期的每日因子 IC
    waiting: deque[tuple[int, np.ndarray]] = deque()          # （可用的日序号，IC）
    out_dirs = {s: root / "scores" / s for s in SCHEMES}
    for folder in out_dirs.values():
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    score_ic: dict[str, list[float]] = {s: [] for s in SCHEMES}
    weight_log: list[dict[str, Any]] = []
    day_index = -1

    for year in year_dirs(root / "features" / version):
        frame = load_year(root, year, (horizon,), version)
        if frame is None or len(frame) == 0:
            continue
        rows: dict[str, list[Any]] = {s: [] for s in SCHEMES}
        for day, part in frame.groupby("trade_date", sort=True):
            if (start and day < start) or (end and day > end) or len(part) < min_stocks:
                continue
            day_index += 1
            while waiting and waiting[0][0] <= day_index:
                matured.append(waiting.popleft()[1])

            _z, neu = prepare_day(part, names)
            present = np.isfinite(neu)
            filled = np.where(present, neu, 0.0)
            enough = present.mean(axis=1) >= min_coverage

            weights = {"prior": prior}
            if len(matured) >= min_history:
                hist = np.array(matured)
                mean = np.nanmean(hist, axis=0)
                std = np.nanstd(hist, axis=0)
                for scheme, w in (("ic", mean), ("icir", np.where(std > 0, mean / std, 0.0))):
                    w = np.nan_to_num(w)
                    total = np.abs(w).sum()
                    weights[scheme] = w / total if total else prior
            else:
                weights["ic"] = weights["icir"] = prior
            if day_index % 20 == 0:
                weight_log.append({"date": day, "matured_days": len(matured),
                                   **{s: dict(zip(names, np.round(weights[s], 4).tolist())) for s in ("ic", "icir")}})

            y = part[f"fwd_{horizon}"].to_numpy(dtype=float)
            ry = rank_columns(np.where(np.isfinite(y), y, np.nan).reshape(-1, 1)).ravel()
            codes = part["ts_code"].to_numpy()
            for scheme in SCHEMES:
                score = np.where(enough, filled @ weights[scheme], np.nan)
                rows[scheme].append(pd.DataFrame({"ts_code": codes, "trade_date": day, "score": score.astype("float32")}))
                ic = column_corr(rank_columns(score.reshape(-1, 1)), ry)[0] if np.isfinite(y).sum() >= min_stocks else np.nan
                score_ic[scheme].append(ic)

            # 今天的因子 IC，在第 day_index + horizon + 1 天收盘后才可用
            if np.isfinite(y).sum() >= min_stocks:
                waiting.append((day_index + horizon + 1, column_corr(rank_columns(neu), ry)))

        for scheme in SCHEMES:
            if rows[scheme]:
                target = out_dirs[scheme] / f"year={year}"
                target.mkdir(parents=True, exist_ok=True)
                pd.concat(rows[scheme], ignore_index=True).to_parquet(target / "part-0.parquet", index=False)

    summary: dict[str, Any] = {"horizon": horizon, "window": window, "min_history": min_history, "days": day_index + 1,
                               "schemes": {}, "weights_sampled_every_20_days": weight_log}
    for scheme, series in score_ic.items():
        x = np.array(series, dtype=float)
        ok = x[np.isfinite(x)]
        summary["schemes"][scheme] = {
            "ic_mean": round(float(ok.mean()), 5) if len(ok) else None,
            "icir": round(float(ok.mean() / ok.std()), 4) if len(ok) > 1 and ok.std() > 0 else None,
            "t_nw": round(newey_west_tstat(x, horizon), 2) if len(ok) > 2 else None,
            "positive_share": round(float((ok > 0).mean()), 4) if len(ok) else None,
        }
    return summary


class ScoreBook:
    """按年懒加载打分，day(date) 返回以代码为索引的打分序列。"""

    def __init__(self, root: str | Path, scheme: str) -> None:
        self.base = Path(root) / "scores" / scheme
        self._year: str | None = None
        self._by_day: dict[str, Any] = {}

    def day(self, date: str) -> Any:
        import pandas as pd

        if date[:4] != self._year:
            self._year, self._by_day = date[:4], {}
            folder = self.base / f"year={date[:4]}"
            if folder.is_dir():
                frame = pd.concat([pd.read_parquet(f) for f in sorted(folder.glob("*.parquet"))], ignore_index=True)
                self._by_day = {d: g.set_index("ts_code")["score"] for d, g in frame.groupby("trade_date")}
        return self._by_day.get(date)
