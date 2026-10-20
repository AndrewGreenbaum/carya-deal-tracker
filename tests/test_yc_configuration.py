"""YC directory configuration regressions use synthetic keys and mocked HTTP."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.config.settings import Settings
from src.harvester.scrapers import ycombinator


def test_yc_credentials_have_no_hardcoded_defaults(monkeypatch):
    monkeypatch.delenv("YC_ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("YC_ALGOLIA_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.yc_algolia_app_id == ""
    assert settings.yc_algolia_api_key == ""


def test_yc_credentials_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("YC_ALGOLIA_APP_ID", "synthetic-app")
    monkeypatch.setenv("YC_ALGOLIA_API_KEY", "synthetic-search-key")
    settings = Settings(_env_file=None)
    assert settings.yc_algolia_app_id == "synthetic-app"
    assert settings.yc_algolia_api_key == "synthetic-search-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("app_id,api_key", [("", ""), ("", "synthetic-search-key"), ("synthetic-app", "")])
async def test_missing_yc_configuration_skips_http(monkeypatch, caplog, app_id, api_key):
    monkeypatch.setattr(ycombinator, "settings", SimpleNamespace(
        yc_algolia_app_id=app_id, yc_algolia_api_key=api_key))
    async with ycombinator.YCombinatorScraper() as scraper:
        scraper.client.post = AsyncMock()
        assert await scraper.fetch_companies_algolia("Winter 2026") == []
        scraper.client.post.assert_not_awaited()
    assert "YC Algolia config not set" in caplog.text
    assert "synthetic-search-key" not in caplog.text


@pytest.mark.asyncio
async def test_configured_yc_source_uses_supplied_credentials(monkeypatch):
    monkeypatch.setattr(ycombinator, "settings", SimpleNamespace(
        yc_algolia_app_id="synthetic-app", yc_algolia_api_key="synthetic-search-key"))
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["X-Algolia-Application-Id"] == "synthetic-app"
        assert request.headers["X-Algolia-API-Key"] == "synthetic-search-key"
        return httpx.Response(200, json={
            "hits": [{"name": "Synthetic Company", "slug": "synthetic-company"}],
            "nbHits": 1,
        })

    async with ycombinator.YCombinatorScraper() as scraper:
        await scraper.client.aclose()
        scraper.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        companies = await scraper.fetch_companies_algolia("Winter 2026")
    assert len(requests) == 1
    assert [company.name for company in companies] == ["Synthetic Company"]
