# Access and hosting

Set `API_KEYS` to a random access key of at least 32 characters before starting the API. Empty, short, or default keys prevent startup. Keep the key on the server and enter it through the dashboard sign-in form.

The dashboard asks for the access key and exchanges it for a signed one-hour session. The browser keeps the session in this tab's session storage and does not save the key. Signing out revokes the session; API clients use `X-API-Key`. Export credentials travel in headers rather than URLs. Private HTTP responses use `Cache-Control: no-store`. Use HTTPS outside localhost.

Run one API worker. Sessions and login throttling are held in that process, so a restart signs users out. Multiple workers need a shared session store first.

For a separately hosted dashboard, set `FRONTEND_URL` to its origin. `VITE_API_BASE` can select the API address but must contain no credentials. The aircraft stream uses the same expiring session.

The local PostgreSQL Compose service requires `POSTGRES_PASSWORD` and binds to `127.0.0.1`. Set `DATABASE_URL` to match. Changing the Compose setting does not change the password in an existing database volume.

CSV exports escape formula-shaped text for spreadsheet use. Ordinary phone numbers and signed numbers keep their existing format.
