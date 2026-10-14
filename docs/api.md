# ScholarPulse API guide

A REST API over JSON. Search five academic databases at once, then get an answer written **only from the papers' abstracts**, with citations.

- **Reference (interactive):** `/docs` (Swagger UI, "Try it out") and `/redoc` (searchable reference with a side menu).
  They are served when `ENABLE_DOCS=true` (always on with `DEV_MODE=true`). The raw schema is at `/openapi.json`.
- **This guide:** concepts and copy-paste examples.

Every example uses a variable for the base URL, so nothing here is tied to a domain:

```bash
# Local development
API=http://localhost:8001
# Production behind nginx at /api (see API_ROOT_PATH in backend/.env.example)
# API=https://<your-domain>/api
```

## 1. Quick start (60 seconds)

```bash
# 1. Search (no account needed)
curl -s "$API/search?q=sleep+and+memory&year_min=2022&open_access_only=true" | head -c 600

# 2. Ask a question about those papers (guest)
curl -s -X POST "$API/ask" -H "Content-Type: application/json" -d '{
  "question": "Does sleep deprivation affect memory?",
  "session_id": "my-client-3f9c2f0e",
  "results": [{
    "title": "Sleep deprivation impairs memory consolidation",
    "authors": ["A. Researcher"],
    "description": "Journal of Sleep Research",
    "publication_year": 2024,
    "doi": "10.1234/abcd.5678",
    "abstract": "We show that one night of sleep loss reduces next-day recall by 20 percent."
  }]
}'
```

In practice you pass the `results` array from `/search` straight into `/ask`.

## 2. Authentication

Sign-in uses an `HttpOnly` session cookie named `access_token` (valid 14 days by default). There is no API key.

```bash
# Register: emails a verification link. It does NOT sign you in, and the response is identical whether or not
# the address is already registered (an existing owner gets a notice email instead), so accounts cannot be discovered.
curl -s -X POST "$API/auth/register" -H "Content-Type: application/json" \
  -d '{"email":"jane.doe@university.test","password":"a-long-passphrase","full_name":"Jane Doe"}'

# Verify: paste the token from the email link (?verify_token=...). This verifies the address AND signs you in.
curl -s -c jar.txt -X POST "$API/auth/verify-email" -H "Content-Type: application/json" -d '{"token":"<token from email>"}'

# Later: sign in again
curl -s -c jar.txt -X POST "$API/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"jane.doe@university.test","password":"a-long-passphrase"}'

# Use the cookie jar on protected routes
curl -s -b jar.txt "$API/auth/me"
curl -s -b jar.txt "$API/bookmarks"
```

Scripts can instead send `Authorization: Bearer <value of the token field>`.

| Caller | Allowed |
|---|---|
| Guest | `/search`, `/ask`, `/deep-dive`, own guest history by `session_id` |
| Signed in, email **not** verified | the above + `/auth/me`, change password, delete account |
| Signed in, email verified | the above + `/bookmarks`, `/history/sync` |

Identity is always taken from the cookie/token. A `user_id` in a request body is ignored.

Sessions:

- `POST /auth/logout` revokes **this** session on the server (a copied token stops working at once).
- `POST /auth/logout-all` revokes **every** session.
- Changing or resetting a password also signs out every other session. Deleting an account requires the password.
- Verification and reset links are single-use; verification links expire after 48 hours, reset links after 1 hour. They are stored only as hashes.

Guest `session_id`: a random value you generate once per client, 16 to 128 characters of letters, digits, `_` or `-` (a UUID works).
Shorter or guessable values are rejected with `422`, because the `session_id` is the only thing protecting a guest's history.

## 3. Endpoints

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Database-backed health check (`{"status":"healthy"}` or 503) |
| `GET /search` | none | Federated search. Params: `q` (required, max 300), `year_min`, `year_max`, `open_access_only` |
| `POST /ask` | optional | Cited answer from papers. Body: `question`, `results`, `session_id`, `conversation_history` |
| `POST /deep-dive` | none | Markdown analysis of one paper. Body: `title`, `abstract` |
| `GET /history/{session_id}` | optional | Your history (signed in) or the guest session's |
| `DELETE /history/entry/{id}` | optional | Delete one entry (guests add `?session_id=`) |
| `DELETE /history/{session_id}` | optional | Clear history |
| `POST /history/sync` | verified | Attach a guest `session_id`'s history to your account |
| `GET/POST /bookmarks`, `DELETE /bookmarks/{id}` | verified | Saved papers |
| `POST /auth/register` | none | Start sign-up; emails a link (never signs in, never reveals existing accounts) |
| `POST /auth/login`, `/auth/logout` | none | Sign in; sign out (revokes this session) |
| `POST /auth/logout-all` | signed in | Revoke every session |
| `GET /auth/me` | signed in | Current user |
| `POST /auth/verify-email`, `/auth/resend-verification` | none | Email verification |
| `POST /auth/forgot-password`, `/auth/reset-password` | none | Password reset |
| `POST /auth/change-password`, `DELETE /auth/delete-account` | signed in | Account management |

