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

## Private access

Before starting the API, set `API_KEYS` on the server to a random access key of at least 32 characters. For example, generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and save it in your private environment configuration. Empty, short, or default keys stop API startup before database work begins. Do not put this key in a `VITE_` variable or commit it.

The dashboard now opens a sign-in screen. Enter the access key there; the browser keeps a signed one-hour session in this tab's session storage and does not save the access key. Sign out revokes that session. API clients can continue using the `X-API-Key` header. Private responses and exports are not cached, and export credentials are sent in headers rather than URLs. Use HTTPS outside localhost.

Run one API worker per deployment. Sessions and login throttling are held in that process, so restarting it signs users out. Multiple workers or replicas require a shared session store before scaling. The supplied startup commands use one worker.

Set `FRONTEND_URL` to your dashboard origin when hosting it separately; `VITE_API_BASE` can point the public frontend to the API but must contain no credentials. The aircraft stream uses the same expiring session.

The local PostgreSQL Compose service now requires `POSTGRES_PASSWORD` and binds only to `127.0.0.1`. Update `DATABASE_URL` to match your chosen password. An existing database volume retains its existing password; changing Compose configuration alone does not change that password.

CSV exports quote formula-shaped text so it remains text when opened in a spreadsheet. Plain phone numbers and signed numbers retain their existing format.

Authentication and export regressions run with `python -m pytest tests -q`. Browser-session tests run with `cd frontend && npm test`; `npm run build` checks the production frontend. Tests use synthetic credentials and local mocked services.
