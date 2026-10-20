# Carya Deal Tracker

Carya collects funding announcements and extracts company, investor, and round details with Claude. A FastAPI backend handles scraping, validation, and duplicate matching; a React dashboard is used to review deals and manage a tracker.

The focus is lead investments in enterprise AI companies from a configured set of VC firms. Investment roles and amounts can be ambiguous, so the dashboard keeps source links and evidence snippets for review.

## Run locally

Requires Python 3.11+, Node.js 22+, and PostgreSQL.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with a local `DATABASE_URL` and the service keys needed for the sources you want to run. Settings are defined in [src/config/settings.py](src/config/settings.py). Set `API_KEYS` to a random access key of at least 32 characters before starting the API. Keep it out of `VITE_` variables and source control.

```sh
python -m uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Startup applies database migrations and starts the scraping scheduler. Use a development database. Scraping, extraction, and enrichment can make paid API calls.

In a second terminal:

```sh
cd frontend
npm ci
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev
```

The command sets the local Vite proxy target; without it, the proxy defaults to the older Railway address. `VITE_API_BASE` selects the dashboard API base URL for other environments.

## Checks

From the repository root, with the Python environment active:

```sh
python -m pytest tests -q
```

For the dashboard:

```sh
cd frontend
npm test
npm run typecheck
npm run build
```

Python tests exclude the `integration` marker by default. Run `python -m pytest -m integration` explicitly to call Anthropic with configured credentials. The regular suite covers extraction helpers, normalization, validation, and duplicate matching; live sources and database upgrades need separate checks.

## Working on it

[CLAUDE.md](CLAUDE.md) covers the pipeline, fund matching rules, enrichment, and troubleshooting. The main code is in `src/harvester`, `src/analyst`, `src/archivist`, and `src/scheduler`; endpoints are in `src/main.py`. The dashboard is in `frontend/` and database migrations are in `alembic/`.

Chat is disabled by default. Enabling it requires a same-origin WebSocket proxy and a `VITE_CARYA_CHAT_PATH` such as `/api/chat` at build time. Keep gateway credentials on the proxy server.

## History

Carya grew out of Funding News Tracker, and the earlier code history is included here. Imported commit dates are approximate reconstructions of the October 2025–August 2026 development period, when changes were committed in batches. October 2026 maintenance and security commits retain their recorded dates.
