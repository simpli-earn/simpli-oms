"""Value-over-time series for the charts.

Strategy history is rebuilt from the strategy's fills (read-only from the shared OMS database,
because the API caps fills at 100-200) and Alpaca price bars:

    value(t) = budget - buys(<= t) + sells(<= t) + sum(qty held at t * last price at or before t)

The last point is the strategy's current equity at the OMS's own prices, so the chart ends on
the same number as the cards. Account history is Alpaca's portfolio history for the paper account.
"""
from __future__ import annotations

import re
import statistics
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from math import sqrt
from zoneinfo import ZoneInfo

import psycopg
from dotenv import dotenv_values

import desk

RANGES = {"1D": timedelta(days=1), "1W": timedelta(days=7), "1M": timedelta(days=31),
          "3M": timedelta(days=92), "1Y": timedelta(days=366), "ALL": None}
OPTION = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _db_url() -> str:
    return dotenv_values(desk.BACKEND / ".env")["OMS_DATABASE_URL"]


def strategy_record(sid: str) -> tuple[dict, list[dict]]:
    """(strategy row, fills oldest first), straight from the shared database (SELECT only)."""
    with psycopg.connect(_db_url(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("select cash_budget, created_at from strategies where id = %s", (sid,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"{sid} is not in the OMS")
        cur.execute("select symbol, side, qty, price, filled_at from fills where strategy_id = %s order by filled_at", (sid,))
        fills = [{"symbol": s, "side": side, "qty": float(q), "price": float(p), "at": at}
                 for s, side, q, p, at in cur.fetchall()]
    return {"budget": float(row[0]), "created_at": row[1]}, fills


def _bars(symbols: list[str], start: datetime, end: datetime, daily: bool) -> dict[str, list[tuple[datetime, float]]]:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    import oms_client

    stocks = sorted({s for s in symbols if not OPTION.match(s)})
    if not stocks:
        return {}
    client = StockHistoricalDataClient(*oms_client.alpaca_keys())
    tf = TimeFrame.Day if daily else TimeFrame(30, TimeFrameUnit.Minute)
    for feed in (DataFeed.SIP, DataFeed.IEX):  # the free plan refuses SIP for the last 15 minutes
        try:
            data = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=stocks, timeframe=tf, start=start,
                                                          end=end, feed=feed, adjustment=Adjustment.RAW)).data
            break
        except Exception:
            data = {}
    return {s: [(b.timestamp, float(b.close)) for b in bars] for s, bars in data.items()}


def _valuer(meta: dict, fills: list[dict], bars: dict):
    """value_at(t): the strategy's cash plus holdings at the last price at or before t."""
    fill_times = [f["at"] for f in fills]
    price_times = {s: [t for t, _ in series] for s, series in bars.items()}

    def value_at(t: datetime) -> float:
        n = bisect_right(fill_times, t)
        cash, held, last_fill = meta["budget"], {}, {}
        for f in fills[:n]:
            mult = 100 if OPTION.match(f["symbol"]) else 1
            amount = f["qty"] * f["price"] * mult
            cash += -amount if f["side"] == "buy" else amount
            held[f["symbol"]] = held.get(f["symbol"], 0.0) + (f["qty"] if f["side"] == "buy" else -f["qty"])
            last_fill[f["symbol"]] = f["price"]
        value = cash
        for sym, qty in held.items():
            if abs(qty) < 1e-9:
                continue
            mult = 100 if OPTION.match(sym) else 1
            i = bisect_right(price_times.get(sym, []), t)
            price = bars[sym][i - 1][1] if i else last_fill[sym]
            value += qty * price * mult
        return value
    return value_at


def strategy_history(sid: str, rng: str = "ALL", current_equity: float | None = None) -> dict:
    meta, fills = strategy_record(sid)
    now = datetime.now(timezone.utc)
    created = meta["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    start = created if RANGES.get(rng) is None else max(created, now - RANGES[rng])
    daily = (now - start) > timedelta(days=10)
    bars = _bars([f["symbol"] for f in fills], start - timedelta(days=5 if daily else 1), now - timedelta(minutes=16), daily)

    # sample on the bar timestamps inside the window, plus every fill time, plus the start
    times = sorted({t for series in bars.values() for t, _ in series if t >= start} | {f["at"] for f in fills if f["at"] >= start} | {start})
    value_at = _valuer(meta, fills, bars)

    points = [[t.isoformat(), round(value_at(t), 2)] for t in times]
    if current_equity is not None:
        points.append([now.isoformat(), round(current_equity, 2)])
    return {"points": points, "baseline": meta["budget"], "baseline_label": "Budget",
            "start": start.isoformat(), "daily": daily, "fills": len(fills)}


# ---------------------------------------------------------------- risk vs an index fund

BENCHMARK = "SPY"
ET = ZoneInfo("America/New_York")
EARLY_DAYS = 60  # fewer trading days than this and every number below is mostly noise

# (green up to, yellow up to, orange up to); anything past the last is red.
# Volatility is graded against SPY's own volatility over the same window, beta on its distance
# from zero (1.0 = moves with the index), and alpha in annual % (positive = beat the index after
# adjusting for beta).
GRADES = {
    "vol_ratio": (1.0, 1.25, 1.6),
    "abs_beta": (0.8, 1.1, 1.5),
}
ALPHA_GRADES = ((3.0, "green"), (0.0, "yellow"), (-3.0, "orange"))
# t-stat of alpha: how sure we are the alpha isn't luck (2+ is the usual bar for "real")
T_GRADES = ((2.0, "green"), (1.0, "yellow"), (0.0, "orange"))


def _grade(value: float, cutoffs: tuple) -> str:
    for cut, color in zip(cutoffs, ("green", "yellow", "orange")):
        if value <= cut:
            return color
    return "red"


def _alpha_grade(alpha: float, t: float | None) -> str:
    """Green or red only when the alpha is statistically distinguishable from zero (|t| >= 2);
    a noisy one is held to yellow or orange, so a lucky week doesn't read as skill."""
    color = next((c for cut, c in ALPHA_GRADES if alpha >= cut), "red")
    if t is None or abs(t) < 2:
        color = {"green": "yellow", "red": "orange"}.get(color, color)
    return color


def strategy_stats(sid: str) -> dict:
    """Annualized volatility, beta and alpha of the strategy against SPY, from its first fill.

    Returns are taken at each daily close once the strategy has 45+ days of history, and at
    30-minute bars during regular hours before that, so a young strategy still gets an estimate.
    Alpha is Jensen's alpha with a 0% risk-free rate (OMS cash earns nothing), annualized by
    simple multiplication: alpha = (mean(r) - beta * mean(r_spy)) * periods per year.
    """
    meta, fills = strategy_record(sid)
    if not fills:
        return {"ready": False, "reason": "No fills yet"}
    now = datetime.now(timezone.utc)
    start = fills[0]["at"]
    daily = (now - start) > timedelta(days=45)
    bars = _bars([f["symbol"] for f in fills] + [BENCHMARK], start - timedelta(days=5), now - timedelta(minutes=16), daily)
    step = timedelta(days=1) if daily else timedelta(minutes=30)

    def regular(t: datetime) -> bool:
        et = t.astimezone(ET)
        return daily or (et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) <= (15, 30))
    # each bar is valued at its close (its start + step), so a fill during the bar counts in it
    grid = [(t, p) for t, p in bars.get(BENCHMARK, []) if t + step > start and regular(t)]
    if len(grid) < 6:
        return {"ready": False, "reason": "Not enough price history yet"}
    value_at = _valuer(meta, fills, bars)
    values = [value_at(t + step - timedelta(seconds=1)) for t, _ in grid]
    r = [b / a - 1 for a, b in zip(values, values[1:]) if a]
    m = [b / a - 1 for (_, a), (_, b) in zip(grid, grid[1:])]
    n = min(len(r), len(m))
    r, m = r[:n], m[:n]
    if statistics.pvariance(m) == 0:
        return {"ready": False, "reason": "Index didn't move in the window"}

    per_year = 252 if daily else 252 * 13
    beta = statistics.covariance(r, m) / statistics.variance(m)
    resid = [a - beta * b for a, b in zip(r, m)]
    alpha = statistics.fmean(resid) * per_year * 100
    se = statistics.stdev(resid) / sqrt(n) if n > 2 else 0
    t = statistics.fmean(resid) / se if se else None
    vol = statistics.stdev(r) * sqrt(per_year) * 100
    spy_vol = statistics.stdev(m) * sqrt(per_year) * 100
    days = len({t.astimezone(ET).date() for t, _ in grid})
    return {
        "ready": True,
        "benchmark": BENCHMARK,
        "since": start.isoformat(),
        "sampling": "daily" if daily else "30-minute",
        "returns": n,
        "trading_days": days,
        "early": days < EARLY_DAYS,
        "volatility": vol, "benchmark_volatility": spy_vol,
        "beta": beta,
        "alpha": alpha,
        "alpha_t": t,
        "grades": {
            "volatility": _grade(vol / spy_vol, GRADES["vol_ratio"]) if spy_vol else "yellow",
            "beta": _grade(abs(beta), GRADES["abs_beta"]),
            "alpha": _alpha_grade(alpha, t),
            "alpha_t": "yellow" if t is None else next((c for cut, c in T_GRADES if t >= cut), "red"),
        },
    }


def account_history(rng: str = "1M") -> dict:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    import oms_client

    period, timeframe = {"1D": ("1D", "5Min"), "1W": ("1W", "1H"), "1M": ("1M", "1D"), "3M": ("3M", "1D"),
                         "1Y": ("1A", "1D"), "ALL": ("1A", "1D")}.get(rng, ("1M", "1D"))
    h = TradingClient(*oms_client.alpaca_keys(), paper=True).get_portfolio_history(
        GetPortfolioHistoryRequest(period=period, timeframe=timeframe, extended_hours=rng in ("1D", "1W")))
    points = [[datetime.fromtimestamp(ts, timezone.utc).isoformat(), round(eq, 2)]
              for ts, eq in zip(h.timestamp, h.equity) if eq]
    return {"points": points, "baseline": float(h.base_value) if h.base_value else None,
            "baseline_label": "Start of period", "daily": timeframe == "1D"}
