"""Kalshi and Polymarket prediction markets.

Market data is public on both venues. Orders go only to Kalshi's demo exchange, which is
Kalshi's paper environment. Polymarket has no paper environment, so it is read-only here.

Every market is returned in one shape. Prices are dollars per contract (0 to 1) for the YES side.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import Settings

KALSHI_LIVE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo-api.kalshi.co/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
VENUES = ("kalshi", "kalshi-demo", "polymarket")
LIST_TTL = 15.0
QUOTE_TTL = 10.0
MENTION_TTL = 600.0
BOOK_ROWS = 10


class PredictionError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _f(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(item: dict, name: str) -> float | None:
    """Kalshi sends `<name>_dollars` as a string, older payloads send `<name>` in cents."""
    dollars = _f(item.get(f"{name}_dollars"))
    if dollars is not None:
        return dollars
    cents = _f(item.get(name))
    return None if cents is None else cents / 100


def _count(item: dict, name: str) -> float | None:
    value = _f(item.get(f"{name}_fp"))
    return value if value is not None else _f(item.get(name))


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class _Cache:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, object]] = {}

    def get(self, key: str, ttl: float, load):
        now = time.monotonic()
        with self._lock:
            hit = self._items.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = load()
        with self._lock:
            self._items[key] = (now, value)
        return value


class Predictions:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.http = httpx.Client(timeout=15, transport=transport, headers={"User-Agent": "simpli-oms"})
        self.cache = _Cache()
        self._key = None
        self._scan_lock = threading.Lock()
        self._scan_thread: threading.Thread | None = None
        self._mentions = {"rows": [], "at": 0.0, "done": 0, "total": 0, "running": False}

    # ---------- shared ----------

    def status(self) -> dict:
        return {
            "venues": [
                {"id": "kalshi", "name": "Kalshi", "env": "live", "tradable": False},
                {"id": "kalshi-demo", "name": "Kalshi demo", "env": "demo", "tradable": self.kalshi_configured()},
                {"id": "polymarket", "name": "Polymarket", "env": "live", "tradable": False},
            ],
            "kalshi_demo_configured": self.kalshi_configured(),
            "mentions_scan": {k: self._mentions[k] for k in ("done", "total", "running")},
        }

    def markets(self, venue: str, q: str = "", limit: int = 50, category: str = "") -> list[dict]:
        limit = min(max(limit, 1), 200)
        q = q.strip().lower()
        if category == "mentions":
            if venue != "kalshi":
                raise PredictionError(400, "mention markets are listed for venue=kalshi")
            rows = self.mentions()
            if q:
                rows = [m for m in rows if q in f"{m['title']} {m['subtitle']} {m['id']} {m['event']}".lower()]
        elif venue == "polymarket":
            rows = self._poly_search(q) if q else self.cache.get("poly", LIST_TTL, self._poly_top)
        elif venue in ("kalshi", "kalshi-demo"):
            rows = self.cache.get(venue, LIST_TTL, lambda: self._kalshi_list(venue))
            if q:
                rows = [m for m in rows if q in f"{m['title']} {m['subtitle']} {m['id']} {m['event']}".lower()]
        else:
            raise PredictionError(404, f"unknown venue {venue!r}, use one of {', '.join(VENUES)}")
        return rows[:limit]

    def market(self, venue: str, market_id: str) -> dict:
        """One market with the top of its YES book."""
        row = self.snapshot(venue, market_id)
        row["book"] = {side: levels[:BOOK_ROWS] for side, levels in row["book"].items()}
        return row

    def snapshot(self, venue: str, market_id: str) -> dict:
        """One market with its full YES book, read fresh. The paper engine fills against this."""
        if venue == "polymarket":
            return self._poly_market(market_id)
        if venue in ("kalshi", "kalshi-demo"):
            return self._kalshi_market(venue, market_id)
        raise PredictionError(404, f"unknown venue {venue!r}")

    def quote(self, venue: str, market_id: str) -> dict:
        """Prices and resolution without the book, cached briefly. Used to mark positions."""
        def load():
            if venue == "polymarket":
                return self._poly_row(self._get(f"{GAMMA}/markets/{market_id}"))
            if venue in ("kalshi", "kalshi-demo"):
                market = self._get(f"{self._kalshi_base(venue)}/markets/{market_id}").get("market", {})
                return self._kalshi_row(venue, market)
            raise PredictionError(404, f"unknown venue {venue!r}")

        return self.cache.get(f"quote:{venue}:{market_id}", QUOTE_TTL, load)

    # ---------- Kalshi mention markets ----------

    def mentions(self) -> list[dict]:
        """Open markets in Kalshi's Mentions category. A background scan refreshes them every 10 minutes."""
        self._start_mention_scan()
        return list(self._mentions["rows"])

    def _start_mention_scan(self) -> None:
        with self._scan_lock:
            if self._mentions["running"] or time.monotonic() - self._mentions["at"] < MENTION_TTL and self._mentions["at"]:
                return
            self._mentions["running"] = True
            self._scan_thread = threading.Thread(target=self._scan_mentions, name="kalshi-mentions", daemon=True)
            self._scan_thread.start()

    def _scan_mentions(self) -> None:
        # Kalshi has no category filter on events, so read each Mentions series. Throttled for rate limits.
        try:
            series = self._get(f"{KALSHI_LIVE}/series", category="Mentions").get("series") or []
            self._mentions.update(total=len(series), done=0)
            rows: list[dict] = []
            for index, item in enumerate(series, 1):
                data = None
                for attempt in range(3):
                    try:
                        data = self._get(f"{KALSHI_LIVE}/events", series_ticker=item["ticker"], status="open",
                                         with_nested_markets="true")
                        break
                    except PredictionError:
                        time.sleep(2 * (attempt + 1))
                for event in (data or {}).get("events") or []:
                    for market in event.get("markets") or []:
                        if market.get("status") in ("active", "open"):
                            rows.append(self._kalshi_row("kalshi", market, event))
                self._mentions["done"] = index
                if index % 25 == 0 or index == len(series):
                    self._mentions["rows"] = sorted(rows, key=lambda m: -(m["volume_24h"] or 0))
                time.sleep(self.settings.kalshi_scan_delay_seconds)
            self._mentions["at"] = time.monotonic()
        except PredictionError:
            self._mentions["at"] = time.monotonic() - MENTION_TTL + 60  # retry in a minute
        finally:
            self._mentions["running"] = False

    def _get(self, url: str, **params) -> dict | list:
        try:
            response = self.http.get(url, params=params or None)
        except httpx.HTTPError as exc:
            raise PredictionError(502, f"{urlsplit(url).netloc} unreachable: {exc}") from exc
        if response.status_code == 404:
            raise PredictionError(404, "market not found")
        if not response.is_success:
            raise PredictionError(502, f"{urlsplit(url).netloc} returned {response.status_code}")
        return response.json()

    # ---------- Kalshi market data ----------

    def _kalshi_base(self, venue: str) -> str:
        return KALSHI_DEMO if venue == "kalshi-demo" else KALSHI_LIVE

    def _kalshi_list(self, venue: str) -> list[dict]:
        base = self._kalshi_base(venue)
        rows: list[dict] = []
        cursor = None
        for _ in range(3):
            params = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self._get(f"{base}/events", **params)
            for event in data.get("events", []):
                for market in event.get("markets") or []:
                    rows.append(self._kalshi_row(venue, market, event))
            cursor = data.get("cursor")
            if not cursor:
                break
        rows.sort(key=lambda m: (-(m["volume_24h"] or 0), -(m["volume"] or 0)))
        return rows

    def _kalshi_row(self, venue: str, market: dict, event: dict | None = None) -> dict:
        event = event or {}
        series = (event.get("series_ticker") or market.get("event_ticker", "").split("-")[0]).lower()
        title = market.get("title") or event.get("title") or market.get("ticker")
        subtitle = market.get("yes_sub_title") or market.get("subtitle") or event.get("sub_title") or ""
        return {
            "venue": venue,
            "id": market.get("ticker"),
            "title": title,
            "subtitle": subtitle if subtitle != title else "",
            "event": market.get("event_ticker", ""),
            "category": event.get("category", ""),
            "yes_bid": _money(market, "yes_bid"),
            "yes_ask": _money(market, "yes_ask"),
            "last": _money(market, "last_price"),
            "volume_24h": _count(market, "volume_24h"),
            "volume": _count(market, "volume"),
            "open_interest": _count(market, "open_interest"),
            "close_time": market.get("close_time"),
            "status": market.get("status"),
            "result": market.get("result") or None,
            "url": f"https://kalshi.com/markets/{series}" if venue == "kalshi" and series else None,
            "tradable": venue == "kalshi-demo" and self.kalshi_configured(),
        }

    def _kalshi_market(self, venue: str, ticker: str) -> dict:
        base = self._kalshi_base(venue)
        market = self._get(f"{base}/markets/{ticker}").get("market", {})
        row = self._kalshi_row(venue, market)
        row["rules"] = market.get("rules_primary", "")
        book = self._get(f"{base}/markets/{ticker}/orderbook")
        book = book.get("orderbook_fp") or book.get("orderbook") or {}
        yes = book.get("yes_dollars") or [[c / 100, q] for c, q in book.get("yes") or []]
        no = book.get("no_dollars") or [[c / 100, q] for c, q in book.get("no") or []]
        # A NO bid at p is a YES offer at 1 - p.
        row["book"] = {
            "bids": sorted(([float(p), float(s)] for p, s in yes), key=lambda x: -x[0]),
            "asks": sorted(([round(1 - float(p), 4), float(s)] for p, s in no), key=lambda x: x[0]),
        }
        return row

    # ---------- Polymarket market data ----------

    def _poly_top(self) -> list[dict]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get(
            f"{GAMMA}/markets",
            active="true", closed="false", limit=200, order="volume24hr", ascending="false", end_date_min=now,
        )
        return [self._poly_row(m) for m in data if m.get("enableOrderBook", True)]

    def _poly_search(self, q: str) -> list[dict]:
        data = self._get(f"{GAMMA}/public-search", q=q, limit_per_type=25, events_status="active")
        rows = []
        for event in data.get("events") or []:
            for market in event.get("markets") or []:
                if market.get("closed") or not market.get("active", True):
                    continue
                rows.append(self._poly_row(market, event))
        rows.sort(key=lambda m: -(m["volume_24h"] or 0))
        return rows

    def _poly_row(self, market: dict, event: dict | None = None) -> dict:
        events = market.get("events") or ([event] if event else [])
        event_slug = (events[0] or {}).get("slug") if events else None
        outcomes = _json_list(market.get("outcomes"))
        prices = [_f(p) for p in _json_list(market.get("outcomePrices"))]
        yes_price = prices[0] if prices else None
        closed = bool(market.get("closed"))
        result = None
        if closed and len(prices) == 2 and None not in prices and max(prices) >= 0.99:
            result = "yes" if prices[0] >= 0.99 else "no"
        return {
            "venue": "polymarket",
            "id": str(market.get("id")),
            "title": market.get("question") or "",
            "subtitle": "" if market.get("groupItemTitle") in (None, "None") else market["groupItemTitle"],
            "event": event_slug or "",
            "category": "",
            "yes_bid": _f(market.get("bestBid")),
            "yes_ask": _f(market.get("bestAsk")),
            "last": _f(market.get("lastTradePrice")) if market.get("lastTradePrice") is not None else yes_price,
            "volume_24h": _f(market.get("volume24hr")),
            "volume": _f(market.get("volumeNum") or market.get("volume")),
            "open_interest": None,
            "close_time": market.get("endDate"),
            "status": "active" if market.get("active") and not closed else "closed",
            "result": result,
            "url": f"https://polymarket.com/event/{event_slug}" if event_slug else None,
            "tradable": False,
            "outcomes": [{"name": n, "price": prices[i] if i < len(prices) else None} for i, n in enumerate(outcomes)],
            "tokens": _json_list(market.get("clobTokenIds")),
        }

    def _poly_market(self, market_id: str) -> dict:
        row = self._poly_row(self._get(f"{GAMMA}/markets/{market_id}"))
        row["rules"] = ""
        row["book"] = {"bids": [], "asks": []}
        if row["tokens"]:
            try:
                book = self._get(f"{CLOB}/book", token_id=row["tokens"][0])
            except PredictionError:
                book = {}
            row["book"] = {
                "bids": sorted(([float(b["price"]), float(b["size"])] for b in book.get("bids") or []), key=lambda x: -x[0]),
                "asks": sorted(([float(a["price"]), float(a["size"])] for a in book.get("asks") or []), key=lambda x: x[0]),
            }
        return row

    # ---------- Kalshi demo trading ----------

    def kalshi_configured(self) -> bool:
        s = self.settings
        return bool(s.kalshi_demo_api_key_id and (s.kalshi_demo_private_key or s.kalshi_demo_private_key_path))

    def _private_key(self):
        if self._key is None:
            from cryptography.hazmat.primitives import serialization

            s = self.settings
            pem = s.kalshi_demo_private_key.replace("\\n", "\n")
            if not pem:
                pem = Path(s.kalshi_demo_private_key_path).expanduser().read_text()
            self._key = serialization.load_pem_private_key(pem.encode(), password=None)
        return self._key

    def sign(self, timestamp_ms: str, method: str, path: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        message = f"{timestamp_ms}{method.upper()}{path.split('?')[0]}".encode()
        signature = self._private_key().sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _kalshi_auth(self, method: str, path: str, body: dict | None = None, **params) -> dict:
        if not self.kalshi_configured():
            raise PredictionError(
                409,
                "Kalshi demo keys are not set. Add KALSHI_DEMO_API_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH to backend/.env.",
            )
        url = f"{KALSHI_DEMO}{path}"
        ts = str(int(time.time() * 1000))
        headers = {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_demo_api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self.sign(ts, method, urlsplit(url).path),
        }
        try:
            response = self.http.request(method, url, headers=headers, json=body, params=params or None)
        except httpx.HTTPError as exc:
            raise PredictionError(502, f"Kalshi demo unreachable: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not response.is_success:
            error = data.get("error") if isinstance(data, dict) else None
            detail = (error or {}).get("message") if isinstance(error, dict) else None
            status = 400 if response.status_code in (400, 409, 422) else 502
            if response.status_code == 401:
                detail = "Kalshi demo rejected the API key. Check the key id and private key."
            raise PredictionError(status, detail or f"Kalshi demo returned {response.status_code}")
        return data

    def kalshi_portfolio(self) -> dict:
        balance = self._kalshi_auth("GET", "/portfolio/balance")
        positions = self._kalshi_auth("GET", "/portfolio/positions", limit=200, count_filter="position")
        orders = self._kalshi_auth("GET", "/portfolio/orders", limit=100)
        return {
            "balance": _money(balance, "balance"),
            "portfolio_value": _money(balance, "portfolio_value"),
            "positions": [
                {
                    "ticker": p.get("ticker"),
                    "position": _count(p, "position"),
                    "exposure": _money(p, "market_exposure"),
                    "realized_pnl": _money(p, "realized_pnl"),
                    "fees_paid": _money(p, "fees_paid"),
                    "traded": _money(p, "total_traded"),
                }
                for p in positions.get("market_positions") or []
                if _count(p, "position")
            ],
            "orders": [self._kalshi_order(o) for o in orders.get("orders") or []],
        }

    def _kalshi_order(self, o: dict) -> dict:
        return {
            "order_id": o.get("order_id"),
            "client_order_id": o.get("client_order_id"),
            "ticker": o.get("ticker"),
            "side": o.get("side"),
            "action": o.get("action"),
            "type": o.get("type"),
            "status": o.get("status"),
            "yes_price": _money(o, "yes_price"),
            "no_price": _money(o, "no_price"),
            "count": _count(o, "initial_count") or _count(o, "count"),
            "filled": _count(o, "fill_count"),
            "remaining": _count(o, "remaining_count"),
            "created_time": o.get("created_time"),
        }

    def kalshi_order(self, ticker: str, side: str, action: str, count: int, price: float,
                     client_order_id: str | None = None, time_in_force: str | None = None) -> dict:
        cents = round(price * 100)
        if not 1 <= cents <= 99:
            raise PredictionError(400, "price must be between 0.01 and 0.99")
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": "limit",
            "client_order_id": client_order_id or str(uuid.uuid4()),
            f"{side}_price": cents,
        }
        if time_in_force:
            body["time_in_force"] = time_in_force
        data = self._kalshi_auth("POST", "/portfolio/orders", body)
        return self._kalshi_order(data.get("order") or data)

    def kalshi_cancel(self, order_id: str) -> dict:
        data = self._kalshi_auth("DELETE", f"/portfolio/orders/{order_id}")
        return self._kalshi_order(data.get("order") or data)
