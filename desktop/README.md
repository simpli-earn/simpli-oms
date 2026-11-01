# SimpliEarn Desk

A Mac app for running the strategy notebooks in `../strategies` against the OMS.

- **Start OMS / Stop OMS** (bottom of the sidebar) starts the backend (port 8787) and the web dashboard (port 5173).
- **My strategies** lists every strategy folder in `strategies/` (see `strategies/README.md`). Each card shows the code source (stored, uploaded, or a linked file), the code checks, the version state, equity, cash, today's and total % change, the last run, and the schedule. **Add strategy** picks an OMS strategy id from a dropdown, then starts from a template, links a file, or uploads a copy.
- **Statistics** on each strategy page: annualized volatility, beta, and alpha next to SPY (an S&P 500 index fund) over the same window, colored green / yellow / orange / red (the ? button shows what counts as good or bad), plus the t-stat of alpha. For its first 45 days it uses 30-minute returns (daily after that), and under 60 trading days it is marked an early estimate. Alpha only turns green or red when its t-stat is at least 2.
- **Versions** saves the current code as a version you name (an uploaded or linked file starts as v1; the first version you save is used for scheduled runs until you pick another), compares versions with the working code, runs or restores a version, and pins one for scheduled runs. Run history shows which version each run used.
- **Strategy guide** documents what strategy code must do and how the app runs it.
- **Predictions** browses Kalshi (including mention markets) and Polymarket, and paper trades them against the live order books (see the main README).
- **Backtest** (on the card of any strategy that uses `PredictionMarkets`) replays historical Kalshi and Polymarket prices through the strategy and shows return, drawdown, win rate, and an equity curve (see `strategies/README.md`).
- **Account**, **Strategies**, and **New order** match the web dashboard's pages, plus today's and total % change for every strategy and position. Open orders on a strategy page have a Cancel button.
- **Run now** executes the notebook for real. **Dry run** prints the orders without sending them. Click a running card or any history row for the live log.
- **Schedule** runs a notebook automatically at a set time on chosen days. Schedules are macOS LaunchAgents, so they fire even when the app is closed. If the laptop is asleep at that time, the run happens once on wake. If it is shut down, that day is missed.
- **Cloud backup** uploads only the code that runs: the version picked with **Use for schedule** (plus `strategy.json`, `models/`, and the schedule's time and days). Drafts and other saved versions stay local, and a strategy with no scheduled version isn't backed up. It uploads to the shared OMS database while the app is open and the OMS is running. Anyone on the team can download any strategy from **In the cloud** on My strategies, so deleting the app or the folder doesn't lose code. A card offers **Update** when another laptop changed the strategy, and **Keep mine** / **Use cloud's** when both did. `runs/`, run history, and files over 10 MB stay local. Needs the tables in `backend/add_strategy_code.sql` (run once by a Supabase project owner).
- **Run history** records every run, whether manual or scheduled, with status, a result summary, and how long it took. The executed notebook is saved to `strategies/runs/` and its text output to `data/logs/`.

A new strategy only needs a notebook in `strategies/` with a `STRATEGY_ID = "..."` line. It appears in the app automatically.

## Install

```bash
./build_app.sh
```

This builds `~/Applications/SimpliEarn Desk.app`, using `strategies/.venv` (see `strategies/README.md`). Re-run it if you move this folder or change your Node install.

## Statuses

| Status | Meaning |
|---|---|
| succeeded | Notebook finished. The result says how many orders were sent. |
| plan only | Market was closed, so the notebook only printed its plan (zscore-spy does this). |
| failed | A cell raised an error. Open the log for the traceback. |
| skipped | Another run of the same strategy was still going. |
| stopped / interrupted | Stopped from the app, or the process died (for example, the laptop shut down mid-run). |

## Files

- `app.py`: the window and the calls behind its buttons
- `runner.py`: runs one notebook and records the result. Both the app and launchd call it.
- `cloud.py`: uploads, downloads, and tracks the cloud copy of each strategy
- `scheduler.py`: writes and loads the LaunchAgents (`~/Library/LaunchAgents/com.simpliearn.desk.*.plist`)
- `desk.py`: paths, run history (`data/desk.db`), and strategy discovery
- `ui/index.html`: the interface

To remove a schedule, turn off "Run automatically" in the app, or delete its plist and run `launchctl bootout gui/$(id -u)/<label>`.
