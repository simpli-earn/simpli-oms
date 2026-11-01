"""The one import a strategy needs to trade through the OMS.

    from oms_client import OMS, latest_prices
    oms = OMS()                                   # id + dry run come from SimpliEarn Desk
    oms.rebalance({"SPY": 0.6, "QQQ": 0.4}, latest_prices(["SPY", "QQQ"]))

Kalshi and Polymarket paper trading uses PredictionMarkets() the same way (see its docstring).

Outside the app pass the id yourself: OMS("my-strategy"). Set OMS_DRY_RUN=1 (or pass
dry_run=True) to print orders instead of sending them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
LOG_DIR = Path(__file__).resolve().parent / ".logs"
ENV = dotenv_values(BACKEND / ".env")

DEFAULT_URL = os.getenv("OMS_API_URL", "http://127.0.0.1:8787").rstrip("/")
DEFAULT_KEY = os.getenv("OMS_API_KEY") or ENV.get("OMS_API_KEY") or "dev-key"
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced"}
_BACKTEST = None  # set by backtest.py; PredictionMarkets() then replays history instead of trading


def _server_up(url: str) -> bool:
    try:
        return requests.get(f"{url}/openapi.json", timeout=2).ok
    except requests.RequestException:
        return False


def ensure_server(url: str = DEFAULT_URL, timeout: float = 45) -> None:
    """Start the OMS backend in the background unless it is already running.

    The server is detached, so it keeps running after the notebook finishes
    and other notebooks reuse it. Stop it with stop_server().
    """
    if _server_up(url):
        print(f"OMS backend already running at {url}")
        return

    python = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        raise RuntimeError("backend/.venv is missing. Run `python setup.py` in the OMS folder first.")
    port = url.rsplit(":", 1)[-1]
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / "oms-backend.log", "a")
    proc = subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--port", port],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (LOG_DIR / "oms-backend.pid").write_text(str(proc.pid))

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"OMS backend exited early. See {LOG_DIR / 'oms-backend.log'}")
        if _server_up(url):
            print(f"Started OMS backend at {url} (pid {proc.pid}, log in strategies/.logs/)")
            return
        time.sleep(0.5)
    raise RuntimeError(f"OMS backend did not start within {timeout}s. See {LOG_DIR / 'oms-backend.log'}")


def stop_server() -> None:
    """Stop a backend that ensure_server() started."""
    pid_file = LOG_DIR / "oms-backend.pid"
    if not pid_file.is_file():
        print("No backend started from a notebook.")
        return
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 15)
        print(f"Stopped OMS backend (pid {pid})")
    except ProcessLookupError:
        print("Backend was already stopped.")
    pid_file.unlink()


class OMSError(RuntimeError):
    pass


class OMS:
    def __init__(
        self,
        strategy_id: str | None = None,
        cash_budget: float | None = None,
        url: str = DEFAULT_URL,
        api_key: str = DEFAULT_KEY,
        dry_run: bool | None = None,
        start_server: bool = True,
    ):
        """strategy_id defaults to SIMPLI_STRATEGY_ID, which SimpliEarn Desk sets on every run.
        dry_run defaults to OMS_DRY_RUN=1, which the app's Test run button sets."""
        strategy_id = strategy_id or os.getenv("SIMPLI_STRATEGY_ID")
        if not strategy_id:
            raise OMSError("no strategy id: pass OMS('your-id') or run this from SimpliEarn Desk")
        self.strategy_id = strategy_id
        self.url = url
        self.headers = {"X-API-Key": api_key}
        self.dry_run = os.getenv("OMS_DRY_RUN") == "1" if dry_run is None else dry_run
        if start_server:
            ensure_server(url)
        if cash_budget is not None:
            self.register(cash_budget)

    def _request(self, method: str, path: str, **kwargs):
        response = requests.request(method, f"{self.url}{path}", headers=self.headers, timeout=30, **kwargs)
        if not response.ok:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise OMSError(f"{method} {path} -> {response.status_code}: {detail}")
        return response.json()

    # strategy

    def register(self, cash_budget: float) -> dict | None:
        """Register this strategy once. Does nothing if it already exists."""
        existing = self.strategy()
        if existing is not None:
            print(f"Strategy {self.strategy_id!r} already registered (budget ${existing['cash_budget']:,.2f})")
            return existing
        if self.dry_run:
            print(f"[test run] would register {self.strategy_id!r} with ${cash_budget:,.2f}")
            return None
        created = self._request("POST", "/api/strategies", json={"id": self.strategy_id, "cash_budget": cash_budget})
        print(f"Registered {self.strategy_id!r} with ${cash_budget:,.2f}")
        return created

    def strategy(self) -> dict | None:
        try:
            return self._request("GET", f"/api/strategies/{self.strategy_id}")
        except OMSError as exc:
            if "404" in str(exc):
                return None
            raise

    def book(self) -> dict:
        """Cash, positions, orders, and fills for this strategy."""
        return self._request("GET", f"/api/strategies/{self.strategy_id}/book")

    def position(self, symbol: str) -> float:
        """This strategy's quantity in a symbol (0 if none, or not registered yet)."""
        if self.strategy() is None:
            return 0.0
        for pos in self.book()["positions"]:
            if pos["symbol"] == symbol.upper():
                return pos["qty"]
        return 0.0

    # orders

    def order(self, client_order_id: str | None = None, **fields) -> dict | None:
        """POST /api/orders. Fields match the OMS order schema (see /docs).

        Reusing a client_order_id returns the original order instead of a new one,
        so a stable id makes re-running a notebook safe.
        """
        body = {"strategy_id": self.strategy_id, "client_order_id": client_order_id or uuid.uuid4().hex[:16], **fields}
        if self.dry_run:
            print(f"[test run] would POST /api/orders {body}")
            return None
        result = self._request("POST", "/api/orders", json=body)
        print(
            f"{result['side'] or 'mleg'} {result['symbol'] or ''} -> {result['status']}"
            f" (order {result['id']}, client id {result['client_order_id']})"
        )
        return result

    def buy(self, symbol: str, qty: float | None = None, notional: float | None = None, **fields) -> dict | None:
        fields.setdefault("type", "market")
        return self.order(symbol=symbol, side="buy", qty=qty, notional=notional, **fields)

    def sell(self, symbol: str, qty: float, **fields) -> dict | None:
        fields.setdefault("type", "market")
        return self.order(symbol=symbol, side="sell", qty=qty, **fields)

    def cancel(self, order_id: str) -> dict:
        return self._request("DELETE", f"/api/orders/{order_id}")

    def market_is_open(self) -> bool:
        from alpaca.trading.client import TradingClient

        return bool(TradingClient(*alpaca_keys(), paper=True).get_clock().is_open)

    def plan_only(self, reason: str = "Market is closed") -> None:
        """Switch to dry run for the rest of this run; the app records it as "plan only"."""
        print(f"{reason}: showing the plan only.")
        self.dry_run = True

    def daily_id(self, tag: str) -> str:
        """A client_order_id that is the same all day, e.g. 'my-strat-20261026-buy'."""
        return f"{self.strategy_id}-{date.today():%Y%m%d}-{tag}"[:48]

    # rebalancing

    def wait_for(self, order_ids: list[str], timeout: float = 90) -> list[dict]:
        """Poll until every order is filled, canceled, expired, or rejected."""
        pending = set(order_ids)
        deadline = time.time() + timeout
        done: list[dict] = []
        while pending and time.time() < deadline:
            time.sleep(3)
            for oid in list(pending):
                order = self._request("GET", f"/api/orders/{oid}")
                if order["status"] in TERMINAL_STATUSES:
                    pending.discard(oid)
                    done.append(order)
        if pending:
            print(f"{len(pending)} order(s) still open after {timeout:.0f}s; continuing with the cash available now")
        return done

    def holdings(self) -> dict[str, float]:
        """{symbol: qty} for this strategy's open positions."""
        return {p["symbol"]: p["qty"] for p in self.book()["positions"] if abs(p["qty"]) > 1e-9}

    def rebalance(
        self,
        target_weights: dict[str, float],
        prices: dict[str, float],
        invest_fraction: float = 0.99,
        min_trade: float = 1.0,
    ) -> dict:
        """Move this strategy's book to target weights of its equity, using market orders.

        Equity = spendable cash + reserved cash + positions at `prices`. Anything held but not in
        `target_weights` is sold. Sells go first and are waited on, because the OMS only credits
        sale proceeds once Alpaca reports the fill; buys are then scaled to the cash left.
        Differences under `min_trade` dollars are skipped (Alpaca's fractional minimum is $1).
        Order ids repeat within a day, so re-running the same day cannot double an order.
        """
        if self.strategy() is None:
            raise OMSError(f"strategy {self.strategy_id!r} is not registered")
        book = self.book()
        strat = book["strategy"]
        held = {p["symbol"]: p["qty"] for p in book["positions"] if abs(p["qty"]) > 1e-9}
        missing = [s for s in held if s not in prices]
        if missing:
            raise OMSError(f"no price for held symbols {missing}")

        cash = strat["cash_remaining"] + open_buy_cost(book["orders"])
        value = {s: q * prices[s] for s, q in held.items()}
        equity = cash + sum(value.values())
        investable = equity * invest_fraction
        targets = {s: w * investable for s, w in target_weights.items() if w > 0}

        sells, buys = [], []
        for s in sorted(set(held) | set(targets)):
            diff = targets.get(s, 0.0) - value.get(s, 0.0)
            if s not in targets and held.get(s, 0) > 0:
                sells.append((s, held[s], value[s]))  # exit the whole position
            elif diff <= -min_trade:
                sells.append((s, min(held[s], round(-diff / prices[s], 6)), -diff))
            elif diff >= min_trade:
                buys.append((s, diff))

        print(f"equity ${equity:,.2f} (cash ${cash:,.2f}) | {len(held)} held, {len(targets)} targeted | "
              f"{len(sells)} sells, {len(buys)} buys")

        sent_sells = []
        for s, qty, dollars in sells:
            try:
                order = self.sell(s, qty=qty, client_order_id=self.daily_id(f"s-{s}"))
                if order:
                    sent_sells.append(order["id"])
            except OMSError as exc:
                print(f"  sell {s} failed: {exc}")
        if sent_sells:
            print(f"waiting for {len(sent_sells)} sell(s) to fill...")
            self.wait_for(sent_sells)

        if self.dry_run:
            spendable = strat["cash_remaining"] + sum(d for _, _, d in sells)
        else:
            spendable = self.strategy()["cash_remaining"]
        wanted = sum(d for _, d in buys)
        scale = min(1.0, spendable / wanted) if wanted else 1.0
        if scale < 1:
            print(f"buys scaled to {scale:.1%} to fit ${spendable:,.2f} of spendable cash")
        sent_buys = []
        for s, dollars in buys:
            notional = round(dollars * scale, 2)
            if notional < min_trade:
                continue
            try:
                order = self.buy(s, notional=notional, client_order_id=self.daily_id(f"b-{s}"))
                if order:
                    sent_buys.append(order["id"])
            except OMSError as exc:
                print(f"  buy {s} failed: {exc}")
        return {"equity": equity, "sells": sells, "buys": buys, "sell_ids": sent_sells, "buy_ids": sent_buys}


