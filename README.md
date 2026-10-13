# ScholarPulse

AI-assisted academic research search. Ask a question in natural language, search five academic databases at once, and get a short answer synthesised **only** from the abstracts of the papers found, with citations back to those papers.

- **Sources:** OpenAlex, Semantic Scholar, arXiv, Crossref, CORE
- **AI:** Google Gemini (primary) with Groq as fallback. Answers are grounded in the supplied abstracts; papers without an abstract are never used as evidence.
- **Accounts:** register, verify email, log in, reset password; bookmarks and synced search history for verified users. Guests can search and ask (rate limited).

## Stack

| Part | Technology |
|---|---|
| Frontend | Next.js (App Router), React, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI, SQLAlchemy |
| Database | PostgreSQL (SQLite works for tests only) |
| Cache | Redis (optional; falls back to in-memory) |
| Re-ranking | FastEmbed `BAAI/bge-small-en-v1.5` (optional, runs locally) |
| Email | SMTP (Brevo shown in `.env.example`) |

## Repository layout

```
backend/    FastAPI app (main.py), tests (test_security.py), requirements*.txt, .env.example
frontend/   Next.js app, .env.example
```

## Run it locally

Prerequisites: Python 3.12+, Node.js 20+, PostgreSQL 14+. Redis is optional.

### 1. Database

```bash
sudo -u postgres psql <<'SQL'
CREATE USER scholarpulse WITH PASSWORD 'change-me';
CREATE DATABASE scholarpulse OWNER scholarpulse;
SQL
```

Tables are created automatically the first time the backend starts. There are no manual migrations.

### 2. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env`:

- `AUTH_SECRET`: generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `DATABASE_URL=postgresql://scholarpulse:change-me@localhost:5432/scholarpulse`
- `GEMINI_API_KEY` and/or `GROQ_API_KEY` (at least one, otherwise `/ask` returns 503)
- `DEV_MODE=true` for local work (non-Secure cookie, emails printed to the console instead of sent, `/docs` enabled). Leave `COOKIE_SECURE` and `ENABLE_DOCS` commented out so these dev defaults apply.

```bash
uvicorn main:app --reload --port 8000
```

Check it: <http://localhost:8000/health> returns `{"status":"healthy"}`. With `DEV_MODE=true`, interactive docs are at <http://localhost:8000/docs>.

### 3. Frontend

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>.

> Use `localhost` for both the app and the API, not `127.0.0.1`. The login cookie is `SameSite=Lax`, and the browser treats `localhost` and `127.0.0.1` as different sites, so mixing them looks like "login works but I'm logged out".

### 4. Try the account flow

Register, then copy the verification link from the backend console (DEV_MODE prints it), open it, log in, test password reset, log out.

### 5. Run the tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

No Postgres, Redis or API keys needed: tests use a throwaway SQLite file and fake AI clients.

## Environment variables

Every variable is documented in [`backend/.env.example`](backend/.env.example) and [`frontend/.env.example`](frontend/.env.example). The backend refuses to start without `AUTH_SECRET` (32+ characters unless `DEV_MODE=true`), `CORS_ORIGINS`, `DATABASE_URL` and `APP_URL`.

`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_APP_URL` are baked into the frontend **at build time**. `npm run build` fails if they are missing. In Docker, pass them as build args before the build step:

```dockerfile
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL
RUN npm run build
```

The build also downloads the Google Fonts used by the layout, so it needs outbound internet access.

## Production notes

- `DEV_MODE=false`; `COOKIE_SECURE` defaults to `true`. Serve both the app and API over HTTPS.
- Put the API behind a reverse proxy and set `TRUSTED_PROXY_COUNT=1` (one proxy that appends `X-Forwarded-For`). With the default `0`, that header is ignored and rate limits use the connection IP, which behind a proxy means every user shares one limit.
- The rate limiter is in memory and per process. Run a single Uvicorn worker, or move the limiter to Redis before scaling out.
- `/docs` and `/openapi.json` are off unless `ENABLE_DOCS=true`.
- `/health` returns only `{"status":"healthy"}` (503 if the database is unreachable); use it for Docker health checks and uptime monitors.
- Set `client_max_body_size` (nginx) to about 1 MB; the API also rejects bodies over `MAX_BODY_BYTES`.
- The first start with `ENABLE_RERANKING=true` downloads the embedding model. Bake it into the image or set `ENABLE_RERANKING=false`.

## Security model (short)

- Identity comes only from a signed, `HttpOnly` session cookie. `/ask` ignores any `user_id` in the request body.
- Bookmarks and history sync require a verified account (401 without a session, 403 if unverified).
- Changing or resetting a password invalidates all other sessions; deleting an account (password required) kills its sessions.
- Provider errors and server paths are logged server-side only; clients get generic messages.
