import base64
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.predictions import Predictions
from tests.fake_broker import FakeBroker

KALSHI_EVENTS = {
    "events": [
        {
            "event_ticker": "KXRAIN-26",
            "series_ticker": "KXRAIN",
            "title": "Rain in NYC?",
            "category": "Climate",
            "markets": [
                {"ticker": "KXRAIN-26-Y", "event_ticker": "KXRAIN-26", "title": "Rain in NYC?",
                 "yes_bid_dollars": "0.4100", "yes_ask_dollars": "0.4400", "last_price_dollars": "0.4200",
                 "volume_24h_fp": "10.00", "volume_fp": "50.00", "status": "active"},
                {"ticker": "KXRAIN-26-Z", "event_ticker": "KXRAIN-26", "title": "Snow in NYC?",
                 "yes_bid": 5, "yes_ask": 9, "last_price": 7, "volume_24h": 99, "status": "active"},
            ],
        }
    ],
    "cursor": "",
}

POLY_MARKETS = [
    {"id": 12, "question": "Will it rain?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.3", "0.7"]',
     "bestBid": 0.29, "bestAsk": 0.31, "lastTradePrice": 0.3, "volume24hr": 1000, "volumeNum": 5000,
     "active": True, "closed": False, "clobTokenIds": '["111", "222"]', "events": [{"slug": "rain"}]},
]


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _app(handler, **settings):
    s = Settings(_env_file=None, oms_api_key="test-key", oms_database_url="sqlite://",
                 oms_sync_enabled=False, **settings)
    predictions = Predictions(s, transport=httpx.MockTransport(handler))
    return TestClient(create_app(s, FakeBroker(), predictions)), predictions


AUTH = {"X-API-Key": "test-key"}


def test_kalshi_markets_normalize_both_price_formats():
    def handler(request):
        assert request.url.path == "/trade-api/v2/events"
        return httpx.Response(200, json=KALSHI_EVENTS)

    client, _ = _app(handler)
    rows = client.get("/api/predictions/markets?venue=kalshi", headers=AUTH).json()
    assert [r["id"] for r in rows] == ["KXRAIN-26-Z", "KXRAIN-26-Y"]  # busiest first
    snow, rain = rows
    assert (snow["yes_bid"], snow["yes_ask"], snow["last"]) == (0.05, 0.09, 0.07)
    assert (rain["yes_bid"], rain["yes_ask"]) == (0.41, 0.44)
    assert rain["url"] == "https://kalshi.com/markets/kxrain"
    assert rain["tradable"] is False

    found = client.get("/api/predictions/markets?venue=kalshi&q=snow", headers=AUTH).json()
    assert [r["id"] for r in found] == ["KXRAIN-26-Z"]


def test_kalshi_orderbook_turns_no_bids_into_yes_asks():
    def handler(request):
        if request.url.path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {
                "yes_dollars": [["0.4000", "5.00"], ["0.4100", "2.00"]],
                "no_dollars": [["0.5500", "3.00"]]}})
        return httpx.Response(200, json={"market": KALSHI_EVENTS["events"][0]["markets"][0]})

    client, _ = _app(handler)
    market = client.get("/api/predictions/markets/kalshi/KXRAIN-26-Y", headers=AUTH).json()
    assert market["book"]["bids"] == [[0.41, 2.0], [0.4, 5.0]]
    assert market["book"]["asks"] == [[0.45, 3.0]]


def test_polymarket_markets_and_book():
    def handler(request):
        if request.url.host == "clob.polymarket.com":
            assert request.url.params["token_id"] == "111"
            return httpx.Response(200, json={"bids": [{"price": "0.29", "size": "100"}],
                                             "asks": [{"price": "0.31", "size": "40"}]})
        if request.url.path == "/markets/12":
            return httpx.Response(200, json=POLY_MARKETS[0])
        return httpx.Response(200, json=POLY_MARKETS)

    client, _ = _app(handler)
    rows = client.get("/api/predictions/markets?venue=polymarket", headers=AUTH).json()
    assert rows[0]["outcomes"] == [{"name": "Yes", "price": 0.3}, {"name": "No", "price": 0.7}]
    assert rows[0]["url"] == "https://polymarket.com/event/rain"
    market = client.get("/api/predictions/markets/polymarket/12", headers=AUTH).json()
    assert market["book"] == {"bids": [[0.29, 100.0]], "asks": [[0.31, 40.0]]}


def test_unknown_venue_and_auth():
    client, _ = _app(lambda r: httpx.Response(500))
    assert client.get("/api/predictions/markets?venue=nope", headers=AUTH).status_code == 404
    assert client.get("/api/predictions/status").status_code == 422


def test_kalshi_orders_need_demo_keys():
    client, _ = _app(lambda r: httpx.Response(500))
    response = client.post("/api/predictions/kalshi/orders", headers=AUTH,
                           json={"ticker": "X", "side": "yes", "count": 1, "price": 0.4})
    assert response.status_code == 409
    assert "KALSHI_DEMO_API_KEY_ID" in response.json()["detail"]


def test_kalshi_demo_order_is_signed_and_sent_to_demo():
    key = _key()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        seen["body"] = json.loads(request.content)
        ts = request.headers["KALSHI-ACCESS-TIMESTAMP"]
        signature = base64.b64decode(request.headers["KALSHI-ACCESS-SIGNATURE"])
        key.public_key().verify(
            signature, f"{ts}POST/trade-api/v2/portfolio/orders".encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return httpx.Response(201, json={"order": {"order_id": "o1", "ticker": "KXRAIN-26-Y", "side": "no",
                                                   "action": "buy", "status": "resting", "no_price": 58,
                                                   "initial_count": 3, "fill_count": 0, "remaining_count": 3}})

    client, _ = _app(handler, kalshi_demo_api_key_id="kid", kalshi_demo_private_key=pem)
    response = client.post("/api/predictions/kalshi/orders", headers=AUTH,
                           json={"ticker": "KXRAIN-26-Y", "side": "no", "count": 3, "price": 0.58})
    assert response.status_code == 201, response.text
    assert seen["host"] == "demo-api.kalshi.co"
    assert seen["body"]["no_price"] == 58 and seen["body"]["type"] == "limit"
    assert response.json()["no_price"] == 0.58
    status = client.get("/api/predictions/status", headers=AUTH).json()
    assert status["kalshi_demo_configured"] is True
