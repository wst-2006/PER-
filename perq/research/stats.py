"""统计检验的基础函数。"""
from __future__ import annotations

import warnings

import numpy as np


def newey_west_se(series: np.ndarray, lags: int) -> float:
    """均值的 Newey-West 标准误（Bartlett 核）。"""
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    d = x - x.mean()
    var = float(d @ d) / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2.0 * (1.0 - lag / (lags + 1.0)) * float(d[lag:] @ d[:-lag]) / n
    return float(np.sqrt(var / n)) if var > 0 else float("nan")


def newey_west_tstat(series: np.ndarray, lags: int) -> float:
    """均值的 Newey-West t 值（Bartlett 核）。持有期为 h 的 IC 序列相邻值高度重叠，lags 取 h。"""
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    d = x - x.mean()
    var = float(d @ d) / n
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1.0 - lag / (lags + 1.0)
        var += 2.0 * weight * float(d[lag:] @ d[:-lag]) / n
    if var <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(var / n))


def winsorize_zscore(values: np.ndarray, n_mad: float = 5.0) -> np.ndarray:
    """按列：中位数 ± n_mad 倍 MAD 截尾，再标准化。空值保持为空。"""
    x = np.array(values, dtype=float, copy=True)
    x[~np.isfinite(x)] = np.nan
    with warnings.catch_warnings():                 # 整列为空是正常情况（因子历史不足），不需要警告
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(x, axis=0)
        mad = np.nanmedian(np.abs(x - med), axis=0) * 1.4826
        # 大多数取值相同的离散因子（如"近 20 日涨停次数"多数为 0）MAD 为 0，这时不截尾，
        # 否则所有非零值都会被截成中位数，整列变成常数
        mad = np.where(mad > 0, mad, np.inf)
        lo, hi = med - n_mad * mad, med + n_mad * mad
        x = np.where(x < lo, lo, np.where(x > hi, hi, x))
        mean, std = np.nanmean(x, axis=0), np.nanstd(x, axis=0)
    std = np.where(std > 0, std, np.nan)
    return (x - mean) / std


def rank_columns(x: np.ndarray) -> np.ndarray:
    """按列求平均秩（并列取平均），空值保持为空。"""
    import pandas as pd

    return pd.DataFrame(x).rank(axis=0, method="average").to_numpy()


def column_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a 的每一列与向量 b 的相关系数，各自只用两者都非空的行。"""
    out = np.full(a.shape[1], np.nan)
    for j in range(a.shape[1]):
        mask = np.isfinite(a[:, j]) & np.isfinite(b)
        if mask.sum() >= 30:
            x, y = a[mask, j], b[mask]
            sx, sy = x.std(), y.std()
            if sx > 0 and sy > 0:
                out[j] = float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))
    return out


def neutralize(z: np.ndarray, industry: np.ndarray, size: np.ndarray | None) -> np.ndarray:
    """对行业哑变量（和市值）做截面回归取残差。z 中的空值先按 0（截面均值）参与回归，结果里恢复为空。"""
    codes, inverse = np.unique(industry.astype(str), return_inverse=True)
    design = np.zeros((len(industry), len(codes)))
    design[np.arange(len(industry)), inverse] = 1.0
    if size is not None:
        s = np.where(np.isfinite(size), size, 0.0)
        design = np.column_stack([design, s])
    filled = np.where(np.isfinite(z), z, 0.0)
    beta, *_ = np.linalg.lstsq(design, filled, rcond=None)
    resid = filled - design @ beta
    resid[~np.isfinite(z)] = np.nan
    return resid


def deflated_sharpe(sr: float, n_obs: int, n_trials: int, sr_var_across_trials: float,
                    skew: float = 0.0, kurtosis: float = 3.0) -> dict[str, float]:
    """收缩夏普比率（Bailey 与 López de Prado）。所有夏普比率都按单期（日）计。

    返回 sr0（在 n_trials 次试验里"纯靠运气"能得到的最大夏普的期望）和 dsr（真实夏普大于 sr0 的概率）。
    """
    from math import e, sqrt

    from scipy.stats import norm

    gamma = 0.5772156649
    sr0 = sqrt(max(sr_var_across_trials, 0.0)) * ((1 - gamma) * norm.ppf(1 - 1 / n_trials)
                                                   + gamma * norm.ppf(1 - 1 / (n_trials * e))) if n_trials > 1 else 0.0
    denom = sqrt(max(1 - skew * sr + (kurtosis - 1) / 4 * sr * sr, 1e-12))
    dsr = float(norm.cdf((sr - sr0) * sqrt(n_obs - 1) / denom))
    return {"sr": sr, "sr0": float(sr0), "dsr": dsr, "n_trials": n_trials, "n_obs": n_obs}
