"""Grant or remove admin rights. Run on the server from the backend folder, with the backend's virtual environment:

    scholarpulse-env/bin/python make_admin.py new-encryption-key   # once: prints a value for SETTINGS_ENCRYPTION_KEY
    scholarpulse-env/bin/python make_admin.py grant you@example.com
    scholarpulse-env/bin/python make_admin.py reset-code you@example.com
    scholarpulse-env/bin/python make_admin.py revoke you@example.com
    scholarpulse-env/bin/python make_admin.py list

Admin rights can only be given here, never from the website. "grant" and "reset-code" print a setup key for an
authenticator app (Google Authenticator, Microsoft Authenticator, Authy). Anyone with that key can create admin
codes, so do not paste it into chat, email or tickets, and clear your terminal afterwards.
"""
import base64
import os
import secrets
import sys

# The admin tool never needs the search re-ranking model; skip loading it (saves memory and time).
os.environ.setdefault("ENABLE_RERANKING", "false")

import main  # noqa: E402  (after the environment tweak above)


def new_encryption_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _require_encryption():
    if main._settings_cipher() is None:
        raise SystemExit(
            "SETTINGS_ENCRYPTION_KEY is missing or invalid in backend/.env.\n"
            "Create one with:  python make_admin.py new-encryption-key\n"
            "then add the line SETTINGS_ENCRYPTION_KEY=<value> to backend/.env and restart the API."
        )


def _find(db, email: str):
    user = db.query(main.User).filter(main.User.email == email.strip().lower()).first()
    if not user:
        raise SystemExit(f"No account with the email {email}. The person must sign up on the site first.")
    return user


def grant(email: str) -> str:
    """Make the account an admin with a new authenticator secret. Returns the secret."""
    _require_encryption()
    db = main.SessionLocal()
    try:
        user = _find(db, email)
        if not user.is_verified:
            raise SystemExit("This account has not verified its email yet. Verify it first, then run grant again.")
        secret = main.new_totp_secret()
        user.is_admin = True
        user.is_disabled = False
        user.totp_secret = main.encrypt_setting(f"totp:{user.id}", secret)
        user.totp_last_step = None
        db.commit()
        email_out = user.email
    finally:
        db.close()
    main.audit(None, "admin_grant", None, target=email_out, detail="server command")
    return secret


def revoke(email: str):
    db = main.SessionLocal()
    try:
        user = _find(db, email)
        user.is_admin = False
        user.totp_secret = None
        user.totp_last_step = None
        db.commit()
        email_out = user.email
    finally:
        db.close()
    main.audit(None, "admin_revoke", None, target=email_out, detail="server command")


def list_admins() -> list[str]:
    db = main.SessionLocal()
    try:
        return [u.email for u in db.query(main.User).filter(main.User.is_admin.is_(True)).order_by(main.User.email).all()]
    finally:
        db.close()


def _print_setup(email: str, secret: str):
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    print(f"\n{email} is now an admin.\n")
    print("Add it to your authenticator app: choose 'Enter a setup key' (not 'Scan QR code') and type:")
    print(f"  Account name: {main.APP_NAME} admin")
    print(f"  Key:          {grouped}")
    print("  Type:         Time based\n")
    print("Then open the site's /admin page, sign in as usual and enter the 6-digit code from the app.")
    print("Keep this key private. Clear the terminal now (type: clear).\n")


def cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    command = argv[1]
    if command == "new-encryption-key":
        print(new_encryption_key())
        return 0
    if command == "list":
        admins = list_admins()
        print("\n".join(admins) if admins else "No admins yet.")
        return 0
    if command in ("grant", "reset-code", "revoke") and len(argv) == 3:
        if command == "revoke":
            revoke(argv[2])
            print(f"{argv[2]} is no longer an admin.")
        else:
            _print_setup(argv[2].strip().lower(), grant(argv[2]))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv))
