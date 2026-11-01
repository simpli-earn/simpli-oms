"""Train the pooled Z-score model from ai-stock-insights and save it for zscore_spy.ipynb.

Same data, features, and hyperparameters as `insights.zscore.fit_global`: trained on 1998-2020
only, so it is the frozen model behind the backtest (pooled top 20: 43.9% CAGR, 2024 -> Oct 2026).

Run with the ai-stock-insights environment, which has the price panel:

    ../../ai-stock-insights/.venv/bin/python train_zscore_pooled.py
"""
import json
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSIGHTS = HERE.parent.parent / "ai-stock-insights"
sys.path.insert(0, str(INSIGHTS))

import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402

from insights import config, data, zscore  # noqa: E402

OUT = HERE / "zscore-spy" / "models"


def main() -> None:
    # The backtest ranked only the tickers that had a per-stock model (enough pre-2021 history).
    per_stock = pickle.loads((config.DATA_DIR / "zscore_models.pkl").read_bytes())
    tickers = sorted(r["ticker"] for r in per_stock)
    panel = data.load_panel([])  # cached prices.pkl

    spy_rsi = zscore.rsi(panel[config.MARKET_SYMBOL]["adj"])
    frames = [zscore._purge(zscore.features(panel[t], spy_rsi).dropna(subset=zscore.FEATURES)) for t in tickers]
    allf = pd.concat(frames)
    tr = allf[(allf["split"] == "train") & allf["y"].notna()]
    print(f"training on {len(tr):,} rows from {len(tickers)} tickers, {tr.index.min():%Y-%m-%d} to {tr.index.max():%Y-%m-%d}")

    m = xgb.XGBRegressor(max_depth=3, n_estimators=300, learning_rate=0.03, min_child_weight=200, subsample=0.8,
                         reg_lambda=5.0, tree_method="hist", n_jobs=-1, random_state=0)
    m.fit(tr[zscore.FEATURES], tr["y"])

    OUT.mkdir(exist_ok=True)
    m.get_booster().save_model(OUT / "zscore_pooled.json")
    (OUT / "zscore_universe.json").write_text(json.dumps({
        "features": zscore.FEATURES,
        "horizon": config.HORIZON,
        "top_n": 20,
        "trained_through": config.TRAIN_END,
        "tickers": tickers,
    }, indent=1))
    print(f"saved {OUT / 'zscore_pooled.json'} and zscore_universe.json")


if __name__ == "__main__":
    main()
