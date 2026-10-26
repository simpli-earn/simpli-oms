from fastapi.testclient import TestClient
from alpaca.trading.enums import OrderClass, PositionIntent

from app.config import Settings
from app.main import create_app
from app.services.broker import SubmitLeg, SubmitRequest, build_alpaca_request
from app.services.sync import sync_once
from tests.fake_broker import FakeBroker


def _strategy(client, auth, strategy_id="alpha", cash=1000):
    response = client.post(
        "/api/strategies",
        json={"id": strategy_id, "cash_budget": cash},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _equity(strategy_id="alpha", **overrides):
    body = {
        "strategy_id": strategy_id,
        "symbol": "AAPL",
        "side": "buy",
        "type": "limit",
        "qty": 2,
        "limit_price": 100,
        "time_in_force": "day",
    }
    body.update(overrides)
    return body


def test_registers_strategy(client, auth):
    created = _strategy(client, auth)
    assert created["id"] == "alpha"
    assert created["cash_remaining"] == 1000
    listed = client.get("/api/strategies", headers=auth)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == "alpha"


def test_two_backends_share_one_database(tmp_path, auth):
    database_url = f"sqlite:///{tmp_path}/shared.db"
    settings = Settings(
        _env_file=None,
        oms_api_key="test-key",
        oms_database_url=database_url,
        oms_sync_enabled=False,
        apca_api_key_id="test-key",
        apca_api_secret_key="test-secret",
    )
    first_app = create_app(settings, FakeBroker())
    second_app = create_app(settings, FakeBroker())
    with TestClient(first_app) as first, TestClient(second_app) as second:
        created = first.post(
            "/api/strategies",
            json={"id": "shared", "cash_budget": 500},
            headers=auth,
        )
        assert created.status_code == 201
        seen_elsewhere = second.get("/api/strategies/shared", headers=auth)
        assert seen_elsewhere.status_code == 200
        assert seen_elsewhere.json()["cash_budget"] == 500
        order = first.post(
            "/api/orders",
            json=_equity("shared", qty=1, limit_price=100),
            headers=auth,
        )
        assert order.status_code == 201
        shared_orders = second.get("/api/orders", headers=auth)
        assert shared_orders.status_code == 200
        assert shared_orders.json()[0]["id"] == order.json()["id"]
        assert shared_orders.json()[0]["strategy_id"] == "shared"


def test_health_checks_database(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "sqlite"}


def test_rejects_duplicate_strategy(client, auth):
    _strategy(client, auth)
    again = client.post("/api/strategies", json={"id": "alpha", "cash_budget": 10}, headers=auth)
    assert again.status_code == 409


def test_rejects_missing_api_key(client):
    response = client.get("/api/strategies", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_unknown_strategy_is_rejected(client, auth):
    response = client.post("/api/orders", json=_equity("missing"), headers=auth)
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown strategy"


def test_debit_over_budget_is_rejected(client, auth):
    _strategy(client, auth, cash=50)
    response = client.post("/api/orders", json=_equity(qty=1, limit_price=100), headers=auth)
    assert response.status_code == 400
    assert "below order cost" in response.json()["detail"]


def test_equity_notional_market_buy_is_forwarded(client, auth, fake_broker):
    _strategy(client, auth, cash=1000)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "symbol": "SPY",
            "side": "buy",
            "type": "market",
            "notional": 100,
            "time_in_force": "day",
        },
        headers=auth,
    )

    assert response.status_code == 201, response.text
    assert response.json()["notional"] == 100
    assert fake_broker.submitted[0].notional == 100
    built = build_alpaca_request(fake_broker.submitted[0])
    assert built.notional == 100
    assert built.qty is None


def test_notional_buy_is_checked_against_strategy_cash(client, auth):
    _strategy(client, auth, cash=50)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "symbol": "SPY",
            "side": "buy",
            "type": "market",
            "notional": 100,
        },
        headers=auth,
    )

    assert response.status_code == 400
    assert "100.00" in response.json()["detail"]


def test_option_cost_uses_contract_multiplier(client, auth):
    _strategy(client, auth, cash=100)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "symbol": "AAPL250117C00150000",
            "side": "buy",
            "position_intent": "buy_to_open",
            "type": "limit",
            "qty": 1,
            "limit_price": 1.5,
        },
        headers=auth,
    )
    assert response.status_code == 400
    assert "150.00" in response.json()["detail"]


def test_multi_leg_payload_is_forwarded(client, auth, fake_broker):
    _strategy(client, auth, cash=1000)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "order_class": "mleg",
            "type": "limit",
            "qty": 1,
            "limit_price": 0.6,
            "time_in_force": "day",
            "legs": [
                {
                    "symbol": "aapl250117c00150000",
                    "ratio_qty": 1,
                    "side": "buy",
                    "position_intent": "buy_to_open",
                },
                {
                    "symbol": "AAPL250117P00150000",
                    "ratio_qty": 1,
                    "side": "buy",
                    "position_intent": "buy_to_open",
                },
            ],
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["order_class"] == "mleg"
    assert [leg["symbol"] for leg in body["legs"]] == [
        "AAPL250117C00150000",
        "AAPL250117P00150000",
    ]
    forwarded = fake_broker.submitted[0]
    assert forwarded.order_class == "mleg"
    assert forwarded.limit_price == 0.6
    assert [leg.symbol for leg in forwarded.legs] == [
        "AAPL250117C00150000",
        "AAPL250117P00150000",
    ]
    assert forwarded.legs[0].position_intent == "buy_to_open"


