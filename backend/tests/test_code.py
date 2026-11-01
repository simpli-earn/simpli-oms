import base64


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _strategy(client, auth, strategy_id="alpha"):
    response = client.post("/api/strategies", json={"id": strategy_id, "cash_budget": 1000}, headers=auth)
    assert response.status_code == 201, response.text


def test_upload_and_download(client, auth):
    _strategy(client, auth)
    assert client.get("/api/strategies/alpha/code", headers=auth).status_code == 404
    response = client.put(
        "/api/strategies/alpha/code",
        json={
            "base_revision": 0,
            "files": [{"path": "strategy.json", "content_b64": _b64("{}")}, {"path": "alpha.py", "content_b64": _b64("print(1)")}],
            "schedule": {"hour": 15, "minute": 40, "weekdays": [1, 2, 3, 4, 5]},
            "updated_by": "laptop-a",
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert all(f["content_b64"] is None for f in response.json()["files"])

    got = client.get("/api/strategies/alpha/code", headers=auth).json()
    assert got["schedule"]["hour"] == 15
    assert {f["path"]: base64.b64decode(f["content_b64"]).decode() for f in got["files"]} == {
        "alpha.py": "print(1)",
        "strategy.json": "{}",
    }
    listed = client.get("/api/strategy-code", headers=auth).json()
    assert listed == [{**listed[0], "strategy_id": "alpha", "revision": 1, "files": 2, "size": 10}]


def test_stale_upload_is_rejected(client, auth):
    _strategy(client, auth)
    first = {"base_revision": 0, "files": [{"path": "alpha.py", "content_b64": _b64("a")}]}
    assert client.put("/api/strategies/alpha/code", json=first, headers=auth).status_code == 200
    stale = {"base_revision": 0, "files": [{"path": "alpha.py", "content_b64": _b64("b")}]}
    assert client.put("/api/strategies/alpha/code", json=stale, headers=auth).status_code == 409
    forced = client.put("/api/strategies/alpha/code", json={**stale, "force": True}, headers=auth)
    assert forced.json()["revision"] == 2


def test_deleted_files_disappear_and_schedule_is_kept(client, auth):
    _strategy(client, auth)
    body = {
        "base_revision": 0,
        "files": [{"path": "alpha.py", "content_b64": _b64("a")}, {"path": "versions/v1.py", "content_b64": _b64("a")}],
        "schedule": {"hour": 9},
    }
    client.put("/api/strategies/alpha/code", json=body, headers=auth)
    client.put("/api/strategies/alpha/code", json={"base_revision": 1, "deleted": ["versions/v1.py"]}, headers=auth)
    got = client.get("/api/strategies/alpha/code", headers=auth).json()
    assert [f["path"] for f in got["files"]] == ["alpha.py"]
    assert got["schedule"] == {"hour": 9}
    from sqlalchemy import text

    engine = client.app.state.session_factory.kw["bind"]
    with engine.connect() as conn:  # only the latest copy is stored: the removed file's row is gone
        assert conn.execute(text("select path from strategy_files")).scalars().all() == ["alpha.py"]
    # Re-adding a deleted path brings it back.
    client.put("/api/strategies/alpha/code", json={"base_revision": 2, "files": [body["files"][1]]}, headers=auth)
    got = client.get("/api/strategies/alpha/code", headers=auth).json()
    assert [f["path"] for f in got["files"]] == ["alpha.py", "versions/v1.py"]


def test_rejects_unknown_strategy_and_bad_paths(client, auth):
    body = {"base_revision": 0, "files": [{"path": "a.py", "content_b64": _b64("a")}]}
    assert client.put("/api/strategies/nope/code", json=body, headers=auth).status_code == 404
    _strategy(client, auth)
    for path in ("../evil.py", "/etc/passwd", "a/../../b.py", ".."):
        bad = {"base_revision": 0, "files": [{"path": path, "content_b64": _b64("a")}]}
        assert client.put("/api/strategies/alpha/code", json=bad, headers=auth).status_code == 422, path
    assert client.get("/api/strategy-code").status_code == 422  # no API key


def test_missing_tables_give_a_clear_503(client, auth):
    from sqlalchemy import text

    engine = client.app.state.session_factory.kw["bind"]
    with engine.begin() as conn:
        conn.execute(text("drop table strategy_files"))
    response = client.get("/api/strategy-code", headers=auth)
    assert response.status_code == 503
    assert "add_strategy_code.sql" in response.json()["detail"]
