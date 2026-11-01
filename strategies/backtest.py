"""Backtest a Kalshi / Polymarket strategy on historical prices.

    .venv/bin/python backtest.py kalshi-mentions --start 2026-10-01 --end 2026-10-31
    .venv/bin/python backtest.py my-poly-idea --start 2026-09-01 --end 2026-10-01 --venue polymarket

The strategy file runs unchanged: while a backtest is going, PredictionMarkets() answers from history
at a simulated clock instead of the live OMS. It runs once per step (hourly by default) and only sees
data from before that moment. Use pm.now() and pm.today() instead of the system clock.

How fills work (no historical order books exist for either venue, so this is a model):
  * Kalshi: hourly candles with the YES bid and ask. An order trades at the last completed hour's
    bid/ask, up to --fill-cap of that hour's traded volume. A resting limit fills when the next
    hour's ask (or bid) reaches it. Kalshi's taker fee applies to immediate fills.
  * Polymarket: one hourly price. Bid and ask are that price -/+ --poly-half-spread, and an order
    fills up to --poly-max contracts per step. No fees.
  * Positions pay $1 per winning contract when the market settles on its real result.

Results go to <strategy>/backtests/<timestamp>/ (summary.json, equity.csv, trades.csv).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
import os
import runpy
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import oms_client

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache" / "backtest"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HOUR = 3600
KALSHI_TAKER_RATE = 0.07


def ts(value: str | None) -> int | None:
    if not value:
        return None
    value = value.replace(" ", "T").replace("Z", "+00:00")
    if value.endswith("+00"):
        value += ":00"
    return int(datetime.fromisoformat(value).timestamp())


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kalshi_fee(fills: list[tuple[float, float]]) -> float:
    raw = sum(KALSHI_TAKER_RATE * size * price * (1 - price) for price, size in fills)
    return math.ceil(raw * 100 - 1e-7) / 100 if fills else 0.0


# ---------------------------------------------------------------- history download + cache

class History:
    def __init__(self, delay: float = 0.12, quiet: bool = False):
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "simpli-oms-backtest"
        self.delay = delay
        self.quiet = quiet

    def _get(self, url: str, **params):
        for attempt in range(5):
            r = self.http.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            return r.json()
        r.raise_for_status()

    def _cached(self, name: str, load, keep: bool = True):
        path = CACHE / name
        if path.exists():
            return json.loads(path.read_text())
        data = load()
        if keep:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return data

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr, flush=True)

    # Kalshi

    def mention_series(self) -> list[str]:
        return self._cached("kalshi/series-mentions.json",
                            lambda: [s["ticker"] for s in self._get(f"{KALSHI}/series", category="Mentions")["series"]],
                            keep=True)

    def kalshi_markets(self, series: str, start: int, end: int) -> list[dict]:
        """Markets in a series that closed in [start, end + 30 days]. Settled windows never change, so they are cached."""
        def load():
            rows, cursor = [], ""
            while True:
                params = {"series_ticker": series, "min_close_ts": start, "max_close_ts": end + 30 * 86400, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                data = self._get(f"{KALSHI}/markets", **params)
                rows += data.get("markets") or []
                cursor = data.get("cursor")
                if not cursor:
                    return rows
        settled = end + 30 * 86400 < time.time()
        return self._cached(f"kalshi/markets/{series}-{start}-{end}.json", load, keep=settled)

    def kalshi_candles(self, series: str, ticker: str, start: int, end: int) -> list[dict]:
        def load():
            out, a = [], start
            while a < end:
                b = min(end, a + 4000 * HOUR)
                data = self._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks",
                                 start_ts=a, end_ts=b, period_interval=60)
                out += data.get("candlesticks") or []
                a = b
            return out
        return self._cached(f"kalshi/candles/{ticker}.json", load, keep=end < time.time())

    # Polymarket

    def poly_markets(self, start: int, end: int, limit: int, query: str = "") -> list[dict]:
        def load():
            rows = []
            for closed in ("true", "false"):
                params = {"closed": closed, "limit": min(limit, 500), "order": "volumeNum", "ascending": "false",
                          "start_date_max": iso(end), "end_date_min": iso(start)}
                rows += self._get(f"{GAMMA}/markets", **params)
            if query:
                q = query.lower()
                rows = [m for m in rows if q in (m.get("question") or "").lower()]
            rows = [m for m in rows if m.get("clobTokenIds")]
            rows.sort(key=lambda m: -float(m.get("volumeNum") or 0))
            return rows[:limit]
        key = hashlib.sha1(f"{start}-{end}-{limit}-{query}".encode()).hexdigest()[:12]
        return self._cached(f"poly/markets-{key}.json", load, keep=end < time.time())

    def poly_history(self, token: str, start: int, end: int) -> list[dict]:
        def load():
            out, a = [], start
            while a < end:
                b = min(end, a + 14 * 86400)
                data = self._get(f"{CLOB}/prices-history", market=token, startTs=a, endTs=b, fidelity=60)
                out += data.get("history") or []
                a = b
            return out
        name = hashlib.sha1(f"{token}-{start}-{end}".encode()).hexdigest()[:16]
        return self._cached(f"poly/history/{name}.json", load, keep=end < time.time())


# ---------------------------------------------------------------- market replay

@dataclass
class Market:
    venue: str
    id: str
    title: str
    subtitle: str
    event: str
    open_ts: int
    close_ts: int            # when trading actually stopped
    shown_close: str | None  # the scheduled close a live strategy would have seen
    result: str | None       # yes / no once settled; hidden from the strategy until close_ts
    series: str = ""
    token: str = ""
    url: str | None = None
    _bars: list | None = None   # [(end_ts, bid, ask, bid_low, bid_high, ask_low, ask_high, volume)]

    def is_open(self, t: int) -> bool:
        return self.open_ts <= t < self.close_ts


class Replay:
    """Historical markets for one backtest window, loaded lazily and cached on disk."""

    def __init__(self, hist: History, start: int, end: int, opts):
        self.hist, self.start, self.end, self.opts = hist, start, end, opts
        self._kalshi: list[Market] | None = None
        self._poly: list[Market] | None = None

    def kalshi(self) -> list[Market]:
        if self._kalshi is None:
            series = self.opts.series or self.hist.mention_series()
            self.hist.log(f"Loading Kalshi markets from {len(series)} series…")
            out = []
            for i, s in enumerate(series, 1):
                for m in self.hist.kalshi_markets(s, self.start, self.end):
                    open_ts, close_ts = ts(m.get("open_time")), ts(m.get("close_time"))
                    if not open_ts or not close_ts or open_ts > self.end or close_ts < self.start:
                        continue
                    result = m.get("result") if m.get("result") in ("yes", "no") else None
                    title = m.get("title") or m["ticker"]
                    sub = m.get("yes_sub_title") or ""
                    out.append(Market("kalshi", m["ticker"], title, sub if sub != title else "", m.get("event_ticker", ""),
                                      open_ts, close_ts, m.get("latest_expiration_time") or m.get("close_time"), result,
                                      series=s, url=f"https://kalshi.com/markets/{s.lower()}"))
                if i % 50 == 0:
                    self.hist.log(f"  {i}/{len(series)} series, {len(out)} markets")
            self.hist.log(f"{len(out)} Kalshi markets in the window")
            self._kalshi = out
        return self._kalshi

    def poly(self) -> list[Market]:
        if self._poly is None:
            self.hist.log("Loading Polymarket markets…")
            out = []
            for m in self.hist.poly_markets(self.start, self.end, self.opts.poly_markets, self.opts.poly_query):
                open_ts = ts(m.get("startDate") or m.get("createdAt"))
                close_ts = ts(m.get("closedTime")) if m.get("closed") else None
                close_ts = close_ts or ts(m.get("endDate")) or self.end + 86400
                prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
                result = None
                if m.get("closed") and len(prices) == 2 and max(prices) >= 0.99:
                    result = "yes" if prices[0] >= 0.99 else "no"
                events = m.get("events") or []
                slug = events[0].get("slug") if events else None
                out.append(Market("polymarket", str(m["id"]), m.get("question") or "",
                                  "" if m.get("groupItemTitle") in (None, "None") else m["groupItemTitle"],
                                  slug or "", open_ts or self.start, close_ts, m.get("endDate"), result,
                                  token=json.loads(m["clobTokenIds"])[0],
                                  url=f"https://polymarket.com/event/{slug}" if slug else None))
            self.hist.log(f"{len(out)} Polymarket markets in the window")
            self._poly = out
        return self._poly

    def find(self, venue: str, market_id: str) -> Market:
        pool = self.poly() if venue == "polymarket" else self.kalshi()
        for m in pool:
            if m.id == market_id:
                return m
        raise oms_client.OMSError(f"{venue} market {market_id} is not in this backtest's universe")

    def bars(self, m: Market) -> list:
        if m._bars is None:
            a, b = max(m.open_ts, self.start - 2 * 86400), min(m.close_ts, self.end) + HOUR
            if m.venue == "kalshi":
                rows = []
                for c in self.hist.kalshi_candles(m.series, m.id, a - HOUR, b):
                    bid, ask = c.get("yes_bid") or {}, c.get("yes_ask") or {}
                    def f(d, k):
                        v = d.get(f"{k}_dollars")
                        return float(v) if v not in (None, "") else None
                    rows.append((c["end_period_ts"], f(bid, "close"), f(ask, "close"), f(bid, "low"), f(bid, "high"),
                                 f(ask, "low"), f(ask, "high"), float(c.get("volume_fp") or c.get("volume") or 0)))
            else:
                hs = self.opts.poly_half_spread
                pts = sorted((p["t"], float(p["p"])) for p in self.hist.poly_history(m.token, a - HOUR, b))
                rows = []
                for t, p in pts:
                    bid, ask = max(0.001, p - hs), min(0.999, p + hs)
                    rows.append((t, bid, ask, bid, bid, ask, ask, None))
            m._bars = rows
        return m._bars

    def bar_at(self, m: Market, t: int):
        """The last completed bar at or before t (no peeking at the hour still in progress)."""
        last = None
        for bar in self.bars(m):
            if bar[0] > t:
                break
            last = bar
        return last

    def bars_between(self, m: Market, a: int, b: int) -> list:
        return [bar for bar in self.bars(m) if a < bar[0] <= b]

    def row(self, m: Market, t: int) -> dict:
        bar = self.bar_at(m, t)
        vol24 = sum(x[7] or 0 for x in self.bars_between(m, t - 86400, t)) if m.venue == "kalshi" else None
        bid = bar[1] if bar else None
        ask = bar[2] if bar else None
        return {
            "venue": m.venue, "id": m.id, "title": m.title, "subtitle": m.subtitle, "event": m.event,
            "category": "Mentions" if m.venue == "kalshi" and not self.opts.series else "",
            "yes_bid": bid if bid else None, "yes_ask": ask if ask and ask < 1 else None,
            "last": None if bid is None or ask is None else round((bid + ask) / 2, 4),
            "volume_24h": vol24, "volume": None, "open_interest": None,
            "close_time": m.shown_close, "status": "active", "result": None, "url": m.url, "tradable": True,
        }


# ---------------------------------------------------------------- simulated account

@dataclass
class Position:
    venue: str
    market_id: str
    outcome: str
    title: str
    count: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class Order:
    id: str
    client_order_id: str
    venue: str
    market_id: str
    title: str
    outcome: str
    action: str
    type: str
    count: float
    limit_price: float | None
    status: str = "open"
    filled_count: float = 0.0
    avg_price: float | None = None
    fees: float = 0.0
    reserved: float = 0.0
    reject_reason: str | None = None
    created_at: str = ""


@dataclass
class Book:
    cash_budget: float = 0.0
    cash: float = 0.0
    reserved: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    positions: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    trades: list = field(default_factory=list)


class BacktestMarkets:
    """Stands in for oms_client.PredictionMarkets during a backtest."""

    def __init__(self, strategy_id: str, replay: Replay, opts):
        self.strategy_id = strategy_id
        self.replay = replay
        self.opts = opts
        self.t = replay.start
        self.book = Book()
        self.dry_run = False
        self.funded = False

    # clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, timezone.utc)

    def today(self) -> date:
        return self.now().date()

    def daily_id(self, tag: str) -> str:
        return f"{self.strategy_id}-{self.today():%Y%m%d}-{tag}"[:48]

    def plan_only(self, reason: str) -> None:
        print(f"{reason}: showing the plan only.")

    # markets

    def markets(self, venue: str = "kalshi", q: str = "", limit: int = 200, category: str = "") -> list[dict]:
        pool = self.replay.poly() if venue == "polymarket" else self.replay.kalshi()
        rows = [self.replay.row(m, self.t) for m in pool if m.is_open(self.t)]
        rows = [r for r in rows if r["yes_bid"] is not None or r["yes_ask"] is not None]
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in f"{r['title']} {r['subtitle']} {r['id']} {r['event']}".lower()]
        rows.sort(key=lambda r: -(r["volume_24h"] or 0))
        return rows[:limit]

    def mentions(self, q: str = "", limit: int = 200, **_) -> list[dict]:
        return self.markets("kalshi", q=q, limit=limit, category="mentions")

    def market(self, venue: str, market_id: str) -> dict:
        m = self.replay.find(venue, market_id)
        row = self.replay.row(m, self.t)
        row["rules"] = ""
        size = self._depth(m)
        row["book"] = {"bids": [[row["yes_bid"], size]] if row["yes_bid"] else [],
                       "asks": [[row["yes_ask"], size]] if row["yes_ask"] else []}
        return row

    def _depth(self, m: Market) -> float:
        if m.venue == "polymarket":
            return float(self.opts.poly_max)
        bar = self.replay.bar_at(m, self.t)
        return math.floor((bar[7] if bar else 0) * self.opts.fill_cap)

    # account

    def fund(self, cash_budget: float):
        if not self.funded:
            self.book.cash_budget = self.book.cash = float(cash_budget)
            self.funded = True
        return self._account()

    def account(self):
        if not self.funded:
            return None
        return {"account": self._account(), "positions": self.positions(), "orders": [vars(o) | {} for o in self.book.orders[-200:]], "fills": []}

    def _mark(self, p: Position) -> float | None:
        m = self.replay.find(p.venue, p.market_id)
        bar = self.replay.bar_at(m, self.t)
        if not bar or bar[1] is None or bar[2] is None:
            return None
        yes = (bar[1] + bar[2]) / 2
        return yes if p.outcome == "yes" else 1 - yes

    def positions(self) -> list[dict]:
        out = []
        for p in self.book.positions.values():
            if p.count <= 0:
                continue
            mark = self._mark(p)
            out.append({"strategy_id": self.strategy_id, "venue": p.venue, "market_id": p.market_id, "outcome": p.outcome,
                        "title": p.title, "count": p.count, "avg_price": p.avg_price, "mark": mark,
                        "market_value": None if mark is None else p.count * mark,
                        "unrealized_pnl": None if mark is None else p.count * (mark - p.avg_price),
                        "realized_pnl": p.realized_pnl})
        return out

    def position(self, venue: str, market_id: str, outcome: str) -> float:
        p = self.book.positions.get((venue, market_id, outcome))
        return p.count if p else 0.0

    def equity(self) -> float:
        value = 0.0
        for p in self.book.positions.values():
            if p.count > 0:
                mark = self._mark(p)
                value += p.count * (mark if mark is not None else p.avg_price)
        return self.book.cash + self.book.reserved + value

    def _account(self) -> dict:
        eq = self.equity()
        return {"strategy_id": self.strategy_id, "cash_budget": self.book.cash_budget, "cash": self.book.cash,
                "cash_reserved": self.book.reserved, "positions_value": eq - self.book.cash - self.book.reserved,
                "equity": eq, "realized_pnl": self.book.realized_pnl, "fees_paid": self.book.fees,
                "total_pnl": eq - self.book.cash_budget,
                "open_positions": sum(1 for p in self.book.positions.values() if p.count > 0)}

    # orders

    def order(self, venue: str, market_id: str, outcome: str, action: str, count: float,
              limit: float | None = None, client_order_id: str | None = None, time_in_force: str = "gtc"):
        if not self.funded:
            raise oms_client.OMSError("fund() the strategy before trading")
        coid = client_order_id or uuid.uuid4().hex[:16]
        for o in self.book.orders:
            if o.client_order_id == coid:
                return vars(o)
        m = self.replay.find(venue, market_id)
        title = f"{m.title} · {m.subtitle}" if m.subtitle else m.title
        o = Order(uuid.uuid4().hex, coid, venue, market_id, title, outcome, action,
                  "market" if limit is None else "limit", float(count), limit, created_at=iso(self.t))
        self.book.orders.append(o)
        if venue == "kalshi" and count != int(count):
            return self._reject(o, "Kalshi contracts are whole numbers")
        if not m.is_open(self.t):
            return self._reject(o, "market is closed")
        row = self.market(venue, market_id)
        bids = [(p, s) for p, s in row["book"]["bids"]]
        asks = [(p, s) for p, s in row["book"]["asks"]]
        if outcome == "yes":
            side = asks if action == "buy" else bids
        else:
            side = [(1 - p, s) for p, s in (bids if action == "buy" else asks)]
        fills, left = [], float(count)
        for price, size in side:
            if left <= 0 or (limit is not None and (price > limit if action == "buy" else price < limit)):
                break
            take = min(left, size)
            if take > 0:
                fills.append((round(price, 4), take))
                left -= take
        fee = kalshi_fee(fills) if venue == "kalshi" else 0.0
        rest = left if (limit is not None and time_in_force == "gtc") else 0.0
        if action == "sell":
            held = self.position(venue, market_id, outcome) - sum(
                x.count - x.filled_count for x in self.book.orders
                if x.status == "open" and x is not o and x.action == "sell"
                and (x.venue, x.market_id, x.outcome) == (venue, market_id, outcome))
            if count > held:
                return self._reject(o, f"only {held:g} {outcome.upper()} contracts are available to sell")
        else:
            need = sum(p * s for p, s in fills) + fee + (rest * limit if limit is not None else 0)
            if need > self.book.cash + 1e-9:
                return self._reject(o, f"needs ${need:.2f}, strategy has ${self.book.cash:.2f} spendable")
        if fills:
            self._apply(o, fills, fee)
        if rest > 0:
            if action == "buy":
                o.reserved = rest * limit
                self.book.cash -= o.reserved
                self.book.reserved += o.reserved
        elif o.filled_count >= o.count - 1e-9:
            o.status = "filled"
        else:
            o.status = "canceled"
            o.reject_reason = "no liquidity at or better than the limit" if not fills else "book exhausted, rest canceled"
        price = f" @ {o.avg_price:.3f}" if o.avg_price is not None else ""
        print(f"{action} {outcome.upper()} {market_id} x{count:g} -> {o.status} (order {o.id[:8]}, client id {coid}){price}")
        return vars(o)

    def buy(self, venue, market_id, outcome, count, limit=None, **kw):
        return self.order(venue, market_id, outcome, "buy", count, limit, **kw)

    def sell(self, venue, market_id, outcome, count, limit=None, **kw):
        return self.order(venue, market_id, outcome, "sell", count, limit, **kw)

    def cancel(self, order_id: str) -> dict:
        for o in self.book.orders:
            if o.id == order_id and o.status == "open":
                self._close(o, "canceled", None)
                return vars(o)
        raise oms_client.OMSError("order not found or not open")

    def _reject(self, o: Order, reason: str) -> dict:
        o.status, o.reject_reason = "rejected", reason
        print(f"  {o.action} {o.market_id} failed: {reason}")
        return vars(o)

    def _apply(self, o: Order, fills, fee: float, kind: str = "taker") -> None:
        size = sum(s for _, s in fills)
        notional = sum(p * s for p, s in fills)
        key = (o.venue, o.market_id, o.outcome)
        p = self.book.positions.setdefault(key, Position(o.venue, o.market_id, o.outcome, o.title))
        if o.action == "buy":
            if o.reserved > 0:
                release = min(o.reserved, size * o.limit_price)
                o.reserved -= release
                self.book.reserved -= release
                self.book.cash += release
            self.book.cash -= notional + fee
            p.avg_price = (p.count * p.avg_price + notional) / (p.count + size)
            p.count += size
            pnl = 0.0
        else:
            self.book.cash += notional - fee
            pnl = notional - size * p.avg_price
            p.realized_pnl += pnl
            self.book.realized_pnl += pnl
            p.count -= size
        self.book.fees += fee
        old = o.filled_count
        o.filled_count += size
        o.avg_price = ((o.avg_price or 0) * old + notional) / o.filled_count
        o.fees += fee
        self.book.trades.append({"time": iso(self.t), "venue": o.venue, "market_id": o.market_id, "title": o.title,
                                 "outcome": o.outcome, "action": o.action, "count": size,
                                 "price": round(notional / size, 4), "fee": fee, "pnl": round(pnl, 4), "kind": kind})

    def _close(self, o: Order, status: str, reason: str | None) -> None:
        if o.reserved > 0:
            self.book.reserved -= o.reserved
            self.book.cash += o.reserved
            o.reserved = 0.0
        o.status, o.reject_reason = status, reason

    # clock advance: resting orders, then settlement

    def advance(self, t_next: int) -> None:
        for o in [x for x in self.book.orders if x.status == "open"]:
            m = self.replay.find(o.venue, o.market_id)
            if not m.is_open(self.t):
                self._close(o, "canceled", "market closed")
                continue
            left = o.count - o.filled_count
            if o.action == "sell":
                left = min(left, self.position(o.venue, o.market_id, o.outcome))
            for bar in self.replay.bars_between(m, self.t, min(t_next, m.close_ts + HOUR)):
                if left <= 0:
                    break
                _, _, _, bid_lo, bid_hi, ask_lo, ask_hi, vol = bar
                L = o.limit_price
                if o.outcome == "yes":
                    hit = (ask_lo is not None and ask_lo <= L) if o.action == "buy" else (bid_hi is not None and bid_hi >= L)
                else:  # NO ask = 1 - YES bid, NO bid = 1 - YES ask
                    hit = (bid_hi is not None and 1 - bid_hi <= L) if o.action == "buy" else (ask_lo is not None and 1 - ask_lo >= L)
                if not hit:
                    continue
                cap = float(self.opts.poly_max) if vol is None else math.floor(vol * self.opts.fill_cap)
                take = min(left, cap)
                if o.venue == "kalshi":
                    take = math.floor(take)
                if take > 0:
                    self._apply(o, [(L, take)], 0.0, kind="maker")
                    left -= take
            if o.filled_count >= o.count - 1e-9:
                self._close(o, "filled", None)
        self.t = t_next
        for key, p in list(self.book.positions.items()):
            if p.count <= 0:
                continue
            m = self.replay.find(p.venue, p.market_id)
            if m.result and self.t >= m.close_ts:
                price = 1.0 if p.outcome == m.result else 0.0
                payout = p.count * price
                pnl = payout - p.count * p.avg_price
                self.book.cash += payout
                self.book.realized_pnl += pnl
                p.realized_pnl += pnl
                self.book.trades.append({"time": iso(self.t), "venue": p.venue, "market_id": p.market_id, "title": p.title,
                                         "outcome": p.outcome, "action": "settle", "count": p.count, "price": price,
                                         "fee": 0.0, "pnl": round(pnl, 4), "kind": "settle"})
                p.count = 0.0


# ---------------------------------------------------------------- runner

def strategy_file(sid: str) -> Path:
    folder = HERE / sid
    manifest = json.loads((folder / "strategy.json").read_text())
    src = manifest["source"]
    path = Path(src["file"])
    return path if path.is_absolute() else folder / path


def run(sid: str, start: datetime, end: datetime, opts) -> dict:
    path = strategy_file(sid)
    hist = History(quiet=opts.quiet)
    replay = Replay(hist, int(start.timestamp()), int(end.timestamp()), opts)
    bt = BacktestMarkets(sid, replay, opts)
    step = int(opts.step_hours * HOUR)
    oms_client._BACKTEST = bt
    os.environ["SIMPLI_STRATEGY_ID"] = sid
    out_dir = HERE / sid / "backtests" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = open(out_dir / "log.txt", "w")
    curve, errors = [], 0
    steps = max(1, (replay.end - replay.start) // step)
    started = time.time()
    try:
        for i in range(steps + 1):
            log.write(f"\n===== {iso(bt.t)} =====\n")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    runpy.run_path(str(path), run_name="__main__")
            except SystemExit:
                pass
            except Exception as exc:  # a failing step is logged, the backtest keeps going
                errors += 1
                buf.write(f"ERROR: {type(exc).__name__}: {exc}\n")
            log.write(buf.getvalue())
            curve.append({"time": iso(bt.t), "equity": round(bt.equity(), 4), "cash": round(bt.book.cash, 4)})
            if not opts.quiet and (i % 24 == 0 or i == steps):
                print(f"  {iso(bt.t)}  equity ${bt.equity():,.2f}  ({i}/{steps} steps, {time.time() - started:.0f}s)",
                      file=sys.stderr, flush=True)
            bt.advance(bt.t + step)
        # let anything still open settle on its real result
        bt.advance(max([bt.t] + [replay.find(p.venue, p.market_id).close_ts
                                 for p in bt.book.positions.values() if p.count > 0]))
    finally:
        oms_client._BACKTEST = None
        log.close()
    summary = report(sid, bt, curve, errors, start, end, opts)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(out_dir / "equity.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "equity", "cash"])
        w.writeheader()
        w.writerows(curve)
    with open(out_dir / "trades.csv", "w", newline="") as f:
        fields = ["time", "venue", "market_id", "title", "outcome", "action", "count", "price", "fee", "pnl", "kind"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(bt.book.trades)
    summary["output"] = str(out_dir)
    return summary


def report(sid, bt: BacktestMarkets, curve, errors, start, end, opts) -> dict:
    budget = bt.book.cash_budget or 0.0
    final = bt.equity()
    peak, max_dd = -1e18, 0.0
    for point in curve + [{"equity": final}]:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = max(max_dd, (peak - point["equity"]) / peak)
    settles = [t for t in bt.book.trades if t["kind"] == "settle"]
    closes = settles + [t for t in bt.book.trades if t["action"] == "sell"]
    wins = [t for t in closes if t["pnl"] > 0]
    buys = [t for t in bt.book.trades if t["action"] == "buy"]
    by_market: dict[str, float] = {}
    for t in bt.book.trades:
        by_market[t["market_id"]] = by_market.get(t["market_id"], 0.0) + t["pnl"] - t["fee"]
    ranked = sorted(by_market.items(), key=lambda kv: kv[1])
    return {
        "strategy_id": sid, "start": start.isoformat(), "end": end.isoformat(), "step_hours": opts.step_hours,
        "fill_cap": opts.fill_cap, "poly_half_spread": opts.poly_half_spread,
        "starting_cash": budget, "final_equity": round(final, 2), "total_pnl": round(final - budget, 2),
        "return_pct": round((final / budget - 1) * 100, 2) if budget else None,
        "max_drawdown_pct": round(max_dd * 100, 2), "fees": round(bt.book.fees, 2),
        "buys": len(buys), "markets_traded": len(by_market), "closed_positions": len(closes),
        "win_rate_pct": round(len(wins) / len(closes) * 100, 1) if closes else None,
        "open_positions_left": sum(1 for p in bt.book.positions.values() if p.count > 0),
        "steps_with_errors": errors,
        "worst_markets": [{"market_id": k, "pnl": round(v, 2)} for k, v in ranked[:5]],
        "best_markets": [{"market_id": k, "pnl": round(v, 2)} for k, v in ranked[::-1][:5]],
    }


def parse_day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest a prediction-market strategy on historical prices.")
    ap.add_argument("strategy", help="strategy folder in strategies/, e.g. kalshi-mentions")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (UTC), not after today")
    ap.add_argument("--step-hours", type=float, default=1.0, help="run the strategy every N hours (default 1)")
    ap.add_argument("--series", nargs="*", help="Kalshi series to load (default: every Mentions series)")
    ap.add_argument("--fill-cap", type=float, default=0.2, help="max share of an hour's Kalshi volume one order can take")
    ap.add_argument("--poly-markets", type=int, default=200, help="Polymarket markets to load, busiest first")
    ap.add_argument("--poly-query", default="", help="only Polymarket markets whose question contains this")
    ap.add_argument("--poly-half-spread", type=float, default=0.01, help="Polymarket bid/ask = price -/+ this")
    ap.add_argument("--poly-max", type=float, default=500, help="max Polymarket contracts per order per step")
    ap.add_argument("--quiet", action="store_true")
    opts = ap.parse_args(argv)
    start, end = parse_day(opts.start), parse_day(opts.end)
    now = datetime.now(timezone.utc)
    if end > now:
        end = now.replace(minute=0, second=0, microsecond=0)
    if end <= start:
        ap.error("--end must be after --start")
    summary = run(opts.strategy, start, end, opts)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
