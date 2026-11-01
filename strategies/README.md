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

## zscore-spy

The pooled Z-score top 20 from `ai-stock-insights`, rebalanced daily. Run it between 3:30 and 3:55 PM ET; with the market closed it only prints the plan. `zscore-spy/models/` holds the frozen model; rebuild it with `../../ai-stock-insights/.venv/bin/python train_zscore_pooled.py`. xgboost needs OpenMP on macOS: `brew install libomp`, or on this machine, point xgboost at scikit-learn's bundled copy:
`install_name_tool -add_rpath @loader_path/../../sklearn/.dylibs .venv/lib/python3.13/site-packages/xgboost/lib/libxgboost.dylib && codesign --force -s - <same path>`
