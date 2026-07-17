"""
tests/unit/test_health.py
──────────────────────────
Unit tests for the health-check endpoint.

Uses Flask's built-in test client — no real server needed.
The app factory pattern means each test gets a clean isolated app.
"""

import pytest
from app.main import create_app
from app.core.config import settings


@pytest.fixture
def client():
    """Provide a test client with test configuration."""
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Root route ────────────────────────────────────────────────────────────────

def test_root_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


def test_root_contains_app_name(client):
    data = client.get("/").get_json()
    assert settings.APP_NAME in data["message"]


def test_root_has_version(client):
    data = client.get("/").get_json()
    assert "version" in data
    assert data["version"] == settings.APP_VERSION


# ── Health endpoint ───────────────────────────────────────────────────────────

def test_health_returns_200(client):
    response = client.get(f"{settings.API_PREFIX}/health")
    assert response.status_code == 200


def test_health_success_envelope(client):
    data = client.get(f"{settings.API_PREFIX}/health").get_json()
    assert data["success"] is True
    assert "data" in data
    assert "message" in data


def test_health_status_is_ok(client):
    data = client.get(f"{settings.API_PREFIX}/health").get_json()
    assert data["data"]["status"] == "ok"


def test_health_contains_app_name(client):
    data = client.get(f"{settings.API_PREFIX}/health").get_json()
    assert data["data"]["app_name"] == settings.APP_NAME


def test_health_contains_version(client):
    data = client.get(f"{settings.API_PREFIX}/health").get_json()
    assert data["data"]["version"] == settings.APP_VERSION


def test_health_contains_environment(client):
    data = client.get(f"{settings.API_PREFIX}/health").get_json()
    assert data["data"]["environment"] == settings.APP_ENV


# ── Error handlers ────────────────────────────────────────────────────────────

def test_404_returns_json(client):
    response = client.get("/this-does-not-exist")
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "NOT_FOUND"


def test_404_has_standard_envelope(client):
    data = client.get("/nonexistent").get_json()
    assert "success" in data
    assert "error" in data
    assert "message" in data