from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_session
from app.main import app


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok_when_db_reachable(client):
    app.dependency_overrides[get_session] = lambda: MagicMock()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "FrogRadar", "db": "connected"}


def test_health_503_when_db_unreachable(client):
    session = MagicMock()
    session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    app.dependency_overrides[get_session] = lambda: session

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["db"] == "disconnected"
