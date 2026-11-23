"""稳健性检验（由设计方提供，受保护）：

1. 收缩夏普：在 T010 的 45 种组合设计里挑出最好的那个，扣除"挑选"带来的运气之后还剩多少
2. 风格归因：五种打分方案的默认组合，超额里有多少来自小市值等风格
3. 模型稳定性：用同一设定、换三个随机种子重训排序模型；以及去掉市值与流动性两个特征后的版本
4. 剔除最小 30% 股票之后的组合表现

用法：
    python scripts/run_robustness.py [--data-dir ...] [--task T011] [--threads 8]
只用开发区。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
from perq.engine.market import MarketData  # noqa: E402
from perq.research import attribution, backtest_report, gbm  # noqa: E402
from perq.research.stats import deflated_sharpe  # noqa: E402

SCHEMES = ["prior", "icir", "gbm_reg_w500", "gbm_rank_w500", "gbm_rank_all"]
# 加快的设定：每 4 天取一天训练、200 棵树、每 42 个交易日重训。几个变体之间只差种子或特征，彼此可比
FAST = {"objective": "rank", "window": None, "day_step": 4, "rounds": 200, "retrain_every": 42}
VARIANTS = {
    "rank_all_fast_s42": dict(FAST, seed=42),
    "rank_all_fast_s7": dict(FAST, seed=7),
    "rank_all_fast_s2024": dict(FAST, seed=2024),
    "rank_all_fast_nosize": dict(FAST, seed=42, exclude_features=("ln_mv", "amihud_20")),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--task", default="T011")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--start", default="20160104")
    parser.add_argument("--end", default="20241231")
    parser.add_argument("--grid", default=str(c.REPO_ROOT / "results" / "T010" / "design_grid.csv"))
    parser.add_argument("--skip-models", action="store_true")
    args = parser.parse_args()

    started = c.utc_now()
    root = c.data_root(args.data_dir)
    out = c.REPO_ROOT / "results" / args.task
    market = MarketData.from_curated(root)
    styles = attribution.style_returns(root, args.start, args.end)
    c.write_json(out / "style_returns_summary.json",
                 {"definition": "每个交易日在股票池内按前一日的因子值分五组，取多头一端减空头一端的次日等权收益；"
                                "年化均值 = 日均值 × 244，未计交易成本",
                  "styles": {k: {"factor": col, "long_end": "因子值最大的一组" if sign > 0 else "因子值最小的一组"}
                             for k, (col, sign) in attribution.STYLES.items()},
                  "annual_mean": {k: round(float(v * 244), 4) for k, v in styles.mean().items()},
                  "correlation": styles.corr().round(3).to_dict()})

    def evaluate(scheme: str, **kw):
        rep = backtest_report.run(root, scheme, args.start, args.end, n=30, rebalance=5, market=market,
                                  keep_series=True, **kw)
        series = rep.pop("_series")
        import pandas as pd

        excess = pd.Series(np.array(series["strategy"]) - np.array(series["benchmark"]), index=series["dates"])
        rep["attribution"] = attribution.regress(excess, styles)
        rep["exposures"] = attribution.exposures(root, series["holdings"])
        rep["daily_excess_skew"] = round(float(excess.skew()), 3)
        rep["daily_excess_kurtosis"] = round(float(excess.kurt() + 3), 3)
        return rep, excess

    rows = []

    def record(label: str, rep: dict) -> None:
        a = rep["attribution"]
        rows.append({"label": label, "annual_excess": rep["annual_excess"], "information_ratio": rep["information_ratio"],
                     "max_drawdown": rep["max_drawdown"], "alpha_after_styles": a.get("alpha_annual"),
                     "alpha_t": a.get("alpha_t_nw"), "beta_size_small": a.get("betas", {}).get("size_small"),
                     "r2_styles": a.get("r2"), "size_percentile": rep["exposures"].get("average_percentile", {}).get("ln_mv"),
                     "smallest_quintile_weight": rep["exposures"].get("size_quintile_weight", {}).get("Q1"),
                     "turnover": rep["annual_turnover_two_sided"]})
        print(rows[-1])

    excess_of: dict[str, object] = {}
    for scheme in SCHEMES:
        t = time.time()
        rep, excess = evaluate(scheme)
        rep["seconds"] = round(time.time() - t)
        c.write_json(out / f"attribution_{scheme}.json", rep)
        record(scheme, rep)
        excess_of[scheme] = excess
        rep2, _ = evaluate(scheme, min_size_pct=0.3)
        c.write_json(out / f"attribution_{scheme}_exmicro.json", rep2)
        record(f"{scheme}_剔除最小30%", rep2)

    # 收缩夏普：以 T010 网格的 45 个信息比率为试验集合
    import pandas as pd

    grid = pd.read_csv(args.grid, encoding="utf-8-sig")
    grid = grid[grid["cost"] == "1x"]
    daily_sr = grid["information_ratio"].to_numpy() / np.sqrt(244)
    best = grid.loc[grid["information_ratio"].idxmax()]
    ex = excess_of.get(best["scheme"])
    dsr = deflated_sharpe(float(best["information_ratio"]) / np.sqrt(244), len(ex) if ex is not None else 2200,
                          len(grid), float(daily_sr.var(ddof=1)),
                          float(ex.skew()) if ex is not None else 0.0, float(ex.kurt() + 3) if ex is not None else 3.0)
    dsr["units"] = "sr 与 sr0 都是日频夏普（日均超额收益 ÷ 日标准差），乘以 √244 即为年化信息比率"
    dsr.update({"best": {"scheme": best["scheme"], "n": int(best["n"]), "rebalance": int(best["rebalance"]),
                         "information_ratio": float(best["information_ratio"])}})
    c.write_json(out / "deflated_sharpe.json", dsr)
    print("收缩夏普：", dsr)

    if not args.skip_models:
        for name, spec in VARIANTS.items():
            t = time.time()
            summary = gbm.walk_forward(root, name, start=args.start, end=args.end, threads=args.threads, **spec)
            summary["seconds"] = round(time.time() - t)
            c.write_json(out / f"model_{name}.json", summary)
            print(name, summary["score_ic"], summary["seconds"], "秒")
    for name in VARIANTS:
        if not (root / "scores" / name).exists():
            continue
        rep, _ = evaluate(name)
        c.write_json(out / f"attribution_{name}.json", rep)
        record(name, rep)

    with (out / "robustness_table.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    c.write_run_meta(out, started, {"task": args.task}, name="run_meta_robustness.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