def test_multi_leg_alpaca_request_shape():
    request = SubmitRequest(
        client_order_id="order-1",
        order_class="mleg",
        asset_class="us_option",
        symbol=None,
        side=None,
        type="limit",
        qty=1,
        limit_price=0.6,
        stop_price=None,
        time_in_force="day",
        position_intent=None,
        legs=[
            SubmitLeg("AAPL250117C00150000", 1, "buy", "buy_to_open"),
            SubmitLeg("AAPL250117P00150000", 1, "buy", "buy_to_open"),
        ],
    )
    built = build_alpaca_request(request)
    assert built.order_class == OrderClass.MLEG
    assert built.limit_price == 0.6
    assert built.symbol is None
    assert len(built.legs) == 2
    assert built.legs[0].position_intent == PositionIntent.BUY_TO_OPEN


def test_repeated_client_order_id_returns_the_original(client, auth, fake_broker):
    _strategy(client, auth)
    body = _equity(client_order_id="idea-1")
    first = client.post("/api/orders", json=body, headers=auth)
    second = client.post("/api/orders", json=body, headers=auth)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(fake_broker.submitted) == 1
    assert client.get("/api/strategies/alpha", headers=auth).json()["cash_remaining"] == 800


def test_sell_requires_a_strategy_position(client, auth):
    _strategy(client, auth)
    response = client.post("/api/orders", json=_equity(side="sell", qty=1), headers=auth)
    assert response.status_code == 400
    assert "not enough AAPL" in response.json()["detail"]


def test_sell_to_open_does_not_require_a_position(client, auth):
    _strategy(client, auth, cash=100)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "symbol": "AAPL250117P00150000",
            "side": "sell",
            "position_intent": "sell_to_open",
            "type": "limit",
            "qty": 1,
            "limit_price": 1.25,
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text


def test_fill_updates_cash_position_and_symbol_detail(client, auth, fake_broker):
    _strategy(client, auth, cash=1000)
    created = client.post("/api/orders", json=_equity(), headers=auth)
    assert created.status_code == 201, created.text
    reserved = client.get("/api/strategies/alpha", headers=auth).json()
    assert reserved["cash_remaining"] == 800
    assert reserved["cash_reserved"] == 200

    remote = fake_broker.orders[created.json()["alpaca_order_id"]]
    remote.status = "partially_filled"
    remote.filled_qty = 1
    remote.filled_avg_price = 100
    sync_once(client.app.state.session_factory, fake_broker)
    partial = client.get("/api/strategies/alpha", headers=auth).json()
    assert partial["cash_remaining"] == 800
    assert partial["cash_reserved"] == 100

    remote.status = "filled"
    remote.filled_qty = 2
    remote.filled_avg_price = 101
    sync_once(client.app.state.session_factory, fake_broker)
    strategy = client.get("/api/strategies/alpha", headers=auth).json()
    assert strategy["cash_remaining"] == 798
    assert strategy["cash_reserved"] == 0
    book = client.get("/api/strategies/alpha/book", headers=auth).json()
    assert book["positions"][0]["qty"] == 2
    assert book["positions"][0]["avg_price"] == 101

    too_many = client.post("/api/orders", json=_equity(side="sell", qty=3, limit_price=110), headers=auth)
    assert too_many.status_code == 400
    sold = client.post("/api/orders", json=_equity(side="sell", qty=2, limit_price=110), headers=auth)
    assert sold.status_code == 201, sold.text
    sale = fake_broker.orders[sold.json()["alpaca_order_id"]]
    sale.status = "filled"
    sale.filled_qty = 2
    sale.filled_avg_price = 110
    sync_once(client.app.state.session_factory, fake_broker)

    strategy = client.get("/api/strategies/alpha", headers=auth).json()
    assert strategy["cash_remaining"] == 1018
    assert strategy["cash_reserved"] == 0
    assert strategy["realized_pnl"] == 18
    detail = client.get("/api/positions/AAPL", headers=auth).json()
    assert detail["strategies"][0]["strategy_id"] == "alpha"
    assert len(detail["strategies"][0]["trades"]) == 3


def test_notional_buy_filled_under_clears_reserved_cash(client, auth, fake_broker):
    _strategy(client, auth, cash=1000)
    created = client.post(
        "/api/orders",
        json=_equity(type="market", qty=None, limit_price=None, notional=39.60),
        headers=auth,
    )
    assert created.status_code == 201, created.text

    remote = fake_broker.orders[created.json()["alpaca_order_id"]]
    remote.status = "filled"
    remote.filled_qty = 0.1786
    remote.filled_avg_price = 221.6686  # $39.59, a cent under the notional
    sync_once(client.app.state.session_factory, fake_broker)

    strategy = client.get("/api/strategies/alpha", headers=auth).json()
    assert strategy["cash_reserved"] == 0
    assert strategy["cash_remaining"] == 960.41