class PredictionMarkets:
    """Paper trade Kalshi and Polymarket against their live order books.

        from oms_client import PredictionMarkets
        pm = PredictionMarkets()                  # id + test run come from SimpliEarn Desk
        pm.fund(500)                              # once: prediction cash, separate from the Alpaca budget
        for m in pm.mentions(q="trump"):          # Kalshi "What will X say" markets
            print(m["id"], m["title"], m["subtitle"], m["yes_bid"], m["yes_ask"])
        pm.buy("kalshi", "KXTRUMPMENTION-26NOV01-CRYP", "no", 10, limit=0.97)

    Prices are dollars per contract (0 to 1) for the outcome you name. A contract pays $1 if that
    outcome wins. An order fills against the live book right away; a limit order's remainder rests
    until the book trades through it. Kalshi charges its taker fee; Polymarket is free.

    The same file can be backtested (backtest.py). Use pm.now() and pm.today() instead of the system
    clock so the backtest can move time.
    """

    def __new__(cls, *args, **kwargs):
        if _BACKTEST is not None:
            return _BACKTEST
        return super().__new__(cls)

    def __init__(
        self,
        strategy_id: str | None = None,
        url: str = DEFAULT_URL,
        api_key: str = DEFAULT_KEY,
        dry_run: bool | None = None,
        start_server: bool = True,
    ):
        strategy_id = strategy_id or os.getenv("SIMPLI_STRATEGY_ID")
        if not strategy_id:
            raise OMSError("no strategy id: pass PredictionMarkets('your-id') or run this from SimpliEarn Desk")
        self.strategy_id = strategy_id
        self.url = url
        self.headers = {"X-API-Key": api_key}
        self.dry_run = os.getenv("OMS_DRY_RUN") == "1" if dry_run is None else dry_run
        if start_server:
            ensure_server(url)

    _request = OMS._request
    daily_id = OMS.daily_id

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> date:
        return date.today()

    def plan_only(self, reason: str) -> None:
        """Switch to test-run mode for the rest of this run; the app records it as "plan only"."""
        print(f"{reason}: showing the plan only.")
        self.dry_run = True

    # markets

    def markets(self, venue: str = "kalshi", q: str = "", limit: int = 200, category: str = "") -> list[dict]:
        """Open markets, busiest first. venue is kalshi or polymarket."""
        params = {"venue": venue, "q": q, "limit": limit, "category": category}
        return self._request("GET", "/api/predictions/markets", params=params)

    def mentions(self, q: str = "", limit: int = 200, wait: float = 120) -> list[dict]:
        """Kalshi mention markets. The first call after the OMS starts waits for its scan of Kalshi."""
        deadline = time.time() + wait
        while True:
            rows = self.markets("kalshi", q=q, limit=limit, category="mentions")
            scan = self._request("GET", "/api/predictions/status").get("mentions_scan", {})
            if not scan.get("running") or time.time() > deadline:
                return rows
            print(f"Waiting for the Kalshi mention scan ({scan.get('done', 0)} of {scan.get('total', '?')} series)")
            time.sleep(10)

    def market(self, venue: str, market_id: str) -> dict:
        """One market with prices, rules, and its YES order book ({"bids": [[price, size]], "asks": ...})."""
        return self._request("GET", f"/api/predictions/markets/{venue}/{market_id}")

    # account

    def account(self) -> dict | None:
        """Cash, positions, orders, and fills, or None before fund() is called."""
        try:
            return self._request("GET", f"/api/predictions/paper/accounts/{self.strategy_id}")
        except OMSError as exc:
            if "-> 404" in str(exc):
                return None
            raise

    def fund(self, cash_budget: float) -> dict | None:
        """Give this strategy prediction-market paper cash once. Does nothing if it already has an account."""
        existing = self.account()
        if existing is not None:
            print(f"Prediction account for {self.strategy_id!r} exists (cash ${existing['account']['cash']:,.2f})")
            return existing["account"]
        if self.dry_run:
            print(f"[test run] would fund {self.strategy_id!r} with ${cash_budget:,.2f} of prediction paper cash")
            return None
        created = self._request("POST", "/api/predictions/paper/accounts",
                                json={"strategy_id": self.strategy_id, "cash_budget": cash_budget})
        print(f"Funded {self.strategy_id!r} with ${cash_budget:,.2f} of prediction paper cash")
        return created

    def positions(self) -> list[dict]:
        account = self.account()
        return account["positions"] if account else []

    def position(self, venue: str, market_id: str, outcome: str) -> float:
        for p in self.positions():
            if (p["venue"], p["market_id"], p["outcome"]) == (venue, market_id, outcome):
                return p["count"]
        return 0.0

    # orders

    def order(self, venue: str, market_id: str, outcome: str, action: str, count: float,
              limit: float | None = None, client_order_id: str | None = None, time_in_force: str = "gtc") -> dict | None:
        """limit=None sends a market order (fills what the book has now, cancels the rest)."""
        body = {
            "strategy_id": self.strategy_id, "venue": venue, "market_id": market_id, "outcome": outcome,
            "action": action, "count": count, "type": "market" if limit is None else "limit", "limit_price": limit,
            "time_in_force": time_in_force, "client_order_id": client_order_id or uuid.uuid4().hex[:16],
        }
        if self.dry_run:
            print(f"[test run] would POST /api/predictions/paper/orders {body}")
            return None
        r = self._request("POST", "/api/predictions/paper/orders", json=body)
        price = f" @ {r['avg_price']:.3f}" if r["avg_price"] is not None else ""
        print(f"{action} {outcome.upper()} {market_id} x{count:g} -> {r['status']}"
              f" (order {r['id']}, client id {r['client_order_id']}){price}")
        if r["status"] in ("rejected", "canceled") and r["reject_reason"]:
            print(f"  {action} {market_id} failed: {r['reject_reason']}")
        return r

    def buy(self, venue: str, market_id: str, outcome: str, count: float, limit: float | None = None, **kw):
        return self.order(venue, market_id, outcome, "buy", count, limit, **kw)

    def sell(self, venue: str, market_id: str, outcome: str, count: float, limit: float | None = None, **kw):
        return self.order(venue, market_id, outcome, "sell", count, limit, **kw)

    def cancel(self, order_id: str) -> dict:
        return self._request("DELETE", f"/api/predictions/paper/orders/{order_id}")


def open_buy_cost(orders: list[dict]) -> float:
    """Cash still tied up in buy orders that have not finished.

    Used instead of the strategy's `cash_reserved`, which the OMS can leave too high after a
    notional buy fills for a few cents less than requested.
    """
    total = 0.0
    for o in orders:
        if o["status"] in TERMINAL_STATUSES or o["side"] != "buy":
            continue
        if o["notional"]:
            total += o["notional"]
        elif o["limit_price"]:
            total += (o["qty"] - (o["filled_qty"] or 0)) * o["limit_price"]
    return total


def alpaca_keys() -> tuple[str, str]:
    """Paper keys from backend/.env, for pulling market data in a notebook."""
    return ENV["APCA_API_KEY_ID"], ENV["APCA_API_SECRET_KEY"]


def latest_prices(symbols) -> dict[str, float]:
    """Last trade price per stock symbol (full-market feed, about 15 minutes delayed on the free plan)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest

    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    client = StockHistoricalDataClient(*alpaca_keys())
    try:
        trades = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.SIP))
    except Exception:  # the free plan refuses SIP for the last 15 minutes; IEX is always allowed
        trades = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX))
    return {s: float(t.price) for s, t in trades.items()}
