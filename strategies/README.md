# Strategies

One folder per strategy, named after its OMS strategy id. SimpliEarn Desk (`../desktop`) lists, runs, versions, and schedules them. The **Strategy guide** tab in the app is the full reference for writing one.

```
strategies/
  oms_client.py          the one import a strategy needs
  templates/             new_strategy.py and new_strategy.ipynb, used by "Add strategy"
  zscore-spy/
    strategy.json        where the code lives, saved versions, which version schedules run
    zscore_spy.ipynb     the code (stored here); a linked strategy points at a file elsewhere
    models/              anything the code reads with a relative path
    versions/            snapshots you saved (v1.ipynb, v2.ipynb, ...)
    runs/                executed notebooks from past runs
```

## The contract

A strategy is one `.py` or `.ipynb` file that creates `OMS()`, decides target weights, and calls `oms.rebalance(weights, prices)`:

```python
from oms_client import OMS, latest_prices

oms = OMS()                       # strategy id + dry run come from the app
if not oms.market_is_open():
    oms.plan_only("Market is closed")
target = {"SPY": 0.5, "QQQ": 0.5}
oms.rebalance(target, latest_prices(set(target) | set(oms.holdings())))
```

The app runs the file from its own folder with this folder's `.venv`, and sets `SIMPLI_STRATEGY_ID`, `SIMPLI_VERSION`, and (for dry runs) `OMS_DRY_RUN=1`. Printed output is the run log, and an uncaught exception marks the run failed. Outside the app, pass the id yourself: `OMS("your-id")`.

`oms_client` also has `buy`, `sell`, `order` (limit, options, multi-leg), `holdings`, `book`, `cancel`, `daily_id`, and `alpaca_keys` for market data.

For Kalshi and Polymarket, use `PredictionMarkets()` instead of `OMS()`: `fund`, `markets`, `mentions`, `market`, `buy`, `sell`, `positions`, `account`. Orders are paper fills against the live order books. Two examples live in `templates/`: `kalshi_mentions.py` (Kalshi mention markets) and `poly_favorites.py` (Polymarket). Copy one into a strategy folder and change its `signal()`.

## Backtesting prediction strategies

A `PredictionMarkets` strategy can be replayed on historical prices without changes: click **Backtest** on its card in SimpliEarn Desk, or run

```bash
.venv/bin/python backtest.py kalshi-mentions --start 2026-10-01 --end 2026-10-31
```

The strategy runs every hour (`--step-hours`) and only sees data from before that moment, so use `pm.now()` and `pm.today()` instead of the system clock. Kalshi uses hourly bid/ask candles, with fills capped at 20% of each hour's volume (`--fill-cap`) and Kalshi's taker fee. Polymarket has one hourly price, so bid/ask is that price -/+ 1 cent (`--poly-half-spread`). Positions settle on each market's real result. By default the Kalshi universe is every Mentions series (`--series` to narrow it) and the Polymarket universe is the 200 busiest markets in the window (`--poly-markets`, `--poly-query`). Picking the busiest markets uses volume from the whole window, which flatters Polymarket results a little. History is cached in `strategies/.cache/`; results go to `<strategy>/backtests/<timestamp>/` (summary.json, equity.csv, trades.csv, log.txt). Run the engine's tests with `.venv/bin/python -m pytest tests`.

## Adding one

In the app, **Add strategy** → pick the OMS strategy id → then choose one:
- **Start from template**: a `.py` or `.ipynb` copied into the folder.
- **Link a file**: run a file where it already lives. Every run reads it fresh.
- **Upload a copy**: copy a file into the folder.

## Versions

An uploaded or linked strategy starts with its code saved as `v1`, used for scheduled runs; the first version you save on any strategy is used for scheduled runs if none is. After that nothing is versioned automatically. In the app, **Versions** → **Save version** with a label and name. Saved versions can be compared with the working code, run, restored, or pinned for scheduled runs.

## Setup (once)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ipykernel install --user --name simpli-oms-strategies --display-name "Simpli OMS strategies"
```

`../desktop/build_app.sh` also adds a `.pth` file so `import oms_client` works from any folder.

## Not in git

Strategy folders are personal, so git ignores everything in `strategies/` except `oms_client.py`, `templates/`, this README, and `requirements.txt`. SimpliEarn Desk backs up each strategy's scheduled version to the shared OMS database instead (see **Cloud backup** in the app's Strategy guide), and any laptop can download it from **In the cloud** on My strategies.

xgboost-based strategies need OpenMP on macOS: `brew install libomp` (`install.command` handles this).
