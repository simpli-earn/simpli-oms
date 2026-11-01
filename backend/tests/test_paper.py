import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from tests.fake_broker import FakeBroker

AUTH = {"X-API-Key": "test-key"}


class FakePredictions:
    """Order books by (venue, market id). Book prices are the YES side."""

    def __init__(self):
        self.markets = {}

    def set(self, venue, market_id, bids, asks, status="active", result=None, title="Rain?"):
        self.markets[(venue, market_id)] = {
            "venue": venue, "id": market_id, "title": title, "subtitle": "", "status": status, "result": result,
            "yes_bid": bids[0][0] if bids else None, "yes_ask": asks[0][0] if asks else None, "last": None,
            "book": {"bids": bids, "asks": asks},
        }

    def snapshot(self, venue, market_id):
        return self.markets[(venue, market_id)]

    quote = snapshot

    def status(self):
        return {"venues": [], "kalshi_demo_configured": False}


@pytest.fixture()
def env(tmp_path):
    fake = FakePredictions()
    settings = Settings(_env_file=None, oms_api_key="test-key", oms_database_url=f"sqlite:///{tmp_path}/oms.db",
                        oms_sync_enabled=False)
    app = create_app(settings, FakeBroker(), fake)
    with TestClient(app) as client:
        client.post("/api/strategies", json={"id": "mentions", "cash_budget": 1000}, headers=AUTH)
        yield client, fake, app


def fund(client, amount=100):
    r = client.post("/api/predictions/paper/accounts", json={"strategy_id": "mentions", "cash_budget": amount}, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def order(client, **body):
    payload = {"strategy_id": "mentions", "venue": "kalshi", "market_id": "RAIN", "outcome": "yes", "action": "buy",
               "type": "market", "count": 10, **body}
    r = client.post("/api/predictions/paper/orders", json=payload, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def account(client):
    return client.get("/api/predictions/paper/accounts/mentions", headers=AUTH).json()


def sync(app):
    app.state.paper.sync_once(app.state.session_factory)


def test_account_needs_registered_strategy(env):
    client, fake, _ = env
    r = client.post("/api/predictions/paper/accounts", json={"strategy_id": "nope", "cash_budget": 5}, headers=AUTH)
    assert r.status_code == 404
    fund(client)
    r = client.post("/api/predictions/paper/accounts", json={"strategy_id": "mentions", "cash_budget": 5}, headers=AUTH)
    assert r.status_code == 409
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.50, 100]])
    r = client.post("/api/predictions/paper/orders", headers=AUTH, json={
        "strategy_id": "other", "venue": "kalshi", "market_id": "RAIN", "outcome": "yes", "count": 1, "type": "market"})
    assert r.status_code == 404 and "Fund one first" in r.json()["detail"]


def test_market_buy_walks_asks_and_charges_kalshi_fee(env):
    client, fake, _ = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.42, 4], [0.45, 100]])
    o = order(client)
    assert o["status"] == "filled" and o["filled_count"] == 10
    assert o["avg_price"] == pytest.approx((4 * 0.42 + 6 * 0.45) / 10)
    # 0.07 * (4*.42*.58 + 6*.45*.55) = 0.17216 -> 0.18
    assert o["fees"] == 0.18
    book = account(client)
    assert book["account"]["cash"] == pytest.approx(100 - 4.38 - 0.18)
    pos = book["positions"][0]
    assert (pos["outcome"], pos["count"]) == ("yes", 10)
    assert pos["mark"] == pytest.approx(0.41)


def test_buy_no_trades_against_yes_bids(env):
    client, fake, _ = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.40, 3], [0.38, 100]], asks=[[0.42, 100]])
    o = order(client, outcome="no", count=5)
    assert o["avg_price"] == pytest.approx((3 * 0.60 + 2 * 0.62) / 5)


def test_limit_rests_reserves_cash_then_fills_as_maker(env):
    client, fake, app = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.30, 10]], asks=[[0.40, 10]])
    o = order(client, type="limit", limit_price=0.35, count=10)
    assert o["status"] == "open" and o["filled_count"] == 0
    a = account(client)["account"]
    assert a["cash"] == pytest.approx(96.5) and a["cash_reserved"] == pytest.approx(3.5)
    sync(app)  # book unchanged, still resting
    assert account(client)["orders"][0]["status"] == "open"
    fake.set("kalshi", "RAIN", bids=[[0.30, 10]], asks=[[0.33, 6], [0.35, 10]])
    sync(app)
    o = account(client)["orders"][0]
    assert o["status"] == "filled" and o["fees"] == 0
    a = account(client)["account"]
    assert a["cash_reserved"] == 0
    assert a["cash"] == pytest.approx(100 - (6 * 0.33 + 4 * 0.35))