Full request and response shapes, with examples for every status code, are in the interactive reference.

### Search filters

- `year_min` / `year_max`: inclusive publication years. Papers with an unknown year are dropped when a year filter is set.
- `open_access_only=true`: only papers a source confirmed as open access. OpenAlex uses its open-access flag, Semantic Scholar needs a free PDF,
  arXiv and CORE are always open access. Crossref cannot say, so it is skipped for these searches. Having a DOI does **not** make a paper open access.
- Each result has `is_open_access` (`true`, `false` or `null` when the source did not say).

### How `/ask` stays grounded

- Only the first 6 papers that have an abstract are used. A paper without an abstract is never evidence.
- Citations are numbered by the paper's position in `results` (starting at 1). Citations the model invents are removed from the response.
- If no paper has an abstract you get `400` rather than an answer.
- Follow-ups: send earlier turns as `conversation_history: [{"question": "...", "answer": "..."}]` (up to 20).

## 4. Errors

All errors look like `{"detail": "message"}` (`422` carries a list under `detail`). Messages are generic on purpose: no server paths, provider
messages or stack traces.

| Status | Meaning |
|---|---|
| 400 | Not acceptable (weak password, empty question, no abstracts, bad token) |
| 401 | Not signed in, or the session is invalid or was invalidated |
| 403 | Email not verified |
| 404 | Not found, or not yours |
| 413 | Body larger than 1 MB (configurable) |
| 422 | A field is missing, too long or the wrong type |
| 429 | Rate limited, account temporarily locked, or a daily AI cap reached: wait `Retry-After` seconds |
| 502 / 503 | AI step failed / no AI provider configured |

## 5. Rate limits

Per client IP, per minute (fixed window): `/ask` 30, `/deep-dive` 20, `/search` 60, `/auth/login` 10, `/auth/register` 10, `/auth/forgot-password` 5,
`/auth/resend-verification` 5, `/auth/reset-password` 10, `/auth/verify-email` 10, other `/auth/*` 30, `/bookmarks` 60, everything else 150.
Behind a reverse proxy set `TRUSTED_PROXY_COUNT=1`, otherwise all users share the proxy's IP. The per-minute limiter is per process: run one worker
(the account lockout, email caps and daily AI caps use Redis when `REDIS_URL` is reachable, so they are shared across workers).

Additional protections, all configurable in `backend/.env.example`:

| Protection | Default |
|---|---|
| Failed sign-ins per account | 5 in 15 minutes, then locked until the window ends (a password reset clears it) |
| Emails per address | 3 per hour (extra requests get the same response; no email is sent) |
| AI calls per UTC day | guests 30 per IP, signed-in accounts 200, everyone 3000 (cached answers are free) |

Over a daily AI cap the API answers `429` with `Retry-After` set to the seconds until midnight UTC.

## 6. Examples

### JavaScript (browser)

```js
const API = process.env.NEXT_PUBLIC_API_URL; // never hardcode it

const { results } = await (await fetch(`${API}/search?q=${encodeURIComponent("sleep and memory")}&year_min=2022`)).json();

const res = await fetch(`${API}/ask`, {
  method: "POST",
  credentials: "include",            // sends the session cookie when signed in
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "Does sleep deprivation affect memory?", results, session_id: crypto.randomUUID() }),
});
if (!res.ok) throw new Error((await res.json()).detail);
const { answer, citations } = await res.json();
```

### Python

```python
import os, requests

API = os.environ["SCHOLARPULSE_API"]          # e.g. http://localhost:8001
s = requests.Session()                         # keeps the cookie
s.post(f"{API}/auth/login", json={"email": "jane.doe@university.test", "password": "a-long-passphrase"}).raise_for_status()

papers = s.get(f"{API}/search", params={"q": "sleep and memory", "year_min": 2022}).json()["results"]
r = s.post(f"{API}/ask", json={"question": "Does sleep deprivation affect memory?", "results": papers, "session_id": "my-script"})
if r.status_code == 429:
    print("slow down, retry in", r.headers["Retry-After"], "seconds")
print(r.json()["answer"])
```

## 7. Notes for operators

- `ENABLE_DOCS=true` publishes `/docs`, `/redoc` and `/openapi.json`. They contain no secrets; leave them off if you prefer not to publish the API surface.
- Behind nginx at `/api/`, set `API_ROOT_PATH=/api` so the interactive docs call the right URLs.
- The interactive docs work with the cookie: run **POST /auth/login** in "Try it out", then the protected routes work in the same browser tab.
