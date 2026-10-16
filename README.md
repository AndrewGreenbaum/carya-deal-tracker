# Carya Eagle Eye

A deal-intelligence service that collects funding announcements, extracts structured company and investor details, filters duplicate records, and serves an analyst dashboard.

## Local setup

Requires Python 3.11+, Node.js 20+, and PostgreSQL for the running backend.

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

## Validation

```sh
python -m pytest tests -q
cd frontend
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

Bud Tracker and Carya Eagle Eye share substantial source history and functionality. Extraction remains dependent on article quality; ambiguous investment roles need analyst review. No accuracy or performance result is implied by the presence of a feature or test.