def test_cancel_releases_reserve(env):
    client, fake, _ = env
    fund(client)
    fake.set("polymarket", "77", bids=[[0.10, 10]], asks=[[0.90, 10]])
    o = order(client, venue="polymarket", market_id="77", type="limit", limit_price=0.2, count=50)
    r = client.delete(f"/api/predictions/paper/orders/{o['id']}", headers=AUTH)
    assert r.json()["status"] == "canceled"
    assert account(client)["account"]["cash"] == 100


def test_sell_is_capped_by_holdings_and_realizes_pnl(env):
    client, fake, _ = env
    fund(client)
    fake.set("polymarket", "77", bids=[[0.50, 100]], asks=[[0.52, 100]])
    order(client, venue="polymarket", market_id="77", count=10)
    r = order(client, venue="polymarket", market_id="77", action="sell", count=11)
    assert r["status"] == "rejected" and "only 10" in r["reject_reason"]
    fake.set("polymarket", "77", bids=[[0.70, 100]], asks=[[0.72, 100]])
    s = order(client, venue="polymarket", market_id="77", action="sell", count=10)
    assert s["status"] == "filled"
    a = account(client)["account"]
    assert a["realized_pnl"] == pytest.approx(10 * (0.70 - 0.52))
    assert a["cash"] == pytest.approx(100 + 1.8)  # polymarket has no fee


def test_rejects_when_cash_is_short_and_fractional_kalshi(env):
    client, fake, _ = env
    fund(client, 5)
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.50, 100]])
    r = order(client, count=20)
    assert r["status"] == "rejected" and "spendable" in r["reject_reason"]
    r = client.post("/api/predictions/paper/orders", headers=AUTH, json={
        "strategy_id": "mentions", "venue": "kalshi", "market_id": "RAIN", "outcome": "yes", "count": 1.5, "type": "market"})
    assert r.status_code == 400


def test_settlement_pays_winners(env):
    client, fake, app = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.50, 100]])
    order(client, count=10)  # 10 YES at 0.50 + 0.18 fee
    order(client, outcome="no", count=10)  # 10 NO at 0.60 + 0.17 fee
    cash = account(client)["account"]["cash"]
    fake.set("kalshi", "RAIN", bids=[], asks=[], status="finalized", result="yes")
    sync(app)
    book = account(client)
    assert book["positions"] == []
    assert book["account"]["cash"] == pytest.approx(cash + 10)
    assert book["account"]["realized_pnl"] == pytest.approx(10 * 0.5 - 10 * 0.6)
    assert {f["action"] for f in book["fills"]} == {"buy", "settle"}


def test_client_order_id_is_idempotent(env):
    client, fake, _ = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.50, 100]])
    first = order(client, count=1, client_order_id="m-1")
    second = order(client, count=1, client_order_id="m-1")
    assert first["id"] == second["id"]
    assert len(account(client)["orders"]) == 1


def test_missing_tables_say_how_to_fix(env):
    client, _, app = env
    with app.state.session_factory() as db:
        for table in ("pm_fills", "pm_positions", "pm_orders", "pm_accounts"):
            db.execute(text(f"drop table {table}"))
        db.commit()
    app.state.paper._ready = False
    r = client.get("/api/predictions/paper/accounts", headers=AUTH)
    assert r.status_code == 503 and "supabase_predictions.sql" in r.json()["detail"]
    assert client.get("/api/predictions/status", headers=AUTH).json()["paper_ready"] is False


def test_kalshi_fills_whole_contracts_only(env):
    client, fake, _ = env
    fund(client)
    fake.set("kalshi", "RAIN", bids=[[0.40, 50]], asks=[[0.42, 2.6], [0.45, 100]])
    o = order(client, count=5)
    assert o["filled_count"] == 5
    assert o["avg_price"] == pytest.approx((2 * 0.42 + 3 * 0.45) / 5)
    fake.set("polymarket", "77", bids=[[0.40, 50]], asks=[[0.42, 2.6], [0.45, 100]])
    p = order(client, venue="polymarket", market_id="77", count=5)
    assert p["avg_price"] == pytest.approx((2.6 * 0.42 + 2.4 * 0.45) / 5)
