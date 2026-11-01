"""Polymarket: back near-certain favorites
Buys the favorite in busy Polymarket markets that resolve within a few days, paper traded against the live book.

Strategy `poly-favorites` for SimpliEarn Desk. The docstring's first line is the name.

The idea (a baseline to replace): markets a few days from resolving with one side at 85-96 cents
usually resolve that way, and the last few cents are a premium for waiting. Each run buys the
favorite in up to MAX_NEW such markets, a fixed dollar amount each, and holds to resolution.

Backtest it: .venv/bin/python backtest.py poly-favorites --start 2026-10-01 --end 2026-10-31
"""
import hashlib
import os
from datetime import datetime, timedelta

from oms_client import PredictionMarkets

STRATEGY_ID = os.environ.get("SIMPLI_STRATEGY_ID", "poly-favorites")
STARTING_CASH = 1000
PER_MARKET = 50
MAX_NEW = 5
FAVORITE_BAND = (0.85, 0.96)
MAX_DAYS_TO_CLOSE = 3


def signal(markets: list[dict], held: set[str], now: datetime) -> list[tuple[dict, str, float]]:
    """Return (market, outcome, price) to buy, best first."""
    soon = now + timedelta(days=MAX_DAYS_TO_CLOSE)
    picks = []
    for m in markets:
        if m["id"] in held or m["yes_bid"] is None or m["yes_ask"] is None or not m["close_time"]:
            continue
        closes = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        if not now < closes <= soon:
            continue
        if FAVORITE_BAND[0] <= m["yes_ask"] <= FAVORITE_BAND[1]:
            picks.append((m, "yes", m["yes_ask"]))
        elif FAVORITE_BAND[0] <= 1 - m["yes_bid"] <= FAVORITE_BAND[1]:
            picks.append((m, "no", round(1 - m["yes_bid"], 3)))
    return picks


def main() -> None:
    pm = PredictionMarkets(STRATEGY_ID)
    pm.fund(STARTING_CASH)
    held = {p["market_id"] for p in pm.positions()}
    picks = signal(pm.markets("polymarket", limit=200), held, pm.now())[:MAX_NEW]
    if not picks:
        print("No favorites in range right now.")
        return
    for m, outcome, price in picks:
        count = int(PER_MARKET // price)
        print(f"{m['title']}: buying {count} {outcome.upper()} at {price:.3f}")
        tag = hashlib.sha1(m["id"].encode()).hexdigest()[:12]
        pm.buy("polymarket", m["id"], outcome, count, limit=price,
               client_order_id=f"{STRATEGY_ID}-{pm.today():%Y%m%d}-{tag}")


if __name__ == "__main__":
    main()
