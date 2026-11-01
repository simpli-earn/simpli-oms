def _strategy(client, auth, strategy_id="alpha"):
    response = client.post("/api/strategies", json={"id": strategy_id, "cash_budget": 1000}, headers=auth)
    assert response.status_code == 201, response.text


def _report(client, auth, host, strategies, app_open=True):
    response = client.put(f"/api/installs/{host}", json={"strategies": strategies, "app_open": app_open}, headers=auth)
    assert response.status_code == 204, response.text


def test_laptops_report_what_they_have(client, auth):
    _strategy(client, auth)
    schedule = {"hour": 15, "minute": 40, "weekdays": [1, 2, 3, 4, 5]}
    _report(client, auth, "laptop-a", [{"strategy_id": "alpha", "version": "v1", "schedule": schedule,
                                        "last_run_at": "2026-10-30T19:40:00Z", "last_run_status": "succeeded"}])
    _report(client, auth, "laptop-b", [{"strategy_id": "alpha", "version": "v2"}, {"strategy_id": "not-registered"}])
    got = {i["host"]: i for i in client.get("/api/strategies/alpha/installs", headers=auth).json()}
    assert set(got) == {"laptop-a", "laptop-b"}
    assert got["laptop-a"]["schedule"] == schedule and got["laptop-a"]["last_run_status"] == "succeeded"
    assert got["laptop-b"]["version"] == "v2" and got["laptop-b"]["schedule"] is None


def test_removed_from_a_laptop_drops_out(client, auth):
    _strategy(client, auth)
    _report(client, auth, "laptop-a", [{"strategy_id": "alpha"}])
    _report(client, auth, "laptop-a", [])
    assert client.get("/api/strategies/alpha/installs", headers=auth).json() == []
    _report(client, auth, "laptop-a", [{"strategy_id": "alpha"}])  # set up again
    assert [i["host"] for i in client.get("/api/strategies/alpha/installs", headers=auth).json()] == ["laptop-a"]


def test_a_run_with_the_app_closed_keeps_seen_at(client, auth):
    _strategy(client, auth)
    _report(client, auth, "laptop-a", [{"strategy_id": "alpha"}])
    seen = client.get("/api/strategies/alpha/installs", headers=auth).json()[0]["seen_at"]
    _report(client, auth, "laptop-a", [{"strategy_id": "alpha", "last_run_status": "succeeded"}], app_open=False)
    after = client.get("/api/strategies/alpha/installs", headers=auth).json()[0]
    assert after["seen_at"] == seen and after["last_run_status"] == "succeeded"


def test_missing_table_gives_a_clear_503(client, auth):
    from sqlalchemy import text

    engine = client.app.state.session_factory.kw["bind"]
    with engine.begin() as conn:
        conn.execute(text("drop table strategy_installs"))
    response = client.get("/api/strategies/alpha/installs", headers=auth)
    assert response.status_code == 503
    assert "add_strategy_installs.sql" in response.json()["detail"]
