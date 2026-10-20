import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fake_broker import FakeBroker


@pytest.fixture()
def fake_broker():
    return FakeBroker()


@pytest.fixture()
def client(tmp_path, fake_broker):
    settings = Settings(
        _env_file=None,
        oms_api_key="test-key",
        oms_database_url=f"sqlite:///{tmp_path}/oms.db",
        oms_sync_enabled=False,
        apca_api_key_id="test-key",
        apca_api_secret_key="test-secret",
    )
    application = create_app(settings, fake_broker)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture()
def auth():
    return {"X-API-Key": "test-key"}
