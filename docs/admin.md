# Admin area

The admin dashboard lives at `/admin` on the site. It shows usage, lets you manage accounts, and lets you replace API keys
without editing the server files.

## How access works

1. **Admin rights are given only on the server**, with `backend/make_admin.py`. No page or API route can make anyone an admin.
2. An admin signs in normally, opens `/admin`, and enters the **6-digit code from an authenticator app**
   (Google Authenticator, Microsoft Authenticator, Authy). That unlocks the admin area for 30 minutes
   (`ADMIN_SESSION_MINUTES`).
3. The unlock is tied to that sign-in. Signing out, changing the password, "log out of all devices", or losing admin rights
   ends it. The admin cookie is `HttpOnly` and `SameSite=Strict`.
4. Each code works once. Five wrong codes lock admin unlocking for 15 minutes.
5. Every admin action is written to the audit log (`admin_audit_log` table, and the "Audit log" tab).
6. The admin routes are left out of the public API docs, and `/admin` is excluded from search engines.

## API keys

Managed keys: Gemini, Groq, CORE, Semantic Scholar, PubMed/NCBI.

- **Write-only.** The panel shows where each key comes from (panel, server configuration, or not set) and its last 4
  characters. A key is never sent back to the browser, logged, or written to the audit log.
- **Tested before saving.** The server makes a small call to the provider with the new key. If the provider rejects it,
  nothing changes, so a typo cannot take the site down.
- **Encrypted at rest** with AES-256-GCM, using `SETTINGS_ENCRYPTION_KEY` from `backend/.env`. A database dump or
  backup alone does not reveal the keys.
- **Fresh code needed** for every change (the unlock code cannot be reused; wait for the next one).
- **No restart.** A saved key is used immediately. "Remove panel value" goes back to the key in `backend/.env`.
- If `SETTINGS_ENCRYPTION_KEY` is lost or changed, keys saved in the panel can no longer be read and the `.env` keys are
  used instead. Re-enter them in the panel. Admin authenticator secrets must also be reissued with `reset-code`.

## Server commands

Run from `/var/www/scholarpulse/backend`:

```bash
scholarpulse-env/bin/python make_admin.py new-encryption-key
scholarpulse-env/bin/python make_admin.py grant you@example.com
scholarpulse-env/bin/python make_admin.py reset-code you@example.com
scholarpulse-env/bin/python make_admin.py revoke you@example.com
scholarpulse-env/bin/python make_admin.py list
```

`grant` and `reset-code` print a setup key. In the authenticator app choose "Enter a setup key", type it, and pick
"Time based". Do not share the setup key; clear the terminal afterwards.

## Sources

The **Sources** tab lists every paper source with an on/off switch. Switching one off is saved in the database and applies at
once (no restart); at least one search source must stay on. Each card shows, since the API last restarted: searches, errors,
papers returned, average time, the last error, and whether a needed key is set (with a link to API keys).

| Source | Key | Notes |
|---|---|---|
| OpenAlex, arXiv, Crossref, Europe PMC | none | |
| Semantic Scholar | optional | without a key it is often rate limited |
| CORE | required | |
| PubMed | optional | a key raises the request limit |
| DOAJ | none | open-access journals |
| ERIC | none | education research; skipped for open-access-only searches |
| OpenAIRE | none | European research |
| AJOL | none | African Journals Online, searched through OpenAlex's index of AJOL journals |
| Unpaywall | contact email | adds "Free PDF" links (legal copies) for the top 15 papers with a DOI; uses `NCBI_EMAIL` or `CONTACT_EMAIL` |

New kinds of sources need code (every API is different); switching sources on and off does not.

## What the dashboard shows

- **Overview:** users (total, verified, new this week, signed in today, disabled, admins), answers saved today and this
  week, AI calls today and deep research runs today against the daily caps.
- **Users:** search by email or name, 25 per page. Actions: disable / enable (reversible; admins cannot be disabled
  here), sign out everywhere, resend verification. There is no delete button on purpose.
- **Sources:** as above.
- **API keys:** as above.
- **Audit log:** every unlock, failed unlock, user action and key change, with time, admin and IP address.

## Database changes

Startup adds the new columns to `users` automatically (`is_admin`, `is_disabled`, `totp_secret`, `totp_last_step`,
`last_login_at`, all with safe defaults) and creates the `app_settings` and `admin_audit_log` tables. Existing users and
sessions are not affected.
