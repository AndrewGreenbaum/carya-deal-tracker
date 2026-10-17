# Carya Deal Tracker

A deal-intelligence service that collects funding announcements, extracts structured company and investor details, filters duplicate records, and serves an analyst dashboard.

## Local setup

Requires Python 3.11+, Node.js 22+, and PostgreSQL for the running backend.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure a local `DATABASE_URL` and the relevant service keys using `.env.example` as a reference. Start the API with:

```sh
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Startup applies database migrations and starts the scraping scheduler. Use a development database; ingestion and extraction need configured external services and may incur API charges.

Start the dashboard from a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

The dashboard's API base URL is configured through its Vite environment settings.

The frontend requires Node.js 22 or newer (the container uses Node.js 24). Its styling uses Tailwind CSS 4; the custom theme is defined in `frontend/src/index.css`.

Carya chat is disabled by default. To enable it, set `VITE_CARYA_CHAT_PATH` to a same-origin path such as `/api/chat` at build time, and provide an authenticated WebSocket proxy at that path. The proxy must keep gateway credentials server-side. Do not put a gateway token in a Vite variable or client source. The previously committed token should be revoked or rotated because removing it does not remove Git history.

## Validation

```sh
python -m pytest tests -q
cd frontend
npm test
npm run typecheck
npm run build
```

The default Python run excludes tests marked `integration`. Seven extraction cases call Anthropic and require an explicit `python -m pytest -m integration` run with credentials. Unit tests cover normalization, validation, extraction helpers and duplicate matching. A passing unit suite does not verify source availability, migration behavior against an existing database, or live extraction quality.

## Structure

- `src/harvester`: article collection and orchestration
- `src/analyst`: structured extraction and validation
- `src/archivist`: SQL models, persistence and deduplication
- `src/scheduler`: scheduled scans and notifications
- `src/main.py`: FastAPI endpoints
- `frontend`: React/TypeScript dashboard
- `alembic`: database migrations

Funding News Tracker and Carya Deal Tracker share substantial source history and functionality. Extraction remains dependent on article quality; ambiguous investment roles need analyst review. No accuracy or performance result is implied by the presence of a feature or test.