def test_credit_multi_leg_fill_adds_cash(client, auth, fake_broker):
    _strategy(client, auth, cash=100)
    response = client.post(
        "/api/orders",
        json={
            "strategy_id": "alpha",
            "order_class": "mleg",
            "type": "limit",
            "qty": 1,
            "limit_price": -0.4,
            "legs": [
                {
                    "symbol": "AAPL250117C00150000",
                    "ratio_qty": 1,
                    "side": "buy",
                    "position_intent": "buy_to_open",
                },
                {
                    "symbol": "AAPL250117C00160000",
                    "ratio_qty": 1,
                    "side": "sell",
                    "position_intent": "sell_to_open",
                },
            ],
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    remote = fake_broker.orders[response.json()["alpaca_order_id"]]
    remote.status = "filled"
    remote.filled_qty = 1
    remote.filled_avg_price = -0.4
    remote.legs[0].filled_qty = 1
    remote.legs[0].filled_avg_price = 1.0
    remote.legs[1].filled_qty = 1
    remote.legs[1].filled_avg_price = 1.4
    sync_once(client.app.state.session_factory, fake_broker)
    book = client.get("/api/strategies/alpha/book", headers=auth).json()
    assert book["strategy"]["cash_remaining"] == 140
    qty_by_symbol = {item["symbol"]: item["qty"] for item in book["positions"]}
    assert qty_by_symbol["AAPL250117C00150000"] == 1
    assert qty_by_symbol["AAPL250117C00160000"] == -1


def test_broker_failure_is_stored_and_not_resent(client, auth, fake_broker):
    _strategy(client, auth)
    fake_broker.fail_submit = True
    body = _equity(client_order_id="once")
    failed = client.post("/api/orders", json=body, headers=auth)
    assert failed.status_code == 502
    again = client.post("/api/orders", json=body, headers=auth)
    assert again.status_code == 200
    assert again.json()["status"] == "rejected"
    assert fake_broker.submitted == []
    restored = client.get("/api/strategies/alpha", headers=auth).json()
    assert restored["cash_remaining"] == 1000
    assert restored["cash_reserved"] == 0


def test_open_debit_blocks_a_second_order(client, auth):
    _strategy(client, auth, cash=150)
    first = client.post("/api/orders", json=_equity(qty=1, limit_price=100), headers=auth)
    assert first.status_code == 201, first.text
    cash = client.get("/api/strategies/alpha", headers=auth).json()
    assert cash["cash_remaining"] == 50
    assert cash["cash_reserved"] == 100
    second = client.post("/api/orders", json=_equity(qty=1, limit_price=100), headers=auth)
    assert second.status_code == 400
    assert "below order cost" in second.json()["detail"]


def test_cancel_returns_reserved_cash_after_sync(client, auth, fake_broker):
    _strategy(client, auth, cash=1000)
    created = client.post("/api/orders", json=_equity(), headers=auth)
    assert created.status_code == 201, created.text
    canceled = client.delete(f"/api/orders/{created.json()['id']}", headers=auth)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "pending_cancel"
    held = client.get("/api/strategies/alpha", headers=auth).json()
    assert held["cash_remaining"] == 800
    sync_once(client.app.state.session_factory, fake_broker)
    released = client.get("/api/strategies/alpha", headers=auth).json()
    assert released["cash_remaining"] == 1000
    assert released["cash_reserved"] == 0


def test_cancel_marks_order_pending_cancel(client, auth, fake_broker):
    _strategy(client, auth)
    created = client.post("/api/orders", json=_equity(), headers=auth)
    order_id = created.json()["id"]
    canceled = client.delete(f"/api/orders/{order_id}", headers=auth)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "pending_cancel"
    assert fake_broker.orders[created.json()["alpaca_order_id"]].status == "canceled"


def test_account_reads_the_broker(client, auth, fake_broker):
    fake_broker.positions = [
        {
            "symbol": "AAPL",
            "qty": 2,
            "avg_entry_price": 100,
            "market_value": 220,
            "unrealized_pl": 20,
            "current_price": 110,
            "side": "long",
            "asset_class": "us_equity",
        }
    ]
    account = client.get("/api/account", headers=auth)
    positions = client.get("/api/positions", headers=auth)
    assert account.status_code == 200
    assert account.json()["equity"] == 100000
    assert positions.json()[0]["symbol"] == "AAPL"


def test_empty_portfolio_is_an_empty_result_not_an_error(client, auth):
    account_positions = client.get("/api/positions", headers=auth)
    strategy_positions = client.get("/api/strategy-positions", headers=auth)
    fills = client.get("/api/fills", headers=auth)
    detail = client.get("/api/positions/NOTHELD", headers=auth)

    assert account_positions.status_code == 200
    assert account_positions.json() == []
    assert strategy_positions.status_code == 200
    assert strategy_positions.json() == []
    assert fills.status_code == 200
    assert fills.json() == []
    assert detail.status_code == 200
    assert detail.json()["account_position"] is None
    assert detail.json()["account_error"] is None
