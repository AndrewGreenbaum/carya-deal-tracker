"""Real route coverage without starting migrations, ingestion or a database."""
import csv
import io
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from src import main
from src.common.csv_safety import safe_csv_cell

KEY = "test-access-key-0123456789-abcdef01"


def test_all_data_routes_reject_anonymous_calls_before_handlers(monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", KEY)
    client = TestClient(main.app)  # No lifespan: no database or scheduler.
    for route_path, operations in main.app.openapi()["paths"].items():
        if route_path == "/health":
            continue
        path = re.sub(r"\{[^}]+\}", "1", route_path)
        for method in set(operations) - {"parameters", "options"}:
            response = client.request(method, path)
            assert response.status_code == 401, (method, path, response.status_code)


def test_real_login_session_allows_funds_then_logout_revokes_it(monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", KEY)
    client = TestClient(main.app)
    response = client.post("/auth/login", json={"api_key": KEY})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    response = client.get("/funds", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/funds", headers=headers).status_code == 401
    assert client.get("/funds", headers={"X-API-Key": KEY}).status_code == 200


def test_startup_rejects_missing_key_before_any_migration(monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", "")
    migration = Mock()
    monkeypatch.setattr(main, "run_migrations", migration)
    with pytest.raises(RuntimeError, match="random API access key"):
        with TestClient(main.app):
            pass
    migration.assert_not_called()


@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1:A2)", "-WEBSERVICE(1)", "@SUM(1)", " \t=1+1", "\n=1+1"])
def test_csv_formula_text_is_neutralized(value):
    assert safe_csv_cell(value) == "'" + value


def test_csv_preserves_plain_names_numbers_and_telephone_numbers():
    for value in ["Acme, Inc.", "Jane Doe", "+15551234567", 4200, None, "-123.5"]:
        assert safe_csv_cell(value) == value


def test_authenticated_csv_export_escapes_untrusted_fields(monkeypatch):
    monkeypatch.setattr(main.settings, "api_keys", KEY)
    deal = SimpleNamespace(
        company=SimpleNamespace(name="=1+1"), round_type="Seed", amount="1M",
        announced_date=None, investors=[], lead_partner_name="@SUM(1)",
        enterprise_category="Software", is_enterprise_ai=True,
        verification_snippet="+WEBSERVICE(1)")
    result = Mock()
    result.scalars.return_value.all.return_value = [deal]
    database = SimpleNamespace(execute=AsyncMock(return_value=result))

    @asynccontextmanager
    async def session():
        yield database

    monkeypatch.setattr(main, "get_session", session)
    client = TestClient(main.app)
    assert client.get("/deals/export").status_code == 401
    token = client.post("/auth/login", json={"api_key": KEY}).json()["access_token"]
    response = client.get("/deals/export", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    row = list(csv.DictReader(io.StringIO(response.text)))[0]
    assert row["Company"] == "'=1+1"
    assert row["Lead Partner"] == "'@SUM(1)"
    assert row["Verification Snippet"] == "'+WEBSERVICE(1)"
    assert row["Round"] == "Seed"
