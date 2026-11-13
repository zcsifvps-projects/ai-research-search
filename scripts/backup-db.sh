#!/usr/bin/env bash
# Daily PostgreSQL backup. Reads DATABASE_URL from backend/.env, writes a compressed custom-format dump,
# checks that the dump can be read back, and keeps the last KEEP_DAYS days.
#
#   bash scripts/backup-db.sh
#
# Optional environment variables:
#   APP_DIR     (default /var/www/scholarpulse)     where the app is installed
#   BACKUP_DIR  (default $HOME/backups/imosmart)     where dumps are written (created with owner-only access)
#   KEEP_DAYS   (default 14)                         older dumps are deleted
#
# Schedule it with cron (crontab -e), for example every night at 02:30:
#   30 2 * * * /usr/bin/bash /var/www/scholarpulse/scripts/backup-db.sh >> $HOME/backups/imosmart/backup.log 2>&1
set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/scholarpulse}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/imosmart}"
KEEP_DAYS="${KEEP_DAYS:-14}"
ENV_FILE="$APP_DIR/backend/.env"

command -v pg_dump >/dev/null || { echo "pg_dump is not installed"; exit 2; }
[ -r "$ENV_FILE" ] || { echo "cannot read $ENV_FILE"; exit 2; }

# Read DATABASE_URL and split it into parts so the password goes through PGPASSWORD (not visible in the process list).
eval "$(python3 - "$ENV_FILE" <<'PY'
import shlex, sys
from urllib.parse import unquote, urlsplit
url = ""
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("DATABASE_URL="):
        url = line.split("=", 1)[1].strip()
        if len(url) >= 2 and url[0] == url[-1] and url[0] in "\"'":
            url = url[1:-1]  # only surrounding quotes; characters inside the password are kept
if not url:
    print("echo 'DATABASE_URL is not set in the env file'; exit 2")
    raise SystemExit
u = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://").replace("postgres://", "postgresql://"))
parts = {"PGHOST": u.hostname or "127.0.0.1", "PGPORT": str(u.port or 5432), "PGUSER": unquote(u.username or ""),
         "PGPASSWORD": unquote(u.password or ""), "PGDATABASE": u.path.lstrip("/")}
print("\n".join(f"export {k}={shlex.quote(v)}" for k, v in parts.items()))
PY
)"

umask 077
mkdir -p "$BACKUP_DIR"
stamp=$(date +%Y-%m-%d_%H%M)
out="$BACKUP_DIR/imosmart-$stamp.dump"

pg_dump --format=custom --no-owner --file="$out.part"
pg_restore --list "$out.part" >/dev/null   # fails if the dump cannot be read back
mv "$out.part" "$out"
find "$BACKUP_DIR" -maxdepth 1 -name 'imosmart-*.dump' -mtime +"$KEEP_DAYS" -delete

echo "$(date -Is) backup ok: $out ($(du -h "$out" | cut -f1)); kept: $(ls "$BACKUP_DIR"/imosmart-*.dump | wc -l) file(s)"
