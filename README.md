# ScholarPulse — AI Academic Research Search

ScholarPulse is an AI-assisted academic discovery and literature-synthesis application. It searches multiple scholarly indexes, combines and deduplicates results, applies optional publication-year and open-access filters, and can produce concise answers grounded in the abstracts returned by those sources.

The project includes a Next.js web interface and a FastAPI service. Accounts support email verification, login, password reset/change, saved bookmarks, and synchronized search history. Guest users can search and ask questions subject to server-side rate limits.

> **Release status:** This repository has not yet been certified production-ready. Run the installation, automated tests, frontend lint/type/build checks, and deployment checklist in this document in the target environment. A Python syntax compilation check alone is not an end-to-end verification.

---

## Contents

- [Product overview](#product-overview)
- [Features](#features)
- [User and account behaviour](#user-and-account-behaviour)
- [Technology stack](#technology-stack)
- [Architecture and request flows](#architecture-and-request-flows)
- [Repository structure](#repository-structure)
- [Requirements](#requirements)
- [Local development setup](#local-development-setup)
- [Environment configuration](#environment-configuration)
- [API reference](#api-reference)
- [Database schema](#database-schema)
- [Academic data providers](#academic-data-providers)
- [AI answer grounding and citations](#ai-answer-grounding-and-citations)
- [Caching, rate limiting, and re-ranking](#caching-rate-limiting-and-re-ranking)
- [Testing and quality checks](#testing-and-quality-checks)
- [Production deployment](#production-deployment)
- [Security and privacy notes](#security-and-privacy-notes)
- [Operations and troubleshooting](#operations-and-troubleshooting)
- [Known limitations and production gates](#known-limitations-and-production-gates)
- [Release checklist](#release-checklist)

---

## Product overview

ScholarPulse is designed to help students, academics, and researchers discover literature without manually searching each provider one at a time. A user enters a topic or question, ScholarPulse queries academic sources, normalizes and deduplicates the results, and displays a combined result set. The user can then ask a question about the retrieved papers or request a deeper analysis of an individual paper.

The AI synthesis feature is deliberately constrained: only papers with abstracts are supplied as evidence for answers, and returned citations are checked against the supplied paper list. This improves traceability but does not guarantee that every generated statement is correct. Users should open and read the original publication before relying on a result in academic or professional work.

### Main application areas

| Area | Purpose |
|---|---|
| Academic search | Federated search across OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE |
| Filtering | Publication-year range and confirmed open-access filtering |
| AI question answering | Synthesize a concise response from retrieved paper abstracts |
| Paper deep dive | Generate a structured analysis from a paper title and abstract |
| Accounts | Registration, email verification, login, logout, and current-session lookup |
| Account recovery | Resend verification, forgot/reset password, and change password |
| Research library | Save and remove bookmarks for verified accounts |
| Search history | Keep guest history scoped to a session and synchronize eligible history to a verified account |
| User preferences | Frontend theme and cookie-consent preferences stored in the browser |

---

## Features

### Research discovery

- Search several scholarly data providers in one request.
- Normalize provider records into a common result structure.
- Deduplicate results, including records that share a DOI and records without a DOI that can be matched by title.
- Filter by minimum and maximum publication year.
- Optionally return only records positively identified as open access.
- Show source counts alongside the combined result set.
- Apply optional local semantic re-ranking using FastEmbed.
- Cache search responses to reduce repeated provider requests.
- Preserve abstracts as the evidence source for AI synthesis; records without abstracts are not used as evidence.

### AI-assisted research

- Ask a natural-language question about the retrieved papers.
- Use Google Gemini as the preferred AI provider and Groq as a fallback when configured.
- Request structured AI output and validate citation IDs against the supplied paper set.
- Keep AI-provider errors generic in API responses; details are written to server logs.
- Ask follow-up questions with a limited conversation history.
- Generate a per-paper deep dive with sections for summary, key findings, methods, limitations, and significance.
- Return a helpful response when a paper has no abstract rather than inventing an abstract-based analysis.

**Important:** an abstract-only synthesis cannot reliably describe details that the abstract does not contain, such as complete methodology, full statistical results, or all study limitations. Treat generated answers as research assistance, not as a replacement for reading the source papers.

### Accounts and saved research

- Register with an email address and password.
- Verify the email address through a verification link.
- Log in and log out using a signed session cookie.
- Request a verification email again.
- Request a password-reset link and set a new password.
- Change the password while signed in.
- Delete an account after confirming the password.
- Save bookmarks and retrieve or remove the current user's bookmarks.
- Synchronize eligible guest search history after login.
- Keep history and bookmarks scoped to the authenticated account; guest history is scoped by a session identifier.

### User interface

The web application is implemented as a client-side interactive research workspace within the Next.js App Router. The page includes research search, filters, AI responses, citations, history, bookmarks, account dialogs/flows, theme preference, and PDF export support through `jsPDF`.

The frontend uses browser storage for selected local preferences and guest-search tracking. Browser storage is not a secure place for secrets and must not be treated as an authentication authority.

---

## User and account behaviour

| User state | Available behaviour |
|---|---|
| Guest | Search papers, request AI synthesis and paper deep dives, subject to rate limits |
| Registered but unverified | Complete email verification; protected saved-library operations remain restricted |
| Verified signed-in user | Use account features, save bookmarks, and synchronize search history |
| Signed-out user | Cannot access protected bookmarks or account-owned history |

The backend's authentication identity is derived from the signed session cookie (or a supported authorization token where the endpoint accepts one), not from a caller-supplied `user_id` field in an AI request body. The backend tests include checks for identity isolation and rejection of forged or expired tokens.

---

## Technology stack

| Layer | Technology | Role |
|---|---|---|
| Web framework | Next.js App Router | Frontend routing, rendering, metadata, and production build |
| UI | React | Interactive application interface |
| Language | TypeScript | Frontend types and compile-time checking |
| Styling | Tailwind CSS v4 | Utility-first styles |
| Icons | Lucide React | Interface icons |
| PDF export | jsPDF | Client-side PDF generation |
| API | FastAPI | HTTP API and request validation |
| ASGI server | Uvicorn | Runs the Python API |
| Validation | Pydantic | Request model validation and field constraints |
| ORM / database | SQLAlchemy | Database models and persistence |
| Production database | PostgreSQL | Accounts, bookmarks, and search history |
| Test database | SQLite | Isolated automated tests |
| HTTP client | HTTPX | Asynchronous provider requests |
| AI providers | Google Gemini, Groq | Structured answer and deep-dive generation |
| Embeddings | FastEmbed | Optional local semantic re-ranking |
| Cache | Redis (optional) | Optional cache backend; in-memory fallback is available |
| Email | SMTP | Verification, welcome, and password-reset messages |
| Testing | pytest | Backend regression and security tests |

Versions are pinned in `backend/requirements.txt` and `backend/requirements-dev.txt`. Frontend dependencies and exact resolved package versions are recorded in `frontend/package.json` and `frontend/package-lock.json`.

---

## Architecture and request flows

### High-level architecture

```text
Browser
  |
  | HTTPS / JSON; session cookie for account identity
  v
Next.js frontend
  |
  | NEXT_PUBLIC_API_URL
  v
FastAPI backend
  |-- PostgreSQL: users, bookmarks, search history
  |-- Redis (optional) / in-memory caches
  |-- OpenAlex
  |-- Semantic Scholar
  |-- arXiv
  |-- Crossref
  |-- CORE
  |-- Google Gemini (primary AI provider)
  |-- Groq (fallback AI provider)
  |-- SMTP server (account emails)
  `-- FastEmbed model (optional local re-ranking)
```

The browser must be able to reach the API origin configured at frontend build time. The API's `CORS_ORIGINS` must list the exact frontend origin(s), including scheme and port where applicable.

### Search flow

1. The frontend sends a query and optional filters to `GET /search`.
2. The API sends concurrent requests to the supported academic providers.
3. Source adapters normalize provider-specific metadata into a common result shape.
4. The API deduplicates overlapping records.
5. It enforces publication-year and open-access filters again on the combined result set.
6. If enabled, FastEmbed re-ranks the results by semantic similarity to the query.
7. The API returns the query, result count, source counts, and result list.

Provider outages can reduce the available result set because provider calls are gathered independently. A partial result set is possible; the UI should not interpret an empty or smaller result set as proof that no relevant research exists.

### AI answer flow

1. The frontend sends the question and selected search results to `POST /ask`.
2. The API determines the signed-in user from the verified session, if present.
3. Results without a non-empty abstract are excluded from the evidence set.
4. The backend supplies a limited number of papers and bounded abstract text to the configured AI provider.
5. The provider returns structured data.
6. Citation IDs are validated against the actual papers supplied to the model; invented IDs are discarded.
7. The answer, valid citations, and paper snapshot are saved to search history.
8. The response is returned to the frontend.

The endpoint rejects a request when none of the supplied papers has an abstract. Provider/API errors are not returned verbatim to the client.

### Account flow

1. Registration creates an unverified account and initiates an email verification message.
2. The user opens the verification link and submits its token to the API.
3. Login establishes a signed session cookie.
4. Protected operations resolve the user from that session and enforce verification requirements.
5. Password changes and resets invalidate prior sessions according to the backend's session-version checks.
6. Account deletion requires password confirmation and invalidates the deleted account's session.

---

## Repository structure

```text
ai-research-search-main/
├── README.md
├── .gitignore
├── backend/
│   ├── main.py                  # FastAPI application, models, routes, providers, AI flows
│   ├── conftest.py              # Isolated pytest environment and temporary SQLite DB
│   ├── test_search_filters.py   # Provider/filter/deduplication regression tests
│   ├── test_security.py         # Auth, isolation, rate limit, error and header tests
│   ├── requirements.txt         # Runtime Python dependencies
│   ├── requirements-dev.txt     # Test dependencies
│   └── .env.example             # Backend configuration reference
└── frontend/
    ├── app/
    │   ├── page.tsx             # Main research workspace and interactions
    │   ├── layout.tsx           # Global layout and SEO/social metadata
    │   ├── globals.css          # Global styles and design tokens
    │   ├── not-found.tsx        # Not-found page
    │   ├── robots.ts            # robots.txt metadata route
    │   └── sitemap.ts           # sitemap.xml metadata route
    ├── public/                  # Public static assets and social preview image
    ├── eslint.config.mjs        # ESLint configuration
    ├── next.config.ts           # Security headers and production env validation
    ├── package.json
    ├── package-lock.json
    ├── tsconfig.json
    └── .env.example             # Frontend build-time API and app URLs
```

---

## Requirements

For local development, install:

- Python 3.12 or newer (use a version supported by all pinned dependencies in your deployment environment).
- Node.js 20 or newer and npm.
- PostgreSQL 14 or newer for normal local/production operation.
- Redis is optional.
- A Gemini API key and/or Groq API key for AI answers and deep dives.
- An SMTP account for sending verification and password-reset messages. In development, email can be printed to the backend console.
- Outbound network access from the backend to the academic providers, AI provider, and SMTP host.
- Outbound network access during the frontend production build for the Google Fonts referenced by `next/font/google`, unless the font setup is changed to self-host fonts.

---

## Local development setup

### 1. Clone or extract the repository

```bash
git clone <your-repository-url> ai-research-search
cd ai-research-search
```

If working from the provided ZIP, extract it and enter the `ai-research-search-main` directory instead.

### 2. Create a PostgreSQL database

Example for a local PostgreSQL installation:

```bash
sudo -u postgres psql
```

Then run the following SQL, replacing the example password with a unique local development password:

```sql
CREATE USER scholarpulse WITH PASSWORD 'replace-with-a-local-password';
CREATE DATABASE scholarpulse OWNER scholarpulse;
\q
```

Do not use the example password on an internet-facing server.

The application calls SQLAlchemy's `create_all()` when the backend module starts and applies a small set of PostgreSQL compatibility additions. This is convenient for a fresh development database, but it is **not a substitute for a versioned migration system** for production schema evolution. See [Known limitations and production gates](#known-limitations-and-production-gates).

### 3. Configure and start the backend

```bash
cd backend
python3 -m venv .venv

# Linux / macOS / WSL
source .venv/bin/activate

# Windows PowerShell alternative:
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
```

Generate a strong secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put the generated value in `AUTH_SECRET` in `backend/.env`. Configure at least:

```dotenv
AUTH_SECRET=replace-with-a-generated-secret-of-at-least-32-characters
CORS_ORIGINS=http://localhost:3000
DATABASE_URL=postgresql://scholarpulse:replace-with-a-local-password@localhost:5432/scholarpulse
APP_URL=http://localhost:3000
DEV_MODE=true
ENABLE_RERANKING=false
```

Set `GEMINI_API_KEY` and/or `GROQ_API_KEY` if you want to use AI answer generation. At least one AI provider key is needed for `/ask` and `/deep-dive` to generate responses. Configure SMTP values if you want real email delivery; with `DEV_MODE=true`, the app is designed to print email content/links to the console instead.

Start the API:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

From another terminal, check:

- API root: <http://localhost:8000/>
- Health check: <http://localhost:8000/health>
- Interactive API docs: <http://localhost:8000/docs> when enabled by development configuration.

The health endpoint checks database connectivity. A `503` response means the API cannot currently access its database.

### 4. Configure and start the frontend

Open another terminal:

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>.

The provided local configuration uses `localhost` for both frontend and API. Keep the hostnames consistent: do not use `localhost` for one and `127.0.0.1` for the other when testing cookie-based login, because browsers can treat them as different sites/origins.

`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_APP_URL` are embedded in the frontend at build time. Set them before creating a production build; changing the runtime environment after the build does not rewrite values already bundled into browser JavaScript.

### 5. Verify the main account journey

With the backend running in development mode:

1. Register with an email address and password.
2. Copy the verification URL printed in the backend console, or use the configured email inbox.
3. Open the verification URL and complete verification.
4. Log in and confirm the current account is shown.
5. Run a research search with and without year/open-access filters.
6. Ask a question about results that have abstracts and verify the citations link to returned papers.
7. Save a bookmark, refresh, and confirm it remains available to the signed-in account.
8. Review search history, then log out and verify protected data is no longer accessible.
9. Test forgot-password and reset-password flows.
10. Test guest search and verify the server-side rate limiter applies.

Do not use real user data or production credentials for this local walkthrough.

---

## Environment configuration

Copy the example files and configure secrets in your deployment environment. Never commit a real `.env`, API key, SMTP password, database password, or production secret.

### Backend variables

| Variable | Required? | Purpose / guidance |
|---|---|---|
| `AUTH_SECRET` | Yes | Signs session tokens. Use a randomly generated value; production requires at least 32 characters. |
| `CORS_ORIGINS` | Yes | Comma-separated exact frontend origins; wildcard `*` is rejected. |
| `DATABASE_URL` | Yes | PostgreSQL connection URL in production; SQLite is used by tests. |
| `APP_URL` | Yes | Public frontend URL used to construct email links. |
| `DEV_MODE` | Recommended explicit value | Use `true` only for local development. Production must use `false`. |
| `GEMINI_API_KEY` | One AI key required | Gemini API key; primary AI provider when configured. |
| `GEMINI_MODEL` | No | Gemini model name; defaults to `gemini-2.5-flash`. |
| `GROQ_API_KEY` | One AI key required | Groq API key used as a fallback provider. |
| `GROQ_MODEL` | No | Groq model name; defaults to `openai/gpt-oss-20b`. |
| `AI_MAX_OUTPUT_TOKENS` | No | Maximum configured AI output tokens; defaults to `4096`. |
| `SEMANTIC_SCHOLAR_API_KEY` | No | Optional key that can improve provider rate limits. |
| `CORE_API_KEY` | No | Optional CORE API key. |
| `SMTP_HOST` | No | SMTP hostname; example file uses Brevo's relay. |
| `SMTP_PORT` | No | SMTP port; example is `587`. |
| `SMTP_USERNAME` | For authenticated SMTP | SMTP login name. |
| `SMTP_PASSWORD` | For authenticated SMTP | SMTP password/API credential. Keep secret. |
| `SMTP_FROM_EMAIL` | Configure for delivery | Sender address; must be verified/authorized by the SMTP provider. |
| `SMTP_FROM_NAME` | No | Sender display name. |
| `COOKIE_SECURE` | No | Defaults to secure cookies when `DEV_MODE=false`; production should use HTTPS and secure cookies. |
| `COOKIE_SAMESITE` | No | `lax` by default; allowed values are `lax`, `strict`, or `none`. `none` requires secure cookies. |
| `COOKIE_DOMAIN` | No | Leave empty unless sharing a cookie across appropriate subdomains is required. |
| `ENABLE_DOCS` | No | Interactive docs/OpenAPI are off by default in production; enable only when intentionally needed. |
| `MAX_BODY_BYTES` | No | Maximum request body size; default is 1,000,000 bytes. |
| `ENABLE_RERANKING` | No | Enables FastEmbed semantic re-ranking; first use may download a model. |
| `REDIS_URL` | No | Redis connection URL; Redis is optional and the app can fall back to in-memory caching. |
| `TRUSTED_PROXY_COUNT` | No | Number of trusted reverse proxies that append `X-Forwarded-For`. Default `0` ignores that header. |

The backend `.env.example` is the canonical list of supported settings. Review it alongside the implementation when adding configuration.

### Frontend variables

| Variable | Required for production build? | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | Public base URL of the FastAPI backend, embedded into the browser bundle at build time. |
| `NEXT_PUBLIC_APP_URL` | Yes | Canonical public frontend URL, used for metadata, robots, and sitemap output. |

Example local values:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_APP_URL=http://localhost:3000
```

Example production values must use the real HTTPS URLs for your deployment.

---

## API reference

The API is implemented in `backend/main.py`. Interactive OpenAPI documentation is available at `/docs` only when `ENABLE_DOCS=true` or development defaults enable it. Keep it disabled in production unless there is a specific reason to expose it.

| Method | Path | Purpose | Access notes |
|---|---|---|---|
| `GET` | `/` | Basic API-running message | Public |
| `GET` | `/health` | Database connectivity health check | Public; returns `503` when database check fails |
| `POST` | `/auth/register` | Create an account and start email verification | Public, rate limited |
| `POST` | `/auth/login` | Authenticate and set the session cookie | Public, rate limited |
| `POST` | `/auth/logout` | Clear the session cookie | Public/session cleanup |
| `GET` | `/auth/me` | Return current session account | Session required |
| `POST` | `/auth/verify-email` | Verify email using a token | Public token flow |
| `POST` | `/auth/resend-verification` | Resend verification email | Public, rate limited |
| `POST` | `/auth/forgot-password` | Request a password reset | Public, rate limited |
| `POST` | `/auth/reset-password` | Reset password using a token | Public token flow |
| `POST` | `/auth/change-password` | Change the signed-in user's password | Authenticated |
| `DELETE` | `/auth/delete-account` | Delete the account after password confirmation | Authenticated |
| `GET` | `/search` | Search scholarly sources | Public, rate limited |
| `POST` | `/ask` | Generate an abstract-grounded answer | Guest or authenticated, rate limited |
| `POST` | `/deep-dive` | Generate a structured analysis for a paper | Public, rate limited |
| `POST` | `/history/sync` | Claim eligible guest history for the signed-in account | Verified account required |
| `GET` | `/history/{session_id}` | Retrieve allowed history for a session/account | Ownership rules apply |
| `DELETE` | `/history/entry/{entry_id}` | Delete one allowed history entry | Ownership rules apply |
| `DELETE` | `/history/{session_id}` | Delete history for the specified allowed scope | Ownership rules apply |
| `POST` | `/bookmarks` | Create a bookmark | Verified account required |
| `GET` | `/bookmarks` | List the current account's bookmarks | Verified account required |
| `DELETE` | `/bookmarks/{bookmark_id}` | Delete a bookmark belonging to the current account | Verified account required |

### Search parameters

`GET /search` accepts:

| Parameter | Type | Meaning |
|---|---|---|
| `q` | string, required, maximum 300 characters | Search query |
| `year_min` | integer, optional | Earliest allowed publication year |
| `year_max` | integer, optional | Latest allowed publication year |
| `open_access_only` | boolean, optional | When true, include only results positively identified as open access |

Example:

```bash
curl --get 'http://localhost:8000/search' \
  --data-urlencode 'q=machine learning in medical imaging' \
  --data-urlencode 'year_min=2020' \
  --data-urlencode 'year_max=2026' \
  --data-urlencode 'open_access_only=true'
```

The search response contains `query`, `count`, `source_counts`, and `results`. Individual result fields depend on the provider record and normalized metadata; abstracts, DOI, publication year, authors, title, source, and open-access information may not be available for every paper.

### AI question request

`POST /ask` accepts a JSON body with:

- `question`: required natural-language question, maximum 2,000 characters.
- `results`: list of search result objects, maximum 50 items.
- `session_id`: session scope for history, maximum 255 characters.
- `conversation_history`: optional list of prior question/answer turns, maximum 20 turns.

Example request shape:

```json
{
  "question": "What evidence supports this approach?",
  "session_id": "a-client-generated-session-id",
  "results": [
    {
      "title": "Example paper title",
      "authors": ["A. Researcher"],
      "publication_year": 2024,
      "description": "OpenAlex",
      "abstract": "The paper's abstract text...",
      "doi": "10.example/example"
    }
  ],
  "conversation_history": []
}
```

Use actual results returned by `/search` rather than the placeholder record above. The backend ignores any `user_id` supplied in the body; account identity comes from the signed session.

### Paper deep dive request

`POST /deep-dive` accepts `title` (maximum 500 characters) and `abstract` (maximum 10,000 characters). The generated analysis is based on these fields only. If no abstract is available, the API returns an explanation rather than generating unsupported findings.

### Error handling

Clients should handle validation errors, authentication/authorization errors, rate-limit responses (`429`), provider/network errors, AI generation errors (`502`), oversized bodies (`413`), and health-check failures (`503`). The backend is designed to avoid returning internal exception details and filesystem paths in unexpected-error responses.

---

## Database schema

SQLAlchemy models are defined in `backend/main.py`. The application currently creates missing tables automatically at startup. The primary tables are:

### `users`

| Column | Purpose |
|---|---|
| `id` | Integer primary key |
| `email` | Unique account email; indexed |
| `hashed_password` | Password hash; never return to clients |
| `full_name` | Optional display name |
| `is_verified` | Whether email verification is complete |
| `verification_token` | Verification token field |
| `reset_token` | Password-reset token field |
| `reset_token_expires_at` | Password-reset expiration |
| `created_at` | Account creation timestamp |

### `search_history`

| Column | Purpose |
|---|---|
| `id` | Integer primary key |
| `session_id` | Guest/browser session scope |
| `user_id` | Optional account owner |
| `question` | Asked question |
| `answer` | Generated answer |
| `citations` | JSON citation list |
| `papers` | JSON snapshot of paper records |
| `created_at` | Entry creation timestamp |

### `bookmarks`

| Column | Purpose |
|---|---|
| `id` | Integer primary key |
| `user_id` | Account owner |
| `title` | Paper title |
| `authors` | JSON list of authors |
| `abstract` | Optional abstract |
| `publication_year` | Optional year |
| `source` | Academic provider name |
| `doi` | Optional DOI |
| `created_at` | Bookmark creation timestamp |

### Database operations and migrations

- The backend uses SQLAlchemy's `create_all()` to create tables that do not exist.
- A small startup compatibility routine adds selected columns to existing PostgreSQL tables.
- SQLite is used by tests; PostgreSQL is the expected production database.
- There is no separate versioned migration directory in this repository at present.

For a production deployment with real accounts, add and test a versioned migration process (for example, Alembic) before relying on ongoing schema changes. Back up the database before any schema update. Do not treat an application restart as a database migration strategy.

---

## Academic data providers

ScholarPulse integrates the following sources:

| Provider | Use | Notes |
|---|---|---|
| OpenAlex | Scholarly works and metadata | Provider coverage and abstract availability vary |
| Semantic Scholar | Papers and academic metadata | Optional API key is supported |
| arXiv | Preprints and scholarly metadata | Primarily open-access preprints |
| Crossref | DOI and publication metadata | Skipped for open-access-only queries because the adapter cannot reliably confirm OA status from its Crossref data |
| CORE | Repository and open-access research discovery | Optional API key is supported |

Provider data is external and may be incomplete, delayed, duplicated, or temporarily unavailable. Open-access status is treated conservatively: when the open-access-only filter is enabled, records not positively marked open access are removed.

Respect each provider's terms, rate limits, attribution requirements, and API policies. Review those policies before operating a high-traffic public instance.

---

## AI answer grounding and citations

The answer-generation flow includes several guardrails:

- Only records with a non-empty abstract are sent as evidence.
- Abstract text and conversation history are length-limited before being sent to the AI provider.
- The backend caps the number of evidence papers used for synthesis.
- The AI is asked to return a structured response.
- Citation references are filtered against the actual paper IDs supplied in that request.
- Errors are logged on the server and generic messages are returned to the client.

These controls reduce some common failure modes, but they do not eliminate hallucination. A valid citation only means that the cited paper was part of the evidence set; it does not independently prove that the generated sentence accurately reflects that paper. Review the source paper and its abstract before using a generated answer in a publication, clinical decision, policy, or other high-impact context.

---

## Caching, rate limiting, and re-ranking

### Cache

Search responses and AI responses are cached to avoid repeating equivalent work. Redis is optional; an in-memory fallback is available. Cache lifetime and implementation details are in `backend/main.py`.

### Rate limiting

The backend includes a request rate limiter, request-size guard, and proxy-aware client-IP handling. The limiter is process-local and uses in-memory state.

**Scaling implication:** multiple Uvicorn workers or multiple API replicas do not share the same in-memory counters. Keep a single worker until the rate limiter is moved to a shared store, or implement a shared Redis-backed limiter before horizontally scaling the API.

When the API is behind a reverse proxy, configure `TRUSTED_PROXY_COUNT` to match the actual trusted proxy chain. Do not trust client-supplied forwarding headers unless the request came through the expected proxy setup.

### Semantic re-ranking

FastEmbed can download and run a local embedding model to re-rank search results. The first run may require a model download and additional disk/RAM. Set `ENABLE_RERANKING=false` to disable this feature when the environment cannot download or run the model. Benchmark latency and memory on the intended host before enabling it for a public production service.

---

## Testing and quality checks

Run the backend test suite from the backend directory in an environment where `requirements-dev.txt` has installed successfully:

```bash
cd backend
python -m compileall -q .
pytest -q
```

The test suite includes regression tests for:

- Provider filter parameters, publication years, and open-access filtering.
- Result deduplication and abstract handling.
- Authentication requirements and verified-account access.
- Invalid, forged, and expired tokens.
- User-to-user isolation for bookmarks and history.
- Guest history scoping and history synchronization.
- AI citation validation, missing abstracts, input bounds, and generic provider errors.
- Health-check behaviour and hidden production API docs.
- Security headers, oversized request rejection, rate limiting, and proxy-header trust.
- Password changes/resets invalidating old sessions.
- Cookie settings, environment validation, and database URL normalization.

The frontend quality checks are:

```bash
cd frontend
npm ci
npm run lint
npx tsc --noEmit
npm run build
```

For local development, `npm run dev` starts Next.js. For production, use `npm run build` and `npm run start`.

**Do not mark a release as passing unless each command has actually completed successfully.** If dependency installation is blocked by network access or package registry access, record the check as blocked rather than passed.

---

## Production deployment

This repository contains application code and environment examples, but no checked-in Dockerfile or deployment manifest. The exact hosting steps depend on your chosen platform. The following are deployment requirements rather than a claim that deployment has already been completed.

### 1. Prepare services

Provision:

- A managed PostgreSQL database with backups enabled.
- A frontend host that supports Next.js 16 and the project's Node.js version.
- A Python host that supports the pinned backend dependencies.
- Optional Redis if shared caching is needed.
- AI provider credentials and an SMTP account with a verified sender.
- HTTPS certificates for both public frontend and API endpoints.

### 2. Configure backend environment

Set production values securely through the hosting provider's secret manager/environment settings:

```dotenv
AUTH_SECRET=<strong-random-secret>
CORS_ORIGINS=https://your-frontend.example
DATABASE_URL=<production-postgresql-connection-url>
APP_URL=https://your-frontend.example
DEV_MODE=false
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
ENABLE_DOCS=false
TRUSTED_PROXY_COUNT=1
ENABLE_RERANKING=false
```

The proxy count above is an example for a deployment with exactly one trusted reverse proxy. Set it to the real topology, not automatically to `1`. Add the AI provider and SMTP variables needed by your deployment. Never expose backend secrets through `NEXT_PUBLIC_*` variables.

### 3. Build and deploy frontend

Set the following before building:

```dotenv
NEXT_PUBLIC_API_URL=https://api.your-domain.example
NEXT_PUBLIC_APP_URL=https://your-frontend.example
```

Then:

```bash
cd frontend
npm ci
npm run lint
npx tsc --noEmit
npm run build
npm run start
```

The frontend build validates that both public URL variables are present in production mode. The app uses `next/font/google`, so a clean build needs outbound access to fetch the configured fonts unless the font strategy is changed.

### 4. Run backend

Install the pinned dependencies and start Uvicorn behind a production reverse proxy:

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

This command is a basic process launch, not a complete deployment setup. Use your platform's process manager/container service, configure health checks, restrict direct access to the API port, and terminate HTTPS at a correctly configured proxy or load balancer. One worker is recommended until the in-memory rate limiter is replaced with a shared implementation.

### 5. Verify deployment

1. Confirm `GET /health` returns `200` from the deployment health-check path.
2. Confirm the frontend is served over HTTPS and API requests reach the correct HTTPS API origin.
3. Register a test account and verify email delivery and verification.
4. Confirm login persists after refresh and logout clears the session.
5. Search across providers and check filters.
6. Ask a question with papers containing abstracts and check citations.
7. Verify bookmark and history ownership isolation using two separate test accounts.
8. Verify `/docs` and `/openapi.json` are not publicly enabled.
9. Inspect logs for startup/database migration notes and provider failures.
10. Confirm database backups and restore procedures.
11. Confirm rate limiting works through the actual reverse proxy.
12. Remove test accounts and test data before launch.

---

## Security and privacy notes

The backend contains several defensive controls:

- Session tokens are signed and tied to password-version information.
- Authentication cookies are `HttpOnly` and `SameSite`-controlled; secure cookies default on outside development mode.
- Production startup requires a strong `AUTH_SECRET`.
- CORS requires explicit origins and rejects a wildcard origin.
- Protected bookmark and history operations apply account-ownership checks.
- Verified-account requirements are applied to saved-library operations.
- Password reset/change paths invalidate old sessions.
- Passwords are hashed using PBKDF2-HMAC-SHA256 with 600,000 iterations for current hashes, with legacy hash support and upgrade logic.
- Request bodies have a configurable maximum size.
- Responses include baseline security headers.
- Interactive docs are disabled by default outside development.
- Unhandled errors return generic client-facing messages rather than tracebacks or filesystem paths.
- The tests use a temporary SQLite database and deliberately avoid reading the developer's real `.env`.

These controls do not replace a full security review. Before public launch, review dependency vulnerabilities, account-enumeration behaviour, verification/reset-token storage and expiry, SMTP delivery and abuse protections, database access controls, log retention, privacy disclosures, and applicable data-protection obligations. Do not publish API keys, database credentials, signed session tokens, or real user records in issue reports.

### Cookie and domain setup

For local testing, use `localhost` consistently for both frontend and API. For production, HTTPS is required when secure cookies are enabled. If frontend and API are on different subdomains, test cookie domain, `SameSite`, CORS credentials, and browser behaviour explicitly. Only set `COOKIE_DOMAIN` when the cookie truly needs to be shared across subdomains that you control.

### Reverse proxy setup

The backend can use `X-Forwarded-For` only when configured to trust the expected number of proxies. Configure the proxy to overwrite/append forwarding headers correctly and prevent clients from bypassing the proxy. Do not set `TRUSTED_PROXY_COUNT` based on a copied example without checking the hosting topology.

---

## Operations and troubleshooting

### Backend refuses to start

- Check that `AUTH_SECRET`, `CORS_ORIGINS`, `DATABASE_URL`, and `APP_URL` are set.
- In production, ensure `AUTH_SECRET` is at least 32 characters.
- Check that the database URL is correct and that the database is reachable.
- Inspect the startup logs for database/schema messages.
- Do not switch `DEV_MODE=true` on a public server to bypass production validation.

### `/health` returns `503`

- Confirm PostgreSQL is running and accepting connections.
- Verify host, port, database name, username, password, and network allow-list.
- Check TLS requirements for a managed database.
- Confirm the database user has the required privileges.

### Login appears to succeed but the user is logged out after refresh

- Use `localhost` consistently for local frontend and API URLs; do not mix `localhost` and `127.0.0.1`.
- Confirm frontend requests include credentials where required.
- Check `CORS_ORIGINS` exactly matches the frontend origin.
- In production, verify HTTPS, cookie `Secure`, `SameSite`, and domain settings.
- Inspect browser cookie policies without copying session tokens into tickets or chats.

### Verification or password emails do not arrive

- Confirm SMTP hostname, port, username, and password.
- Confirm the sender address is verified with the provider.
- Inspect backend logs for SMTP delivery errors.
- Check spam/quarantine folders and the provider's delivery logs.
- In development mode, inspect the backend console for the generated email link.

### AI answer returns an error

- Set at least one valid Gemini or Groq API key.
- Confirm the selected model is enabled for the account and provider.
- Check provider quota, billing, and service status.
- Ensure the selected search results include abstracts.
- Review server logs for provider errors; the API intentionally returns a generic message.

### Search returns few or no papers

- Try a broader query.
- Remove year/open-access filters to compare the result set.
- Check provider availability, network egress, and provider rate limits.
- Remember that source coverage and abstracts vary; an empty result set is not proof that no literature exists.

### Slow first search or high memory use

- The first FastEmbed use may download a model and create embeddings.
- Set `ENABLE_RERANKING=false` to isolate whether re-ranking is responsible.
- Measure on the intended production hardware before enabling it again.
- Avoid scaling Uvicorn workers without addressing process-local rate-limit state.

### Frontend production build fails

- Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_APP_URL` before `npm run build`.
- Confirm the Node.js version and run `npm ci` using the committed lockfile.
- Ensure the build host can access the Google Fonts endpoints used by `next/font/google`.
- Run lint and TypeScript checks to identify code-level issues separately from network or environment failures.

---

## Known limitations and production gates

The following items should be considered before describing the application as production-ready:

1. **Automated tests must run successfully.** Test collection and execution must be completed in an environment with the pinned backend dependencies installed.
2. **Frontend checks must run successfully.** ESLint, TypeScript, and the production build have to pass in a clean dependency installation.
3. **Database migrations are not versioned.** Startup table creation and compatibility `ALTER TABLE` statements are not a complete schema migration strategy. Introduce and test versioned migrations before ongoing production changes.
4. **Rate limiting is process-local.** Limits are not shared between workers or replicas. Use one worker or move rate limiting to a shared store before scaling.
5. **Redis behaviour must be verified in the intended deployment.** Redis is optional, and fallback behaviour should be monitored under load.
6. **AI and provider dependencies are external.** Quotas, outages, changing APIs, and rate limits can affect results or generation.
7. **Email delivery requires real provider configuration.** Successful local link printing does not prove production email deliverability.
8. **Re-ranking requires runtime resources.** Validate model downloads, memory, disk, and latency on the target host.
9. **Deployment infrastructure is not included.** The repository has no Dockerfile or deployment manifest; production process management, TLS, backups, firewall rules, monitoring, and rollback need to be configured separately.
10. **Security review remains necessary.** Automated tests cover important cases but do not constitute a penetration test or complete privacy/compliance assessment.

### Current verification record

Use this table as a release record and update it with the actual environment, date, and output before release.

| Check | Result for the uploaded repository review | Notes |
|---|---|---|
| Python syntax compilation | Passed | `python -m compileall -q backend` completed successfully in the review environment. |
| Backend pytest suite | Blocked | Test collection could not import `google.genai`; installing dependencies was not possible because package-registry DNS/network access was unavailable in that environment. This is not a passing test run. |
| Frontend ESLint | Not run | npm dependency installation could not be completed in the review environment. |
| Frontend TypeScript | Not run | Requires installed frontend dependencies. |
| Frontend production build | Not run | Requires installed dependencies and build-time public URL configuration. |
| Live integration / deployment | Not verified | No running target environment or production credentials were supplied as part of this code archive. |

---

## Release checklist

Before launch, check each item in the target environment:

- [ ] Production secrets generated and stored in a secret manager.
- [ ] `DEV_MODE=false` and secure cookies enabled.
- [ ] Exact production frontend origin configured in `CORS_ORIGINS`.
- [ ] Production `DATABASE_URL` tested with least-privilege credentials.
- [ ] Versioned schema migrations created, reviewed, and tested.
- [ ] Database backup and restore verified.
- [ ] Gemini and/or Groq credentials tested.
- [ ] SMTP sender verified and delivery tested.
- [ ] `npm ci`, lint, TypeScript, and production build pass.
- [ ] Backend dependencies install cleanly and `pytest -q` passes.
- [ ] HTTPS, proxy headers, cookie domain, CORS, and health checks verified.
- [ ] Rate limiter strategy documented for the number of workers/replicas.
- [ ] `/docs` and `/openapi.json` disabled unless intentionally exposed.
- [ ] Monitoring, log retention, alerting, and rollback procedure configured.
- [ ] Two-account authorization/isolation checks completed.
- [ ] Privacy notice and applicable data-retention policy published.
- [ ] No secrets, test accounts, or sensitive data included in the release.

---

## Contributing and maintenance

- Keep the backend and frontend environment examples aligned with code changes.
- Add regression tests for bug fixes, especially authentication, ownership, filtering, and citation validation.
- Run all backend and frontend checks before merging.
- Use a versioned database migration for schema changes.
- Never commit `.env` files or real API credentials.
- Document new API routes, request constraints, response shapes, and deployment settings in this README.

