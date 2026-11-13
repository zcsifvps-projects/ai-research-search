# Operations

Day-to-day running of the production server: backups, restore, clean-up, checks and secrets.

## Backups

`scripts/backup-db.sh` dumps the database (compressed, PostgreSQL custom format), checks that the dump can be read back,
and keeps the last 14 days in `~/backups/imosmart/` (owner-only permissions).

Run it once by hand:

```bash
bash /var/www/scholarpulse/scripts/backup-db.sh
```

Schedule it every night at 02:30 (`crontab -e`, then add this line):

```
30 2 * * * /usr/bin/bash /var/www/scholarpulse/scripts/backup-db.sh >> $HOME/backups/imosmart/backup.log 2>&1
```

Check that it ran:

```bash
tail -3 ~/backups/imosmart/backup.log
```

**Admin → Overview** shows a read-only "Database backups" card: when the last backup ran, its size, how many are kept, and a
warning if the newest is more than 26 hours old (cron stopped). The website only reads the folder; it cannot create,
download or delete backups. If you change `BACKUP_DIR` for the script, set the same `BACKUP_DIR` in `backend/.env`.

**Keep a copy off the server.** A backup on the same disk does not survive a disk failure or a lost server. At least once a
week copy the newest dump somewhere else (another server, cloud storage, or your own machine with `scp`).

## Restore

### Test a backup (safe, does not touch the live database)

```bash
latest=$(ls -t ~/backups/imosmart/imosmart-*.dump | head -1)
sudo -u postgres createdb -O scholarpulse scholarpulse_restore_test
pg_restore --no-owner -h 127.0.0.1 -U scholarpulse -d scholarpulse_restore_test "$latest"
psql -h 127.0.0.1 -U scholarpulse -d scholarpulse_restore_test -c "SELECT count(*) AS users FROM users;"
sudo -u postgres dropdb scholarpulse_restore_test
```

Do this once after setting up backups, and again every few months.

### Real restore (replaces the live data)

Only after data loss. Stop the API so nothing writes during the restore:

```bash
pm2 stop scholarpulse-api
pg_restore --clean --if-exists --no-owner -h 127.0.0.1 -U scholarpulse -d scholarpulse "$latest"
pm2 start scholarpulse-api
```

## Housekeeping (automatic)

Once a day the API removes:

- guest history older than `GUEST_HISTORY_DAYS` (default 90). Signed-in users' history is kept until they delete it.
- sign-out records whose session has expired anyway, and expired verification / reset links.
- admin audit log entries older than `AUDIT_LOG_DAYS` (default 365).

The admin Overview shows when it last ran. Run it now with:

```bash
cd /var/www/scholarpulse/backend && scholarpulse-env/bin/python make_admin.py housekeeping
```

Deleting an account removes everything it owns: history, saved papers, folders, saved conversations (their share links stop
working) and uploaded documents.

## After every deploy

```bash
bash /var/www/scholarpulse/scripts/smoke-test.sh https://imosmart.online/api https://imosmart.online
```

It checks security headers, that protected routes refuse anonymous callers, search, the citation map, share links, body-size
limits and robots rules. It makes no AI calls and changes no data. It sends 14 bad logins at the end, so your IP may be
blocked for about a minute; add `SKIP_RATE_TEST=1` in front to skip that part.

## Secrets to keep safe (outside the server)

| Secret | Why |
|---|---|
| `AUTH_SECRET` | Signs every session; if it changes, everyone is signed out. |
| `SETTINGS_ENCRYPTION_KEY` | Decrypts API keys saved in the admin panel and admin authenticator secrets. Lost = re-enter keys and re-run `make_admin.py reset-code`. |
| Database password | Needed to restore backups. |
| AI and source API keys | Can be re-issued by each provider. |

Store them in a password manager, not in chat, email or the repository.
