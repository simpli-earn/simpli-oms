"""Kalshi mention markets: fade long shots
Buys NO on liquid "What will X say" markets where YES trades as a cheap long shot, paper traded against Kalshi's live book.

Strategy `kalshi-mentions` for SimpliEarn Desk. The docstring's first line is the name.

The idea (a baseline to replace with your own signal): cheap YES contracts tend to be overpriced
(the long-shot bias), so NO at 85-97 cents wins more often than its price implies. Each run:
  1. lists open Kalshi mention markets,
  2. keeps the liquid ones whose event is close and whose YES ask is in the long-shot band,
  3. buys NO with a limit at the current NO ask, a fixed dollar amount per market,
  4. skips markets it already holds. Positions settle on their own when Kalshi resolves them.

Change signal() to try another rule. Everything prints to the run log.
Backtest it: .venv/bin/python backtest.py kalshi-mentions --start 2026-10-01 --end 2026-10-31
"""
import hashlib
import os
from datetime import datetime, timedelta

from oms_client import PredictionMarkets

STRATEGY_ID = os.environ.get("SIMPLI_STRATEGY_ID", "kalshi-mentions")
STARTING_CASH = 1000      # prediction paper cash, funded on the first run
PER_MARKET = 25           # dollars of NO per market
MAX_NEW = 10              # new positions per run
YES_ASK_BAND = (0.03, 0.15)
MIN_VOLUME_24H = 500      # contracts traded in the last day
MAX_DAYS_TO_CLOSE = 16    # mention markets close up to two weeks after the event
MAX_SPREAD = 0.04


def signal(markets: list[dict], held: set[str], now: datetime) -> list[dict]:
    """Return the markets to buy NO in, best first."""
    soon = now + timedelta(days=MAX_DAYS_TO_CLOSE)
    picks = []
    for m in markets:
        bid, ask = m["yes_bid"], m["yes_ask"]
        if m["id"] in held or bid is None or ask is None:
            continue
        if "does not qualify" in (m["subtitle"] or "").lower():
            continue  # a different bet: whether the event happens at all
        closes = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) if m["close_time"] else None
        if closes is None or closes > soon:
            continue
        if not (YES_ASK_BAND[0] <= ask <= YES_ASK_BAND[1]) or ask - bid > MAX_SPREAD:
            continue
        if (m["volume_24h"] or 0) < MIN_VOLUME_24H:
            continue
        picks.append(m)
    return sorted(picks, key=lambda m: -(m["volume_24h"] or 0))


def main() -> None:
    pm = PredictionMarkets(STRATEGY_ID)
    pm.fund(STARTING_CASH)
    account = pm.account()
    if account:
        a = account["account"]
        print(f"Cash ${a['cash']:,.2f} · positions ${a['positions_value']:,.2f} · total P&L ${a['total_pnl']:+,.2f}")
    held = {p["market_id"] for p in pm.positions()}
    open_ids = {o["market_id"] for o in (account or {}).get("orders", []) if o["status"] == "open"}

    markets = pm.mentions()
    print(f"{len(markets)} open mention markets")
    picks = signal(markets, held | open_ids, pm.now())[:MAX_NEW]
    if not picks:
        print("No markets pass the filters today.")
        return
    for m in picks:
        fresh = pm.market("kalshi", m["id"])  # the scan can be minutes old; price off the live book
        if fresh["yes_bid"] is None or not (YES_ASK_BAND[0] <= (fresh["yes_ask"] or 0) <= YES_ASK_BAND[1]):
            print(f"{m['id']}: moved out of the band, skipping")
            continue
        m = {**m, **{k: fresh[k] for k in ("yes_bid", "yes_ask")}}
        no_price = round(1 - m["yes_bid"], 2)  # NO ask = 1 - YES bid
        count = int(PER_MARKET // no_price)
        print(f"{m['title']} · {m['subtitle']}: YES {m['yes_bid']:.2f}/{m['yes_ask']:.2f}, buying {count} NO at {no_price:.2f}")
        # Same id all day, so re-running the strategy never doubles an order.
        tag = hashlib.sha1(m["id"].encode()).hexdigest()[:12]
        pm.buy("kalshi", m["id"], "no", count, limit=no_price, client_order_id=f"{STRATEGY_ID}-{pm.today():%Y%m%d}-{tag}")


if __name__ == "__main__":
    main()
