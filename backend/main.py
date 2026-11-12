import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from urllib.parse import urlparse
import re
import secrets
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import xml.etree.ElementTree as ET

import httpx
from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from fastapi import BackgroundTasks, FastAPI, HTTPException, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
from groq import Groq
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# ============================================================
# ENVIRONMENT & STRICT SECURITY VALIDATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.getenv("ENV_FILE") or os.path.join(BASE_DIR, ".env")  # override is used by the tests
load_dotenv(dotenv_path=ENV_FILE, override=False)

def _utcnow() -> datetime:
    """Naive UTC 'now' (what the DateTime columns store); replaces the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("true", "1", "yes")


# DEV_MODE must be defined first: other secure-by-default settings depend on it.
DEV_MODE = _env_bool("DEV_MODE", False)

AUTH_SECRET = os.getenv("AUTH_SECRET", "").strip()
if not AUTH_SECRET:
    raise RuntimeError("AUTH_SECRET environment variable is required and must not be empty.")
if len(AUTH_SECRET) < 32 and not DEV_MODE:
    raise RuntimeError(
        "AUTH_SECRET must be at least 32 characters. Generate one with: "
        'python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )

CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "").strip()
if not CORS_ORIGINS_RAW:
    raise RuntimeError("CORS_ORIGINS environment variable is required and must be a comma-separated list of origins.")
ALLOWED_ORIGINS = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if "*" in ALLOWED_ORIGINS:
    raise RuntimeError("Wildcard '*' is not allowed in CORS_ORIGINS for credentialed requests.")

def normalize_database_url(url: str) -> str:
    """SQLAlchemy 2.1 maps a bare "postgresql://" to the psycopg v3 driver, but this project ships
    psycopg2. Pin the driver so the usual "postgresql://..." (or "postgres://...") URL just works."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required and must not be empty.")
DATABASE_URL = normalize_database_url(DATABASE_URL)

APP_URL = os.getenv("APP_URL", "").strip()
if not APP_URL:
    raise RuntimeError("APP_URL environment variable is required and must not be empty.")

# Cookie & Environment Flags
# Secure by default: the cookie is only sent over HTTPS unless DEV_MODE is on or it is explicitly disabled.
COOKIE_SECURE = _env_bool("COOKIE_SECURE", not DEV_MODE)
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").strip().lower()
if COOKIE_SAMESITE not in ("lax", "strict", "none"):
    COOKIE_SAMESITE = "lax"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "").strip() or None
if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true (browsers reject it otherwise).")

# AI / proxy / request-size settings
AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "4096"))
GENERIC_AI_ERROR = "Couldn't generate an answer for this question. Please try rephrasing it."
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(1_000_000)))
ENABLE_DOCS = _env_bool("ENABLE_DOCS", DEV_MODE)  # /docs and /openapi.json are off in production by default
ENABLE_RERANKING = _env_bool("ENABLE_RERANKING", True)
# Path prefix when a reverse proxy strips it before forwarding (e.g. nginx "location /api/" -> API_ROOT_PATH=/api).
# It makes the interactive docs call the right URLs. Leave empty when the API is served at the domain root.
API_ROOT_PATH = os.getenv("API_ROOT_PATH", "").strip().rstrip("/")

# Sessions, abuse protection and AI spend limits (0 disables a limit)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))          # failed sign-ins per account ...
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))        # ... within this window, then locked until it ends
EMAIL_MAX_PER_HOUR = int(os.getenv("EMAIL_MAX_PER_HOUR", "3"))          # verification/reset/notice emails per address
VERIFICATION_TOKEN_HOURS = int(os.getenv("VERIFICATION_TOKEN_HOURS", "48"))
AI_DAILY_LIMIT_GUEST = int(os.getenv("AI_DAILY_LIMIT_GUEST", "30"))     # AI calls per guest IP per UTC day
AI_DAILY_LIMIT_USER = int(os.getenv("AI_DAILY_LIMIT_USER", "200"))      # per signed-in account per UTC day
AI_DAILY_LIMIT_GLOBAL = int(os.getenv("AI_DAILY_LIMIT_GLOBAL", "3000")) # all callers together per UTC day
# Deep research runs several searches and 2 AI calls, so it has its own, smaller daily caps (on top of the AI caps).
DEEP_DAILY_LIMIT_GUEST = int(os.getenv("DEEP_DAILY_LIMIT_GUEST", "3"))
DEEP_DAILY_LIMIT_USER = int(os.getenv("DEEP_DAILY_LIMIT_USER", "20"))
DEEP_DAILY_LIMIT_GLOBAL = int(os.getenv("DEEP_DAILY_LIMIT_GLOBAL", "300"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
CORE_API_KEY = os.getenv("CORE_API_KEY", "").strip()
# PubMed (NCBI E-utilities) works without a key at 3 requests/second; a free key raises it to 10.
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "").strip()

# SMTP Configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp-relay.brevo.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
# No hardcoded domain: the sender defaults to noreply@<host of APP_URL>. Set SMTP_FROM_EMAIL to a sender
# verified with your SMTP provider (and SPF/DKIM on your domain) so mail is actually delivered.
_app_host = urlparse(APP_URL).hostname or "localhost"
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip() or f"noreply@{_app_host}"
# Contact address sent to OpenAlex/Crossref/etc. ("polite pool"). Defaults to the sender address.
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip() or SMTP_FROM_EMAIL
# Product name shown in emails, API docs and the User-Agent sent to the academic APIs. Comes from env so the
# brand (which may match the domain) is never hardcoded. Set APP_NAME in backend/.env.
APP_NAME = os.getenv("APP_NAME", "").strip() or "Research Engine"
APP_UA_NAME = "".join(ch for ch in APP_NAME if ch.isalnum()) or "ResearchEngine"
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "").strip() or APP_NAME


# ============================================================
# POSTGRESQL CONFIGURATION
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    verification_token = Column(String(255), nullable=True, index=True)
    reset_token = Column(String(255), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime, nullable=True)
    # verification/reset tokens are stored as SHA-256 hashes; only the emailed link holds the raw value
    verification_token_expires_at = Column(DateTime, nullable=True)
    # sessions issued before this moment are rejected ("sign out everywhere")
    tokens_valid_after = Column(DateTime, nullable=True)
    # Phase 5: admin rights are granted only by backend/make_admin.py; disabled accounts cannot sign in.
    is_admin = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_disabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    totp_secret = Column(Text, nullable=True)        # encrypted with SETTINGS_ENCRYPTION_KEY
    totp_last_step = Column(Integer, nullable=True)  # last accepted authenticator time step (no code reuse)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class RevokedToken(Base):
    """Session tokens that were signed out before their natural expiry."""
    __tablename__ = "revoked_tokens"

    jti = Column(String(64), primary_key=True)
    expires_at = Column(DateTime, nullable=False, index=True)


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True, default=list)
    papers = Column(JSON, nullable=True, default=list)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    authors = Column(JSON, nullable=True, default=list)
    abstract = Column(Text, nullable=True)
    publication_year = Column(Integer, nullable=True)
    source = Column(String(100), nullable=True)
    doi = Column(String(255), nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


def init_db():
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name != "postgresql":
        return  # the ALTERs below use Postgres-only syntax
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE search_history ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
            conn.execute(text("ALTER TABLE search_history ADD COLUMN IF NOT EXISTS papers JSON;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token VARCHAR(255);"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(255);"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token_expires_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_valid_after TIMESTAMP;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_disabled BOOLEAN NOT NULL DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_step INTEGER;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP;"))
            conn.commit()
    except Exception as e:
        print("Schema migration note:", e)


# ============================================================
# AUTHENTICATION & SECURITY UTILITIES
# ============================================================

PBKDF2_ITERATIONS = 600_000  # OWASP 2023 guidance for PBKDF2-HMAC-SHA256
LEGACY_PBKDF2_ITERATIONS = 100_000  # hashes created before the upgrade ("salt:hash")
MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${hashed}"


def _parse_hash(stored_hash: str) -> tuple[int, str, str] | None:
    if not stored_hash:
        return None
    if stored_hash.startswith("pbkdf2$"):
        parts = stored_hash.split("$")
        if len(parts) != 4:
            return None
        try:
            return int(parts[1]), parts[2], parts[3]
        except ValueError:
            return None
    if ":" in stored_hash:
        salt, original = stored_hash.split(":", 1)
        return LEGACY_PBKDF2_ITERATIONS, salt, original
    return None


def verify_password(password: str, stored_hash: str) -> bool:
    parsed = _parse_hash(stored_hash)
    if not parsed:
        return False
    iterations, salt, original_hash = parsed
    new_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(original_hash, new_hash)


def password_needs_rehash(stored_hash: str) -> bool:
    parsed = _parse_hash(stored_hash)
    return bool(parsed) and parsed[0] < PBKDF2_ITERATIONS


# Used to spend the same time on unknown emails as on real ones (prevents timing-based user enumeration).
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def password_version(stored_hash: str | None) -> str:
    """Short fingerprint of the current password hash. Tokens carry it, so changing or resetting
    the password invalidates every session issued before the change."""
    return hashlib.sha256((stored_hash or "").encode("utf-8")).hexdigest()[:16]


def create_access_token(user_id: int, email: str, pw_hash: str | None = None) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "iat": time.time(),
        "exp": int(time.time()) + SESSION_DAYS * 24 * 3600,
        "jti": secrets.token_hex(16),  # lets a single session be revoked on logout
    }
    if pw_hash is not None:
        payload["pv"] = password_version(pw_hash)
    payload_str = json.dumps(payload, separators=(',', ':'))
    payload_hex = payload_str.encode('utf-8').hex()
    signature = hmac.new(
        AUTH_SECRET.encode('utf-8'),
        payload_hex.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_hex}.{signature}"


def verify_access_token(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    try:
        payload_hex, signature = token.split(".", 1)
        expected_sig = hmac.new(
            AUTH_SECRET.encode('utf-8'),
            payload_hex.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        payload = json.loads(bytes.fromhex(payload_hex).decode('utf-8'))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def extract_token_from_request(request: Request, authorization: str | None = None) -> str | None:
    # 1. First check httpOnly cookie
    cookie_token = request.cookies.get("access_token")
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()

    # 2. Check Authorization Header (fallback for API / tests)
    if authorization:
        parts = authorization.strip().split()
        return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()

    return None


def _load_session_user(payload: dict | None) -> "User | None":
    """Return the user a valid token points at, or None if the account is gone or the password
    changed since the token was issued."""
    if not payload or "user_id" not in payload:
        return None
    try:
        uid = int(payload["user_id"])
    except (TypeError, ValueError):
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if not user or getattr(user, "is_disabled", False):
            return None
        if "pv" in payload and payload["pv"] != password_version(user.hashed_password):
            return None
        valid_after = user.tokens_valid_after
        if valid_after is not None and float(payload.get("iat", 0)) < valid_after.replace(tzinfo=timezone.utc).timestamp():
            return None  # signed out everywhere after this token was issued
        jti = payload.get("jti")
        if jti and db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
            return None  # this session was signed out
        db.expunge(user)
        return user
    finally:
        db.close()


def resolve_user_id(request: Request, authorization: str | None = None, require_verified: bool = False) -> int:
    """Strictly resolve the caller from a signed token. 401 if missing/invalid/stale,
    403 if require_verified=True and the email is not verified yet."""
    token = extract_token_from_request(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")
    user = _load_session_user(payload)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")
    if require_verified and not user.is_verified:
        raise HTTPException(
            status_code=403,
            detail="Email verification required. Please verify your email to access this feature.",
        )
    return user.id


def optional_user_id(request: Request, authorization: str | None = None) -> int | None:
    """Like resolve_user_id but returns None for anonymous/invalid callers (guest endpoints)."""
    token = extract_token_from_request(request, authorization)
    payload = verify_access_token(token) if token else None
    user = _load_session_user(payload)
    return user.id if user else None


def set_auth_cookie(response: Response, token: str):
    """Set environment-driven httpOnly SameSite cookie."""
    kwargs = {
        "key": "access_token",
        "value": token,
        "httponly": True,
        "samesite": COOKIE_SAMESITE,
        "secure": COOKIE_SECURE,
        "max_age": SESSION_DAYS * 24 * 3600,
        "path": "/",
    }
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**kwargs)


def clear_auth_cookie(response: Response):
    """Clear auth cookie on logout."""
    kwargs = {
        "key": "access_token",
        "path": "/",
        "httponly": True,
        "samesite": COOKIE_SAMESITE,
        "secure": COOKIE_SECURE,
    }
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.delete_cookie(**kwargs)


# ============================================================
# REUSABLE PLAIN SMTP EMAIL SYSTEM
# ============================================================

def send_email(to_email: str, subject: str, html_content: str, text_content: str | None = None) -> bool:
    """Reusable plain SMTP mailer compatible with Brevo, Gmail, Outlook, AWS SES."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        if DEV_MODE:
            print(
                f"\n========================================\n"
                f"[SMTP DEV LOG] (DEV_MODE active - credentials omitted)\n"
                f"TO: {to_email}\n"
                f"SUBJECT: {subject}\n"
                f"PREVIEW: {text_content or html_content[:250]}...\n"
                f"========================================\n"
            )
            return True
        else:
            print(f"[SMTP ERROR] SMTP credentials missing in production mode for {to_email}")
            return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg["To"] = to_email

        if text_content:
            msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        print(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"SMTP error sending to {to_email}: {e}")
        return False


def send_verification_email(to_email: str, token: str, full_name: str | None = None):
    verify_url = f"{APP_URL}/?verify_token={token}"
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">{APP_NAME}: Verify Your Account</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Please verify your email address to unlock research bookmarks and synchronization.</p>
      <div style="text-align: center; margin: 30px 0;">
        <a href="{verify_url}" style="background-color: #7C2A2A; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 14px; display: inline-block;">Verify Email Address</a>
      </div>
      <p style="color: #6E6459; font-size: 12px;">Or copy this URL into your browser:</p>
      <p style="color: #6E6459; font-size: 11px; word-break: break-all;"><a href="{verify_url}" style="color: #7C2A2A;">{verify_url}</a></p>
    </div>
    """
    text = f"{greeting}\n\nPlease verify your {APP_NAME} account at:\n{verify_url}\n"
    return send_email(to_email, f"Verify your {APP_NAME} account", html, text)


def send_welcome_email(to_email: str, full_name: str | None = None):
    greeting = f"Welcome {full_name}!" if full_name else f"Welcome to {APP_NAME}!"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">{greeting}</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Your email is verified and your account is active.</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Search eleven academic databases at once, including OpenAlex, PubMed, DOAJ and African Journals Online.</p>
      <div style="text-align: center; margin: 30px 0;">
        <a href="{APP_URL}" style="background-color: #7C2A2A; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 14px; display: inline-block;">Start Researching</a>
      </div>
    </div>
    """
    text = f"{greeting}\n\nYour account is active. Explore research at {APP_URL}\n"
    return send_email(to_email, f"Welcome to {APP_NAME}", html, text)


def send_account_exists_email(to_email: str, full_name: str | None = None):
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">{APP_NAME}: You already have an account</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Someone tried to create a {APP_NAME} account with this email address, which is already registered. If that was you, just <a href="{APP_URL}" style="color: #7C2A2A;">sign in</a> (or use "Forgot password" on the sign-in form). If it wasn't you, you can ignore this message; nothing was changed.</p>
    </div>
    """
    text = f"{greeting}\n\nSomeone tried to create a {APP_NAME} account with this email, which is already registered. If that was you, sign in at {APP_URL} (or use Forgot password). If not, ignore this message.\n"
    return send_email(to_email, f"You already have a {APP_NAME} account", html, text)


def send_password_reset_email(to_email: str, token: str, full_name: str | None = None):
    reset_url = f"{APP_URL}/?reset_token={token}"
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">{APP_NAME}: Reset Password</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Click the button below to reset your password. This link is valid for 1 hour.</p>
      <div style="text-align: center; margin: 30px 0;">
        <a href="{reset_url}" style="background-color: #7C2A2A; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 14px; display: inline-block;">Reset Password</a>
      </div>
      <p style="color: #6E6459; font-size: 12px;">Or copy this URL into your browser:</p>
      <p style="color: #6E6459; font-size: 11px; word-break: break-all;"><a href="{reset_url}" style="color: #7C2A2A;">{reset_url}</a></p>
    </div>
    """
    text = f"{greeting}\n\nReset your password within 1 hour at:\n{reset_url}\n"
    return send_email(to_email, f"Reset your {APP_NAME} password", html, text)


# ============================================================
# RATE LIMITING & PROXY-AWARE SECURITY HARDENING
# ============================================================

def get_client_ip(request: Request) -> str:
    """Return the caller's IP. X-Forwarded-For is attacker-controlled, so it is only used when
    TRUSTED_PROXY_COUNT says how many reverse proxies we run in front of the app, and then only the
    entry added by our own outermost proxy is trusted (counted from the right)."""
    peer = request.client.host if request.client else "unknown"
    if TRUSTED_PROXY_COUNT <= 0:
        return peer  # not behind a trusted proxy: ignore X-Forwarded-For completely
    hops = [h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
    if len(hops) >= TRUSTED_PROXY_COUNT:
        return hops[-TRUSTED_PROXY_COUNT]  # the address our own proxy saw
    return peer


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter, in memory and per process. Keyed on client IP + the matched rule (not the
    raw path), so /history/<anything> cannot be used to mint fresh buckets. With several workers each
    keeps its own counters; see the README before scaling out."""

    MAX_TRACKED_KEYS = 20000

    def __init__(self, app):
        super().__init__(app)
        self.request_history = defaultdict(list)
        # First match wins, so specific prefixes come before general ones.
        self.limits = {
            "/ask": (30, 60),
            "/deep-dive": (20, 60),
            "/takeaways": (20, 60),
            "/compare": (20, 60),
            "/deep-research": (5, 60),
            "/admin/unlock": (10, 60),
            "/admin/keys": (20, 60),
            "/admin": (120, 60),
            "/mode": (60, 60),
            "/search": (60, 60),
            "/auth/login": (10, 60),
            "/auth/register": (10, 60),
            "/auth/forgot-password": (5, 60),
            "/auth/reset-password": (10, 60),
            "/auth/resend-verification": (5, 60),
            "/auth/verify-email": (10, 60),
            "/auth/": (30, 60),
            "/bookmarks": (60, 60),
            "/collections": (60, 60),
            "/documents": (20, 60),
            "/graph": (30, 60),
            "/threads": (60, 60),
            "/share/": (60, 60),
            "default": (150, 60),
        }

    def _purge(self, now: float):
        longest = max(w for _, w in self.limits.values())
        for k in [k for k, hist in self.request_history.items() if not hist or now - hist[-1] > longest]:
            self.request_history.pop(k, None)

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        client_ip = get_client_ip(request)
        path = request.url.path

        limit_config = self.limits.get("default")
        matched = "default"
        for prefix, limit in self.limits.items():
            if prefix != "default" and path.startswith(prefix):
                limit_config = limit
                matched = prefix
                break

        max_reqs, window = limit_config
        now = time.time()
        key = f"{client_ip}:{matched}"

        if len(self.request_history) > self.MAX_TRACKED_KEYS:
            self._purge(now)

        history = [ts for ts in self.request_history[key] if now - ts < window]
        if len(history) >= max_reqs:
            self.request_history[key] = history
            retry_after = int(window - (now - history[0])) + 1
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests. Please slow down and try again shortly."},
                headers={"Retry-After": str(max(1, retry_after))},
            )

        history.append(now)
        self.request_history[key] = history
        return await call_next(request)


class RequestGuardMiddleware(BaseHTTPMiddleware):
    """Rejects oversized bodies and adds baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        length = request.headers.get("content-length")
        # PDF uploads have their own, larger limit (checked again while reading the body).
        limit = MAX_UPLOAD_BYTES if request.method == "POST" and request.url.path.rstrip("/") == "/documents" else MAX_BODY_BYTES
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if COOKIE_SECURE:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


# ============================================================
# FASTAPI APP
# ============================================================

def _err(code: int, description: str, detail: str) -> dict:
    return {code: {"description": description, "content": {"application/json": {"example": {"detail": detail}}}}}


def _ok(example: dict, description: str = "Success") -> dict:
    return {200: {"description": description, "content": {"application/json": {"example": example}}}}


E_RATE = _err(429, "Rate limit exceeded (see Rate limits). The Retry-After header says how many seconds to wait.",
              "Too many requests. Please slow down and try again shortly.")
E_401 = _err(401, "No valid session cookie, or the session expired or was invalidated.", "Authentication required.")
E_403 = _err(403, "Signed in, but the email address is not verified yet.",
             "Email verification required. Please verify your email to access this feature.")
E_AI = {
    **_err(502, "The AI provider failed twice (rate limit, invalid or truncated output). Details are only in the server log.",
           "Couldn't generate an answer for this question. Please try rephrasing it."),
    **_err(503, "No AI provider is configured on the server.",
           "The AI service is temporarily unavailable. Please try again later."),
}

EXAMPLE_USER = {"id": 7, "email": "jane.doe@university.test", "full_name": "Jane Doe", "is_verified": False}
EXAMPLE_PAPER = {
    "title": "Sleep deprivation impairs memory consolidation",
    "authors": ["A. Researcher", "B. Scientist"],
    "description": "Journal of Sleep Research",
    "publication_year": 2024,
    "doi": "10.1234/abcd.5678",
    "abstract": "We show that one night of sleep loss reduces next-day recall by 20 percent.",
    "similarity_score": 0.83,
    "is_open_access": True,
    "database": "OpenAlex",
    "cited_by_count": 42,
    "study_types": ["rct", "clinical_trial"],
    "is_preprint": False,
    "is_retracted": False,
    "field": "Psychology",
}
EXAMPLE_BOOKMARK = {
    "id": 3, "user_id": 7, "title": EXAMPLE_PAPER["title"], "authors": EXAMPLE_PAPER["authors"],
    "abstract": EXAMPLE_PAPER["abstract"], "publication_year": 2024, "source": "Journal of Sleep Research",
    "doi": EXAMPLE_PAPER["doi"], "created_at": "2026-10-14T09:30:00",
}

API_DESCRIPTION = """
__APP_NAME__ searches eleven academic databases at once (**OpenAlex, Semantic Scholar, arXiv, Crossref, CORE, PubMed, Europe PMC, DOAJ, ERIC, OpenAIRE, AJOL**) and
writes a short answer **only from the abstracts of the papers it found**, with citations back to those papers.

## Typical flow

1. `GET /search?q=...` returns papers from all five sources (merged, de-duplicated, ranked).
2. `POST /ask` sends the question plus those papers; you get a cited answer.
3. `POST /deep-dive` analyses a single paper from its title and abstract.
4. Optional: create an account to keep search history and a library of saved papers.

## Authentication

Sign-in uses a **signed, `HttpOnly` session cookie** named `access_token` (14 days by default). `POST /auth/login` and
`/auth/verify-email` set it (registration does not sign you in; the emailed link does); `POST /auth/logout` revokes this session
and `POST /auth/logout-all` every session. Browsers and HTTP clients with a
cookie jar (`curl -c jar.txt -b jar.txt`) send it automatically. A `Authorization: Bearer <token>` header with the same
token value is also accepted for scripts. Identity is **only** ever taken from the cookie/token, never from a request body.

| Who | Can do |
|---|---|
| **Guest** (no cookie) | `/search`, `/ask`, `/deep-dive`, and their own guest history via `session_id` |
| **Signed in, unverified** | everything a guest can, plus `/auth/me`, change password, delete account |
| **Signed in, verified** | everything above, plus `/bookmarks` and `/history/sync` |

## Errors

Every error is JSON: `{"detail": "human readable message"}`. Validation errors (`422`) use FastAPI's standard list in `detail`.
Messages are deliberately generic: server paths, provider errors and stack traces are never returned.

| Status | Meaning |
|---|---|
| 400 | The request is understood but not acceptable (e.g. weak password, empty question) |
| 401 | Not signed in, or the session is no longer valid |
| 403 | Signed in but email not verified |
| 404 | Resource not found, or not yours |
| 413 | Request body larger than the server limit (1 MB by default) |
| 422 | A field is missing, too long, or the wrong type |
| 429 | Rate limit hit; honour `Retry-After` |
| 502 / 503 | The AI step failed / no AI provider configured |

## Rate limits

Per client IP, per minute, in a fixed window: `/ask` 30, `/deep-dive` 20, `/search` 60, `/auth/login` 10, `/auth/register` 10,
`/auth/forgot-password` 5, `/auth/resend-verification` 5, `/auth/reset-password` 10, `/auth/verify-email` 10, other `/auth/*` 30,
`/bookmarks` 60, everything else 150.

Additional protections: **5 failed sign-ins per account in 15 minutes** locks that account for the rest of the window (a password reset
clears it); **3 emails per address per hour**; and **daily AI caps** per UTC day (guests 30 per IP, signed-in accounts 200, everyone 3000).
Over a cap you get `429` with `Retry-After`. All limits are configurable (see `.env.example`).

## Filters

`/search` accepts `year_min`, `year_max` and `open_access_only`. They are sent to each source so results are not wasted.
**Open access** means a source confirmed it (OpenAlex flag, Semantic Scholar free PDF, arXiv and CORE always); Crossref cannot
say, so it is skipped for open-access searches. Papers with an unknown year are dropped when a year filter is set.

More: see `docs/api.md` in the repository for copy-paste examples in curl, JavaScript and Python.
"""

TAGS_METADATA = [
    {"name": "System", "description": "Liveness and root."},
    {"name": "Auth", "description": "Accounts and sessions. Sets or clears the `access_token` cookie."},
    {"name": "Search", "description": "Federated search across eleven academic databases."},
    {"name": "AI", "description": "Answers and paper analysis grounded in abstracts. Open to guests, rate limited."},
    {"name": "History", "description": "Past questions and answers, per signed-in user or per guest `session_id`."},
    {"name": "Library", "description": "Saved papers. Requires a verified account."},
]

PROTECTED_OPERATIONS = {
    ("get", "/auth/me"), ("post", "/auth/change-password"), ("delete", "/auth/delete-account"), ("post", "/auth/logout-all"),
    ("post", "/history/sync"), ("post", "/bookmarks"), ("get", "/bookmarks"), ("delete", "/bookmarks/{bookmark_id}"),
}

API_DESCRIPTION = API_DESCRIPTION.replace("__APP_NAME__", APP_NAME)

app = FastAPI(
    title=f"{APP_NAME} API",
    version="1.0.0",
    summary="Federated academic search with answers grounded in the papers' abstracts.",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    root_path=API_ROOT_PATH,
    # Interactive docs reveal the full API surface: off in production unless ENABLE_DOCS=true.
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title, version=app.version, summary=app.summary, description=app.description,
        routes=app.routes, tags=TAGS_METADATA, servers=[{"url": API_ROOT_PATH}] if API_ROOT_PATH else None,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["cookieAuth"] = {
        "type": "apiKey", "in": "cookie", "name": "access_token",
        "description": "Set automatically by POST /auth/login (HttpOnly). Run login first; the docs then send it for you.",
    }
    for path, ops in schema.get("paths", {}).items():
        for method, op in ops.items():
            if (method, path) in PROTECTED_OPERATIONS:
                op["security"] = [{"cookieAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

# Starlette runs the LAST added middleware first (outermost). CORS must be outermost so that
# 429 / 413 responses from the ones below it still carry CORS headers and the browser can read them.
app.add_middleware(RateLimiterMiddleware)
app.add_middleware(RequestGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Filename"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    print(f"[Unhandled Error on {request.method} {request.url.path}]: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Please try again later."},
    )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================
try:
    init_db()
except Exception as e:
    print("Database auto-init note:", e)


# ============================================================
# AI CONFIGURATION (GEMINI + GROQ FALLBACK)
# ============================================================

def get_gemini_client():
    key = effective_key("GEMINI_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key)


def get_groq_client():
    key = effective_key("GROQ_API_KEY")
    if not key:
        return None
    return Groq(api_key=key)


# ============================================================
# REQUEST MODELS
# ============================================================

class RegisterRequest(BaseModel):
    email: str = Field(max_length=255, examples=["jane.doe@university.test"])
    password: str = Field(max_length=MAX_PASSWORD_LENGTH, description="8 to 128 characters.", examples=["a-long-passphrase"])
    full_name: str | None = Field(default=None, max_length=255, examples=["Jane Doe"])


class LoginRequest(BaseModel):
    email: str = Field(max_length=255, examples=["jane.doe@university.test"])
    password: str = Field(max_length=MAX_PASSWORD_LENGTH, examples=["a-long-passphrase"])


class VerifyEmailRequest(BaseModel):
    token: str = Field(max_length=255)


class ResendVerificationRequest(BaseModel):
    email: str = Field(max_length=255)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(max_length=255)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class DeleteAccountRequest(BaseModel):
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class SyncHistoryRequest(BaseModel):
    session_id: str = Field(max_length=128, pattern=r"^[A-Za-z0-9_-]{16,128}$")


# Guest history is protected only by its session id, so the server insists on an unguessable one
# (a UUID or 32 hex characters qualifies; "anonymous" means "do not keep a retrievable history").
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{16,128}$"
SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)


class ConversationTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=20000)


# Identity comes only from the signed cookie, never from the body: any extra "user_id" is ignored.
class AskRequest(BaseModel):
    question: str = Field(max_length=2000, examples=["Does sleep deprivation affect memory?"])
    results: list[dict] = Field(
        max_length=50,
        description="Papers exactly as returned by GET /search (title, authors, abstract, publication_year, ...). Only papers with an abstract are used.",
        examples=[[EXAMPLE_PAPER]],
    )
    session_id: str = Field(
        default="anonymous", max_length=128, pattern=r"^(anonymous|[A-Za-z0-9_-]{16,128})$",
        description="A random id you generate once per browser/client: 16 to 128 characters of letters, digits, '_' or '-' (a UUID works). "
                    "Guest history is stored under it, so make it unguessable.",
        examples=["3f9c2f0e-8c1a-4b7e-9a55-0d6d2f5c1a11"],
    )
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=20)
    mode: str = Field(default="quick", pattern="^(quick|medical)$",
                      description="`medical` adds evidence-level guidance and a not-medical-advice note to the answer.")


class DeepResearchRequest(BaseModel):
    question: str = Field(max_length=2000, examples=["How does remote work affect productivity across industries?"])
    session_id: str = Field(default="anonymous", max_length=128, pattern=r"^(anonymous|[A-Za-z0-9_-]{16,128})$")
    medical: bool | None = Field(default=None, description="true/false forces medical handling; null detects it from the wording.")
    year_min: int | None = Field(default=None, ge=0, le=3000)
    year_max: int | None = Field(default=None, ge=0, le=3000)
    open_access_only: bool = False
    min_citations: int | None = Field(default=None, ge=0, le=1_000_000)
    exclude_preprints: bool = False
    study_type: str | None = Field(default=None, max_length=40)
    field: str | None = Field(default=None, max_length=60)


class DeepDiveRequest(BaseModel):
    title: str = Field(max_length=500, examples=[EXAMPLE_PAPER["title"]])
    abstract: str = Field(max_length=10000, examples=[EXAMPLE_PAPER["abstract"]])


class PaperInput(BaseModel):
    """One paper sent to /takeaways or /compare. paper_id is its position in the /search results (from 1)."""
    paper_id: int = Field(ge=1, le=1000)
    title: str = Field(max_length=500)
    abstract: str | None = Field(default=None, max_length=10000)
    publication_year: int | None = Field(default=None, ge=0, le=3000)
    is_retracted: bool = False


class TakeawaysRequest(BaseModel):
    papers: list[PaperInput] = Field(min_length=1, max_length=10)


class CompareRequest(BaseModel):
    papers: list[PaperInput] = Field(min_length=2, max_length=5)


class BookmarkCreateRequest(BaseModel):
    title: str = Field(max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=200)
    abstract: str | None = Field(default=None, max_length=20000)
    publication_year: int | None = Field(default=None, ge=0, le=3000)
    source: str | None = Field(default=None, max_length=100)
    doi: str | None = Field(default=None, max_length=255)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_new_password(password: str, label: str = "Password"):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail=f"{label} must be at least 8 characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"{label} must be at most {MAX_PASSWORD_LENGTH} characters.")


# ============================================================
# BASIC ENDPOINTS
# ============================================================

@app.get(
    "/",
    tags=["System"],
    summary="API root",
    description="Confirms the API process is up. Use `/health` for a database-backed check.",
    responses=_ok({"message": f"{APP_NAME} API is running"}),
)
def root():
    return {"message": f"{APP_NAME} API is running"}


@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Runs `SELECT 1` against the database. Returns only `{\"status\": \"healthy\"}`; nothing about the "
                "configuration is exposed. Use it for uptime monitors and load-balancer checks.",
    responses={**_ok({"status": "healthy"}, "Healthy"), **_err(503, "The database is unreachable.", "unhealthy")},
)
def health():
    """Liveness/readiness probe. Deliberately exposes nothing about configuration."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "healthy"}


# ============================================================
# ABUSE PROTECTION: counters, login lockout, email limits, AI spend limits
# ============================================================

_COUNTERS: dict[str, tuple[int, float]] = {}
_COUNTER_LOCK = threading.Lock()


def counter_incr(key: str, ttl: int) -> int:
    """Increment a counter that expires `ttl` seconds after its first increment. Uses Redis when it is
    available (shared by all workers), otherwise process memory."""
    if redis_client is not None:
        try:
            full_key = f"ctr:{key}"
            count = int(redis_client.incr(full_key))
            if count == 1 or redis_client.ttl(full_key) < 0:
                redis_client.expire(full_key, max(1, ttl))
            return count
        except Exception as error:
            print("Counter store (Redis) note, using memory:", error)
    now = time.time()
    with _COUNTER_LOCK:
        count, expires_at = _COUNTERS.get(key, (0, 0.0))
        if expires_at <= now:
            count, expires_at = 0, now + ttl
        count += 1
        _COUNTERS[key] = (count, expires_at)
        if len(_COUNTERS) > 50000:
            for k in [k for k, (_, exp) in _COUNTERS.items() if exp <= now]:
                _COUNTERS.pop(k, None)
        return count


def counter_get(key: str) -> int:
    if redis_client is not None:
        try:
            return int(redis_client.get(f"ctr:{key}") or 0)
        except Exception:
            pass
    with _COUNTER_LOCK:
        count, expires_at = _COUNTERS.get(key, (0, 0.0))
        return count if expires_at > time.time() else 0


def counter_ttl(key: str) -> int:
    if redis_client is not None:
        try:
            ttl = int(redis_client.ttl(f"ctr:{key}"))
            if ttl > 0:
                return ttl
        except Exception:
            pass
    with _COUNTER_LOCK:
        _, expires_at = _COUNTERS.get(key, (0, 0.0))
        return max(1, int(expires_at - time.time()) + 1)


def counter_reset(key: str):
    if redis_client is not None:
        try:
            redis_client.delete(f"ctr:{key}")
        except Exception:
            pass
    with _COUNTER_LOCK:
        _COUNTERS.pop(key, None)


def hash_token(raw: str) -> str:
    """Verification/reset tokens are random, so an unsalted SHA-256 is enough: a database leak does not
    reveal usable links."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _subject_key(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


def login_lock_key(email: str) -> str:
    return f"lf:{_subject_key(email)}"


def check_login_not_locked(email: str):
    """Per-ACCOUNT lockout, on top of the per-IP rate limit: stops a distributed password-guessing attack.
    The same counter runs for unknown emails, so the behaviour never reveals whether an account exists."""
    key = login_lock_key(email)
    if LOGIN_MAX_FAILURES > 0 and counter_get(key) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Please wait a few minutes, or reset your password.",
            headers={"Retry-After": str(counter_ttl(key))},
        )


def record_login_failure(email: str):
    if LOGIN_MAX_FAILURES > 0:
        counter_incr(login_lock_key(email), LOGIN_LOCK_SECONDS)


def email_allowed(kind: str, email: str) -> bool:
    """Caps how many emails one address can be sent per hour (stops email bombing). When over the cap the
    email is silently skipped; the API response stays identical."""
    if EMAIL_MAX_PER_HOUR <= 0:
        return True
    return counter_incr(f"em:{kind}:{_subject_key(email)}", 3600) <= EMAIL_MAX_PER_HOUR


def _seconds_until_utc_midnight() -> int:
    now = _utcnow()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def enforce_ai_quota(request: Request, user_id: int | None):
    """Daily caps on AI calls (they cost money): per signed-in account or per guest IP, plus a global cap.
    Counted when a provider call is about to happen; cache hits are free."""
    day = _utcnow().strftime("%Y%m%d")
    ttl = _seconds_until_utc_midnight()
    if user_id is not None:
        subject_key, limit = f"ai:u:{user_id}:{day}", AI_DAILY_LIMIT_USER
    else:
        subject_key, limit = f"ai:ip:{_subject_key(get_client_ip(request))}:{day}", AI_DAILY_LIMIT_GUEST
    if limit > 0 and counter_incr(subject_key, ttl) > limit:
        raise HTTPException(
            status_code=429,
            detail="Daily AI usage limit reached. Please try again tomorrow." + (
                "" if user_id is not None else " Signing in raises the limit."),
            headers={"Retry-After": str(ttl)},
        )
    if AI_DAILY_LIMIT_GLOBAL > 0 and counter_incr(f"ai:g:{day}", ttl) > AI_DAILY_LIMIT_GLOBAL:
        raise HTTPException(
            status_code=429,
            detail="The AI service is very busy right now. Please try again later.",
            headers={"Retry-After": str(min(ttl, 3600))},
        )


DEEP_LIMIT_MESSAGE = "Daily deep research limit reached. A quick answer is still available."


def enforce_deep_quota(request: Request, user_id: int | None):
    """Separate daily caps for deep research (per account or guest IP, plus a global cap). 0 turns a cap off."""
    day = _utcnow().strftime("%Y%m%d")
    ttl = _seconds_until_utc_midnight()
    if user_id is not None:
        subject_key, limit = f"deep:u:{user_id}:{day}", DEEP_DAILY_LIMIT_USER
    else:
        subject_key, limit = f"deep:ip:{_subject_key(get_client_ip(request))}:{day}", DEEP_DAILY_LIMIT_GUEST
    if limit > 0 and counter_incr(subject_key, ttl) > limit:
        raise HTTPException(status_code=429, detail=DEEP_LIMIT_MESSAGE + (
            "" if user_id is not None else " Signing in raises the limit."), headers={"Retry-After": str(ttl)})
    if DEEP_DAILY_LIMIT_GLOBAL > 0 and counter_incr(f"deep:g:{day}", ttl) > DEEP_DAILY_LIMIT_GLOBAL:
        raise HTTPException(status_code=429, detail=DEEP_LIMIT_MESSAGE, headers={"Retry-After": str(min(ttl, 3600))})


# ============================================================
# AUTHENTICATION ENDPOINTS
# ============================================================

REGISTER_MESSAGE = (
    "Check your email. If this address can be registered, we have sent a link to verify it and finish creating your account."
)


@app.post(
    "/auth/register",
    tags=["Auth"],
    summary="Create an account",
    description="Starts account creation and emails a verification link (valid 48 hours). **It does not sign you in**: opening the link "
                "(`POST /auth/verify-email`) verifies the address and signs you in. The response is identical whether or not the address "
                "is already registered, so accounts cannot be discovered; an already-registered address receives a notice email instead. "
                "Password: 8 to 128 characters. At most 3 emails per address per hour.",
    responses={
        **_ok({"message": "Check your email. If this address can be registered, we have sent a link to verify it and finish creating your account."},
              "Always the same message"),
        **_err(400, "Invalid email, or password too short/long.", "Password must be at least 8 characters."),
        **E_RATE,
    },
)
def register(req: RegisterRequest, background_tasks: BackgroundTasks):
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 255:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    validate_new_password(req.password)

    full_name = req.full_name.strip() if req.full_name else None

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        created = None
        if not existing:
            raw_token = secrets.token_urlsafe(32)
            user = User(
                email=email,
                hashed_password=hash_password(req.password),
                full_name=full_name,
                is_verified=False,
                verification_token=hash_token(raw_token),
                verification_token_expires_at=_utcnow() + timedelta(hours=VERIFICATION_TOKEN_HOURS),
            )
            db.add(user)
            try:
                db.commit()
                created = (user.email, raw_token, user.full_name)
            except IntegrityError:  # two simultaneous sign-ups: the other one won
                db.rollback()
                existing = db.query(User).filter(User.email == email).first()
        if existing:
            hash_password(req.password)  # same work as creating an account, so timing does not reveal it
            if email_allowed("register", email):
                background_tasks.add_task(send_account_exists_email, existing.email, existing.full_name)
        elif created and email_allowed("register", email):
            background_tasks.add_task(send_verification_email, *created)
        return {"message": REGISTER_MESSAGE}
    finally:
        db.close()


@app.post(
    "/auth/login",
    tags=["Auth"],
    summary="Sign in",
    description="Sets the `access_token` cookie (valid 14 days by default). Wrong email and wrong password return the same 401 so accounts "
                "cannot be enumerated. After 5 failed attempts for one account within 15 minutes, further attempts get 429 until the window "
                "ends (resetting the password clears it).",
    responses={
        **_ok({"token": "<same value as the cookie>", "user": {**EXAMPLE_USER, "is_verified": True}}, "Signed in"),
        **_err(401, "Invalid email or password.", "Invalid email or password."),
        **_err(429, "Per-IP rate limit, or the account is temporarily locked after repeated failures. See Retry-After.",
               "Too many failed sign-in attempts. Please wait a few minutes, or reset your password."),
    },
)
def login(req: LoginRequest, response: Response):
    email = req.email.strip().lower()
    check_login_not_locked(email)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            verify_password(req.password, _DUMMY_HASH)  # same work as a real check: no timing oracle
            record_login_failure(email)
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if not verify_password(req.password, user.hashed_password):
            record_login_failure(email)
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        counter_reset(login_lock_key(email))
        if user.is_disabled:
            raise HTTPException(status_code=403, detail="This account has been disabled. Please contact support.")

        if password_needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(req.password)
        user.last_login_at = _utcnow().replace(tzinfo=None)
        db.commit()

        token = create_access_token(user.id, user.email, user.hashed_password)
        set_auth_cookie(response, token)

        return {
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "is_verified": user.is_verified,
            },
        }
    finally:
        db.close()


@app.post(
    "/auth/logout",
    tags=["Auth"],
    summary="Sign out",
    description="Clears the cookie **and revokes this session on the server**, so a copied token stops working immediately. "
                "Always succeeds, even if you were not signed in.",
    responses=_ok({"message": "Logged out successfully."}),
)
def logout(request: Request, response: Response, authorization: str | None = Header(None)):
    token = extract_token_from_request(request, authorization)
    payload = verify_access_token(token) if token else None
    if payload and payload.get("jti"):
        db = SessionLocal()
        try:
            if not db.query(RevokedToken).filter(RevokedToken.jti == payload["jti"]).first():
                expires = datetime.fromtimestamp(int(payload["exp"]), timezone.utc).replace(tzinfo=None)
                db.add(RevokedToken(jti=payload["jti"], expires_at=expires))
            db.query(RevokedToken).filter(RevokedToken.expires_at < _utcnow()).delete(synchronize_session=False)
            db.commit()
        except Exception as error:
            db.rollback()
            print("Session revocation note:", error)
        finally:
            db.close()
    clear_auth_cookie(response)
    return {"message": "Logged out successfully."}


@app.post(
    "/auth/logout-all",
    tags=["Auth"],
    summary="Sign out on every device",
    description="Invalidates every session issued so far (including this one) and clears the cookie. Use it if a device was lost.",
    responses={**_ok({"message": "Signed out on all devices."}), **E_401},
)
def logout_all(request: Request, response: Response, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=False)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if user:
            user.tokens_valid_after = _utcnow()
            db.commit()
    finally:
        db.close()
    clear_auth_cookie(response)
    return {"message": "Signed out on all devices."}


@app.get(
    "/auth/me",
    tags=["Auth"],
    summary="Current user",
    description="Returns the signed-in user. Frontends call this on load to learn whether a session exists; a guest gets 401.",
    responses={**_ok({"user": EXAMPLE_USER}), **E_401},
)
def get_current_user(request: Request, authorization: str | None = Header(None)):
    user_id = resolve_user_id(request, authorization, require_verified=False)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "is_verified": user.is_verified,
            }
        }
    finally:
        db.close()


@app.post(
    "/auth/verify-email",
    tags=["Auth"],
    summary="Verify email address",
    description="Consumes the one-time token from the verification email and signs you in.",
    responses={
        **_ok({"token": "<same value as the cookie>", "user": {**EXAMPLE_USER, "is_verified": True}, "message": f"Email verified successfully! Welcome to {APP_NAME}."}),
        **_err(400, "Invalid or expired token.", "Invalid or expired verification token."),
        **E_RATE,
    },
)
def verify_email(req: VerifyEmailRequest, response: Response):
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Verification token is required.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.verification_token == hash_token(token)).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid or expired verification token.")
        if user.verification_token_expires_at and user.verification_token_expires_at < _utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired verification token.")

        user.is_verified = True
        user.verification_token = None
        user.verification_token_expires_at = None
        db.commit()

        send_welcome_email(user.email, user.full_name)

        auth_token = create_access_token(user.id, user.email, user.hashed_password)
        set_auth_cookie(response, auth_token)

        return {
            "token": auth_token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "is_verified": True,
            },
            "message": f"Email verified successfully! Welcome to {APP_NAME}.",
        }
    finally:
        db.close()


@app.post(
    "/auth/resend-verification",
    tags=["Auth"],
    summary="Resend the verification email",
    description="Always answers the same way, whether or not the address exists, so it cannot be used to discover accounts.",
    responses={**_ok({"message": "If the account exists and is unverified, a new link has been sent."}), **E_RATE},
)
def resend_verification(req: ResendVerificationRequest, background_tasks: BackgroundTasks):
    email = req.email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        token = secrets.token_urlsafe(32)
        if user and not user.is_verified:
            user.verification_token = hash_token(token)
            user.verification_token_expires_at = _utcnow() + timedelta(hours=VERIFICATION_TOKEN_HOURS)
            db.commit()
            if email_allowed("verify", email):
                background_tasks.add_task(send_verification_email, user.email, token, user.full_name)
        return {"message": "If the account exists and is unverified, a new link has been sent."}
    finally:
        db.close()


@app.post(
    "/auth/forgot-password",
    tags=["Auth"],
    summary="Request a password reset",
    description="Emails a one-hour reset link if the address is registered. The response is identical either way.",
    responses={**_ok({"message": "If this email is registered, password reset instructions have been sent."}), **E_RATE},
)
def forgot_password(req: ForgotPasswordRequest, background_tasks: BackgroundTasks):
    email = req.email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        reset_token = secrets.token_urlsafe(32)
        if user:
            user.reset_token = hash_token(reset_token)
            user.reset_token_expires_at = _utcnow() + timedelta(hours=1)
            db.commit()
            if email_allowed("reset", email):
                background_tasks.add_task(send_password_reset_email, user.email, reset_token, user.full_name)
        return {"message": "If this email is registered, password reset instructions have been sent."}
    finally:
        db.close()


@app.post(
    "/auth/reset-password",
    tags=["Auth"],
    summary="Set a new password from a reset link",
    description="Consumes the reset token. **All existing sessions are invalidated.**",
    responses={
        **_ok({"message": "Password reset successfully. You can now log in with your new password."}),
        **_err(400, "Invalid or expired token, or weak password.", "Invalid or expired reset token."),
        **E_RATE,
    },
)
def reset_password(req: ResetPasswordRequest):
    validate_new_password(req.new_password)

    token = req.token.strip()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.reset_token == hash_token(token)).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid password reset token.")
        if not user.reset_token_expires_at or user.reset_token_expires_at < _utcnow():
            raise HTTPException(status_code=400, detail="Password reset token has expired.")

        user.hashed_password = hash_password(req.new_password)
        user.reset_token = None
        user.reset_token_expires_at = None
        db.commit()
        counter_reset(login_lock_key(user.email))  # a locked-out owner can get back in by resetting
        return {"message": "Password reset successfully. You can now log in with your new password."}
    finally:
        db.close()


@app.post(
    "/auth/change-password",
    tags=["Auth"],
    summary="Change password",
    description="Requires the current password. Other sessions are invalidated; this one receives a fresh cookie.",
    responses={
        **_ok({"message": "Password updated successfully."}),
        **_err(400, "Current password wrong or the new one is invalid.", "Current password is incorrect."),
        **E_401,
    },
)
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
):
    uid = resolve_user_id(request, authorization, require_verified=False)

    validate_new_password(req.new_password, "New password")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        if not verify_password(req.current_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        user.hashed_password = hash_password(req.new_password)
        db.commit()
        # Old sessions are now invalid (password fingerprint changed); keep this browser signed in.
        set_auth_cookie(response, create_access_token(user.id, user.email, user.hashed_password))
        return {"message": "Password updated successfully."}
    finally:
        db.close()


@app.delete(
    "/auth/delete-account",
    tags=["Auth"],
    summary="Delete the account and all its data",
    description="Permanently deletes the user, bookmarks and history. The password is mandatory in the body.",
    responses={
        **_ok({"message": "Account and all associated data deleted successfully."}),
        **_err(400, "Password confirmation incorrect.", "Password confirmation incorrect."),
        **E_401,
    },
)
def delete_account(
    req: DeleteAccountRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
):
    uid = resolve_user_id(request, authorization, require_verified=False)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        # Password confirmation is mandatory: a stolen session alone must not be able to wipe an account.
        if not verify_password(req.password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Password confirmation incorrect.")

        db.query(Bookmark).filter(Bookmark.user_id == uid).delete(synchronize_session=False)
        db.query(SearchHistory).filter(SearchHistory.user_id == uid).delete(synchronize_session=False)
        db.delete(user)
        db.commit()
        clear_auth_cookie(response)
        return {"message": "Account and all associated data deleted successfully."}
    finally:
        db.close()


@app.post(
    "/history/sync",
    tags=["History"],
    summary="Attach guest history to your account",
    description="Moves the unclaimed history of a guest `session_id` (created before you signed up) into your account. "
                "Entries already owned by an account are never touched. Verified accounts only.",
    responses={**_ok({"synced": 4}, "Number of entries claimed"), **E_401, **E_403},
)
def sync_search_history(
    req: SyncHistoryRequest,
    request: Request,
    authorization: str | None = Header(None),
):
    # Enforce email verification for history sync
    uid = resolve_user_id(request, authorization, require_verified=True)
    session_id = req.session_id.strip()
    if not session_id:
        return {"synced": 0}

    db = SessionLocal()
    try:
        updated = (
            db.query(SearchHistory)
            .filter(
                SearchHistory.session_id == session_id,
                SearchHistory.user_id.is_(None),
            )
            .update({"user_id": uid}, synchronize_session=False)
        )
        db.commit()
        return {"synced": updated}
    finally:
        db.close()


# ============================================================
# BOOKMARKS / MY LIBRARY ENDPOINTS (REQUIRE VERIFIED EMAIL)
# ============================================================

@app.post(
    "/bookmarks",
    tags=["Library"],
    summary="Save a paper",
    description="Saves a paper to your library. Saving the same DOI (or title when there is no DOI) again returns the existing "
                "entry with a `message` instead of creating a duplicate. Verified accounts only.",
    responses={**_ok(EXAMPLE_BOOKMARK, "The saved (or already existing) bookmark"), **E_401, **E_403},
)
def create_bookmark(
    req: BookmarkCreateRequest,
    request: Request,
    authorization: str | None = Header(None),
):
    # Enforce verified user account
    uid = resolve_user_id(request, authorization, require_verified=True)
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Paper title is required.")

    db = SessionLocal()
    try:
        query = db.query(Bookmark).filter(Bookmark.user_id == uid)
        if req.doi:
            existing = query.filter(Bookmark.doi == req.doi.strip()).first()
        else:
            existing = query.filter(Bookmark.title == req.title.strip()).first()

        if existing:
            return {
                "id": existing.id,
                "user_id": existing.user_id,
                "title": existing.title,
                "authors": existing.authors or [],
                "abstract": existing.abstract,
                "publication_year": existing.publication_year,
                "source": existing.source,
                "doi": existing.doi,
                "created_at": existing.created_at,
                "message": "Paper is already in your library."
            }

        bookmark = Bookmark(
            user_id=uid,
            title=req.title.strip(),
            authors=req.authors or [],
            abstract=req.abstract.strip() if req.abstract else None,
            publication_year=req.publication_year,
            source=req.source,
            doi=req.doi.strip() if req.doi else None,
        )
        db.add(bookmark)
        db.commit()
        db.refresh(bookmark)
        return {
            "id": bookmark.id,
            "user_id": bookmark.user_id,
            "title": bookmark.title,
            "authors": bookmark.authors or [],
            "abstract": bookmark.abstract,
            "publication_year": bookmark.publication_year,
            "source": bookmark.source,
            "doi": bookmark.doi,
            "created_at": bookmark.created_at,
        }
    finally:
        db.close()


@app.get(
    "/bookmarks",
    tags=["Library"],
    summary="List saved papers",
    description="Your saved papers, newest first, each with `collection_ids` (its folders). `collection_id` limits the list "
                "to one of your folders. Verified accounts only.",
    responses={**_ok({"bookmarks": [EXAMPLE_BOOKMARK]}), **E_401, **E_403},
)
def list_bookmarks(
    request: Request,
    collection_id: int | None = Query(default=None, ge=1),
    authorization: str | None = Header(None),
):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        query = db.query(Bookmark).filter(Bookmark.user_id == uid)
        if collection_id is not None:
            _own_collection(db, uid, collection_id)
            ids_in = [r[0] for r in db.query(CollectionItem.bookmark_id)
                      .filter(CollectionItem.collection_id == collection_id).all()]
            query = query.filter(Bookmark.id.in_(ids_in or [-1]))
        items = query.order_by(Bookmark.created_at.desc()).all()
        folder_map: dict[int, list[int]] = {}
        if items:
            own_folders = [c.id for c in db.query(Collection.id).filter(Collection.user_id == uid).all()]
            if own_folders:
                for cid, bid in (db.query(CollectionItem.collection_id, CollectionItem.bookmark_id)
                                 .filter(CollectionItem.collection_id.in_(own_folders),
                                         CollectionItem.bookmark_id.in_([b.id for b in items])).all()):
                    folder_map.setdefault(bid, []).append(cid)
        return {
            "bookmarks": [
                {
                    "id": b.id,
                    "user_id": b.user_id,
                    "title": b.title,
                    "authors": b.authors or [],
                    "abstract": b.abstract,
                    "publication_year": b.publication_year,
                    "source": b.source,
                    "doi": b.doi,
                    "created_at": b.created_at,
                    "collection_ids": sorted(folder_map.get(b.id, [])),
                }
                for b in items
            ]
        }
    finally:
        db.close()


@app.delete(
    "/bookmarks/{bookmark_id}",
    tags=["Library"],
    summary="Remove a saved paper",
    description="Removes one of **your** bookmarks. Someone else's id returns 404, the same as a missing one.",
    responses={**_ok({"message": "Bookmark removed successfully."}), **E_401, **E_403, **_err(404, "Not found, or not yours.", "Bookmark not found.")},
)
def delete_bookmark(
    bookmark_id: int,
    request: Request,
    authorization: str | None = Header(None),
):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        deleted = (
            db.query(Bookmark)
            .filter(Bookmark.id == bookmark_id, Bookmark.user_id == uid)
            .delete(synchronize_session=False)
        )
        if deleted:
            db.query(CollectionItem).filter(CollectionItem.bookmark_id == bookmark_id).delete(synchronize_session=False)
        db.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Bookmark not found.")
        return {"message": "Bookmark removed successfully."}
    finally:
        db.close()


# ============================================================
# FASTEMBED LOCAL EMBEDDINGS & SEMANTIC RE-RANKING
# ============================================================

embedding_model = None
if ENABLE_RERANKING:
    try:
        from fastembed import TextEmbedding
        embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("FastEmbed model (BAAI/bge-small-en-v1.5) loaded successfully.")
    except Exception as e:
        print(f"FastEmbed initialization note: {e}")
        embedding_model = None
else:
    print("Semantic re-ranking disabled (ENABLE_RERANKING=false).")


def compute_cosine_similarity(vec_a, vec_b) -> float:
    if vec_a is None or vec_b is None:
        return 0.0
    try:
        a = [float(x) for x in vec_a]
        b = [float(x) for x in vec_b]
        if len(a) != len(b) or len(a) == 0:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(dot / (norm_a * norm_b))
    except Exception:
        return 0.0


def rerank_papers_by_similarity(query: str, papers: list[dict]) -> list[dict]:
    if not papers:
        return papers

    if not embedding_model:
        for p in papers:
            p.setdefault("similarity_score", None)
        return papers

    try:
        # Generate query embedding locally using fastembed (0 API calls, 0 rate limits)
        q_vec = list(embedding_model.embed([query]))[0]

        # Batch embed paper titles and abstracts
        paper_texts = [
            f"{p.get('title', 'Untitled')}. {p.get('abstract', '') or ''}"[:1200]
            for p in papers
        ]
        paper_vecs = list(embedding_model.embed(paper_texts))

        if len(paper_vecs) == len(papers):
            for i, p in enumerate(papers):
                sim = compute_cosine_similarity(q_vec, paper_vecs[i])
                # Convert to percentage score
                p["similarity_score"] = round(max(0.0, min(1.0, float(sim))) * 100, 1)

            # Sort papers descending by similarity score
            papers.sort(
                key=lambda p: (p.get("similarity_score") is not None, p.get("similarity_score") or 0.0),
                reverse=True,
            )
        else:
            for p in papers:
                p.setdefault("similarity_score", None)

        return papers

    except Exception as error:
        print(f"FastEmbed semantic re-ranking note (fallback to default order): {error}")
        for p in papers:
            p.setdefault("similarity_score", None)
        return papers


# ============================================================
# COMMON RESULT FORMAT
# ============================================================

def normalize_result(
    title,
    authors=None,
    abstract=None,
    year=None,
    source="Unknown source",
    doi=None,
    is_open_access=None,
    database=None,
    cited_by_count=None,
    study_types=None,
    is_preprint=None,
    is_retracted=False,
    field=None,
):
    """Common paper shape. is_open_access / is_preprint are True/False when the source states it, None when
    unknown. cited_by_count is None when the source does not report citations (never guessed as 0).
    study_types holds canonical values from STUDY_TYPES; title-based detection is added on top."""
    paper = {
        "title": (title or "Untitled").strip(),
        "authors": authors or [],
        "abstract": abstract.strip() if abstract else None,
        "publication_year": year,
        "description": source or "Unknown source",
        "doi": doi,
        "is_open_access": is_open_access,
        "database": database or source or "Unknown",
        "cited_by_count": cited_by_count if isinstance(cited_by_count, int) and cited_by_count >= 0 else None,
        "study_types": [],
        "is_preprint": is_preprint,
        "is_retracted": bool(is_retracted),
        "field": field if field in FIELDS_OF_STUDY else None,
    }
    paper["study_types"] = classify_study_types(paper["title"], study_types or [])
    return paper


# ============================================================
# STUDY TYPES AND FIELDS OF STUDY
# ============================================================

# Canonical study types accepted by GET /search?study_type=...
STUDY_TYPES = ("meta_analysis", "systematic_review", "review", "rct", "clinical_trial", "case_report")

# Best-effort detection from the title, on top of what each source declares.
_TITLE_STUDY_PATTERNS = [
    ("meta_analysis", re.compile(r"\bmeta[\s-]?analy", re.IGNORECASE)),
    ("systematic_review", re.compile(r"\bsystematic (literature )?review", re.IGNORECASE)),
    ("rct", re.compile(r"\brandomi[sz]ed[\s,-]+(\w+[\s-]+){0,3}(controlled )?trial", re.IGNORECASE)),
    ("clinical_trial", re.compile(r"\b(clinical|randomi[sz]ed)[\s,-]+(\w+[\s-]+){0,3}trial", re.IGNORECASE)),
    ("case_report", re.compile(r"\bcase report", re.IGNORECASE)),
]


def classify_study_types(title: str, declared: list[str]) -> list[str]:
    found = [t for t in declared if t in STUDY_TYPES]
    for name, pattern in _TITLE_STUDY_PATTERNS:
        if pattern.search(title or ""):
            found.append(name)
    # Implied types: an RCT is a clinical trial; meta-analyses and systematic reviews are reviews.
    if "rct" in found:
        found.append("clinical_trial")
    if "meta_analysis" in found or "systematic_review" in found:
        found.append("review")
    return [t for t in STUDY_TYPES if t in found]


# PubMed / Europe PMC publication types -> canonical study types.
_PUBLICATION_TYPE_MAP = {
    "meta-analysis": "meta_analysis",
    "systematic review": "systematic_review",
    "review": "review",
    "review-article": "review",
    "randomized controlled trial": "rct",
    "clinical trial": "clinical_trial",
    "controlled clinical trial": "clinical_trial",
    "case reports": "case_report",
    "case-report": "case_report",
}


def map_publication_types(names) -> list[str]:
    out = []
    for name in names or []:
        key = str(name).strip().lower()
        if key.startswith("clinical trial"):
            key = "clinical trial"
        if key in _PUBLICATION_TYPE_MAP:
            out.append(_PUBLICATION_TYPE_MAP[key])
    return out


# Field of study filter. Each canonical field maps to OpenAlex field ids and Semantic Scholar field names.
FIELDS_OF_STUDY = {
    "Medicine": {"openalex": ["27", "29", "35", "36"], "s2": ["Medicine"]},
    "Biology": {"openalex": ["13", "24"], "s2": ["Biology"]},
    "Neuroscience": {"openalex": ["28"], "s2": []},
    "Psychology": {"openalex": ["32"], "s2": ["Psychology"]},
    "Computer Science": {"openalex": ["17"], "s2": ["Computer Science"]},
    "Engineering": {"openalex": ["22", "15", "21"], "s2": ["Engineering"]},
    "Physics": {"openalex": ["31"], "s2": ["Physics"]},
    "Chemistry": {"openalex": ["16"], "s2": ["Chemistry"]},
    "Mathematics": {"openalex": ["26", "18"], "s2": ["Mathematics"]},
    "Materials Science": {"openalex": ["25"], "s2": ["Materials Science"]},
    "Environmental Science": {"openalex": ["23"], "s2": ["Environmental Science"]},
    "Earth Sciences": {"openalex": ["19"], "s2": ["Geology", "Geography"]},
    "Agriculture": {"openalex": ["11", "34"], "s2": ["Agricultural and Food Sciences"]},
    "Pharmacology": {"openalex": ["30"], "s2": []},
    "Economics": {"openalex": ["20"], "s2": ["Economics"]},
    "Business": {"openalex": ["14"], "s2": ["Business"]},
    "Social Sciences": {"openalex": ["33"], "s2": ["Sociology", "Political Science", "Education", "Law", "Linguistics"]},
    "Arts and Humanities": {"openalex": ["12"], "s2": ["Art", "Philosophy", "History"]},
}

_OPENALEX_FIELD_TO_NAME = {fid: name for name, m in FIELDS_OF_STUDY.items() for fid in m["openalex"]}
_S2_FIELD_TO_NAME = {s2: name for name, m in FIELDS_OF_STUDY.items() for s2 in m["s2"]}


def openalex_field_name(work: dict) -> str | None:
    field = ((work.get("primary_topic") or {}).get("field") or {})
    fid = str(field.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
    return _OPENALEX_FIELD_TO_NAME.get(fid)


def s2_field_name(paper: dict) -> str | None:
    for item in paper.get("s2FieldsOfStudy") or []:
        name = _S2_FIELD_TO_NAME.get((item or {}).get("category"))
        if name:
            return name
    for category in paper.get("fieldsOfStudy") or []:
        name = _S2_FIELD_TO_NAME.get(category)
        if name:
            return name
    return None


# ============================================================
# DOI NORMALIZATION
# ============================================================

def normalize_doi(doi):

    if not doi:

        return None

    value = str(
        doi
    ).strip().lower()

    prefixes = [

        "https://doi.org/",

        "http://doi.org/",

        "https://dx.doi.org/",

        "http://dx.doi.org/",

        "doi:",
    ]

    for prefix in prefixes:

        if value.startswith(prefix):

            value = value[
                len(prefix):
            ]

    return (
        value.rstrip(".").strip()
    )


# ============================================================
# OPENALEX ABSTRACT
# ============================================================

def get_abstract(work):

    abstract_index = (
        work.get(
            "abstract_inverted_index"
        )
        or {}
    )

    if not abstract_index:

        return None

    abstract_words = []

    for word, positions in (
        abstract_index.items()
    ):

        for position in positions:

            abstract_words.append(
                (
                    position,
                    word,
                )
            )

    abstract_words.sort(
        key=lambda item: item[0]
    )

    abstract_text = " ".join(
        word
        for position, word
        in abstract_words
    )

    return (
        abstract_text.strip()
        or None
    )


# ============================================================
# OPENALEX
# ============================================================

def openalex_filter_parts(year_min=None, year_max=None, oa_only=False, filters=None):
    """OpenAlex applies these itself, so result slots are not wasted on papers we then discard."""
    parts = []
    if year_min is not None:
        parts.append(f"publication_year:>{year_min - 1}")
    if year_max is not None:
        parts.append(f"publication_year:<{year_max + 1}")
    if oa_only:
        parts.append("is_oa:true")
    flt = filters or {}
    if flt.get("min_citations"):
        parts.append(f"cited_by_count:>{flt['min_citations'] - 1}")
    if flt.get("exclude_preprints"):
        parts.append("type:!preprint")
    if flt.get("study_type") in ("review", "systematic_review", "meta_analysis"):
        parts.append("type:review")
    if flt.get("field") in FIELDS_OF_STUDY:
        parts.append("primary_topic.field.id:" + "|".join(FIELDS_OF_STUDY[flt["field"]]["openalex"]))
    return parts


def parse_openalex_work(work: dict, database: str = "OpenAlex") -> dict:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    authors = [
        a.get("author", {}).get("display_name")
        for a in work.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]
    work_type = (work.get("type") or "").lower()
    return normalize_result(
        title=work.get("title"),
        authors=authors,
        # No abstract means None. Never invent one: the AI must only be given real evidence.
        abstract=get_abstract(work),
        year=work.get("publication_year"),
        source=source.get("display_name") or "OpenAlex",
        doi=work.get("doi"),
        is_open_access=bool((work.get("open_access") or {}).get("is_oa")),
        database=database,
        cited_by_count=work.get("cited_by_count"),
        study_types=["review"] if work_type == "review" else [],
        is_preprint=(work_type == "preprint") if work_type else None,
        is_retracted=bool(work.get("is_retracted")),
        field=openalex_field_name(work),
    )


async def search_openalex(client, q, year_min=None, year_max=None, oa_only=False, filters=None, mode="topic",
                          extra_filter=None, database_label="OpenAlex", health_name="openalex"):
    url = "https://api.openalex.org/works"
    params = {
        "per-page": 15,
        "mailto": CONTACT_EMAIL,
    }
    filter_parts = openalex_filter_parts(year_min, year_max, oa_only, filters)
    if extra_filter:
        filter_parts.append(extra_filter)
    if mode == "title":
        # Commas separate OpenAlex filters, so they are removed from the title text.
        filter_parts.append("title.search:" + q.rstrip("?").replace(",", " "))
    else:
        params["search"] = q.rstrip("?")
    if filter_parts:
        params["filter"] = ",".join(filter_parts)
    headers = {
        "User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{CONTACT_EMAIL})"
    }

    try:
        response = await client.get(url, params=params, headers=headers, timeout=10.0)
        if response.status_code == 429:
            source_failed(health_name, "rate limited")
            print(f"{database_label} (OpenAlex) rate limited (429). Skipping.")
            return []
        response.raise_for_status()
        data = response.json()
        return [parse_openalex_work(work, database_label) for work in data.get("results", [])]
    except Exception as error:
        source_failed(health_name, type(error).__name__)
        print(f"{database_label} (OpenAlex) search note:", error)
        return []


async def search_openalex_by_author(client, name, year_min=None, year_max=None, oa_only=False, filters=None):
    """Find the best-matching author, then their works (most cited first)."""
    headers = {"User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{CONTACT_EMAIL})"}
    try:
        response = await client.get(
            "https://api.openalex.org/authors",
            params={"search": name, "per-page": 1, "mailto": CONTACT_EMAIL},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code == 429:
            source_failed("openalex", "rate limited")
            print("OpenAlex rate limited (429). Skipping.")
            return []
        response.raise_for_status()
        authors = response.json().get("results") or []
        if not authors:
            return []
        author_id = str(authors[0].get("id") or "").rstrip("/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"A\d+", author_id):
            return []
        filter_parts = [f"author.id:{author_id}"] + openalex_filter_parts(year_min, year_max, oa_only, filters)
        response = await client.get(
            "https://api.openalex.org/works",
            params={"filter": ",".join(filter_parts), "sort": "cited_by_count:desc", "per-page": 25,
                    "mailto": CONTACT_EMAIL},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code == 429:
            source_failed("openalex", "rate limited")
            print("OpenAlex rate limited (429). Skipping.")
            return []
        response.raise_for_status()
        return [parse_openalex_work(work) for work in response.json().get("results", [])]
    except Exception as error:
        source_failed("openalex", type(error).__name__)
        print("OpenAlex author search note:", error)
        return []


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================

_S2_STUDY_TYPE_PARAM = {
    "review": "Review",
    "systematic_review": "Review",
    "meta_analysis": "MetaAnalysis",
    "rct": "ClinicalTrial",
    "clinical_trial": "ClinicalTrial",
    "case_report": "CaseReport",
}
_S2_TYPE_TO_STUDY = {"Review": "review", "MetaAnalysis": "meta_analysis", "ClinicalTrial": "clinical_trial",
                     "CaseReport": "case_report"}
_PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "ssrn", "preprints", "research square", "chemrxiv", "psyarxiv")


async def search_semantic_scholar(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": q.rstrip("?"),
        "limit": 10,
        "fields": "title,authors,abstract,year,externalIds,venue,isOpenAccess,openAccessPdf,"
                  "citationCount,publicationTypes,s2FieldsOfStudy",
    }
    if year_min is not None or year_max is not None:
        # Semantic Scholar range syntax: "2023-", "-2020", "2020-2023"
        params["year"] = f"{year_min if year_min is not None else ''}-{year_max if year_max is not None else ''}"
    if oa_only:
        params["openAccessPdf"] = ""  # flag parameter: only papers with a public PDF
    flt = filters or {}
    if flt.get("min_citations"):
        params["minCitationCount"] = str(flt["min_citations"])
    if flt.get("study_type") in _S2_STUDY_TYPE_PARAM:
        params["publicationTypes"] = _S2_STUDY_TYPE_PARAM[flt["study_type"]]
    if flt.get("field") in FIELDS_OF_STUDY and FIELDS_OF_STUDY[flt["field"]]["s2"]:
        params["fieldsOfStudy"] = ",".join(FIELDS_OF_STUDY[flt["field"]]["s2"])

    headers = {}
    s2_key = effective_key("SEMANTIC_SCHOLAR_API_KEY")
    if s2_key:
        headers["x-api-key"] = s2_key

    try:
        response = await client.get(url, params=params, headers=headers)
        if response.status_code == 429:
            source_failed("semantic_scholar", "rate limited")
            print("Semantic Scholar rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        data = response.json()
        results = []

        for paper in data.get("data", []):
            external_ids = paper.get("externalIds") or {}
            authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]
            abstract = paper.get("abstract")
            if not abstract:
                continue
            venue = (paper.get("venue") or "").lower()
            declared = [_S2_TYPE_TO_STUDY[t] for t in (paper.get("publicationTypes") or []) if t in _S2_TYPE_TO_STUDY]

            results.append(
                normalize_result(
                    title=paper.get("title"),
                    authors=authors,
                    abstract=abstract,
                    year=paper.get("year"),
                    source=paper.get("venue") or "Semantic Scholar",
                    doi=external_ids.get("DOI"),
                    is_open_access=bool(paper.get("isOpenAccess") or paper.get("openAccessPdf")),
                    database="Semantic Scholar",
                    cited_by_count=paper.get("citationCount"),
                    study_types=declared,
                    is_preprint=True if any(v in venue for v in _PREPRINT_VENUES) else None,
                    field=s2_field_name(paper),
                )
            )

        return results

    except Exception as error:
        source_failed("semantic_scholar", type(error).__name__)
        print(f"Semantic Scholar search failed: {error}")
        return []


# ============================================================
# ARXIV (every arXiv paper is open access, and a preprint)
# ============================================================

async def search_arxiv(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    if (filters or {}).get("exclude_preprints"):
        return []
    url = "https://export.arxiv.org/api/query"
    search_query = f"all:{q.rstrip('?')}"
    if year_min is not None or year_max is not None:
        start = f"{year_min if year_min is not None else 1991}01010000"
        end = f"{year_max if year_max is not None else 2100}12312359"
        search_query += f" AND submittedDate:[{start} TO {end}]"
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": 10,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/atom+xml,application/xml,text/xml",
    }
    try:
        response = await client.get(url, params=params, headers=headers, timeout=8.0)
        if response.status_code != 200:
            source_failed("arxiv", f"HTTP {response.status_code}")
            return []
        root = ET.fromstring(response.text)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", namespace):
            title = (entry.findtext("atom:title", default="Untitled", namespaces=namespace) or "Untitled").strip()
            abstract = (entry.findtext("atom:summary", default="", namespaces=namespace) or "").strip()
            if not abstract:
                continue
            published = entry.findtext("atom:published", default="", namespaces=namespace) or ""
            year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
            authors = [
                a.findtext("atom:name", default="", namespaces=namespace).strip()
                for a in entry.findall("atom:author", namespace)
                if a.findtext("atom:name", default="", namespaces=namespace)
            ]
            doi = None
            for link in entry.findall("atom:link", namespace):
                href = link.get("href") or ""
                if "doi.org" in href.lower():
                    doi = href
                    break
            results.append(
                normalize_result(
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    year=year,
                    source="arXiv",
                    doi=doi,
                    is_open_access=True,
                    database="arXiv",
                    is_preprint=True,
                )
            )
        return results
    except Exception as error:
        source_failed("arxiv", type(error).__name__)
        print("arXiv search note:", error)
        return []


# ============================================================
# CROSSREF (does not say whether a paper is open access: is_open_access stays None)
# ============================================================

def parse_crossref_work(work: dict, require_abstract: bool = True) -> dict | None:
    titles = work.get("title") or []
    title = titles[0] if titles else "Untitled"
    authors = []
    for author in (work.get("author") or []):
        name = f"{author.get('given', '') or ''} {author.get('family', '') or ''}".strip()
        if name:
            authors.append(name)
    abstract = re.sub(r"<[^>]+>", "", work.get("abstract") or "").strip() or None
    if require_abstract and not abstract:
        return None
    year = None
    for date_field in ("published-print", "published-online", "published", "posted"):
        date_parts = (work.get(date_field) or {}).get("date-parts") or []
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = date_parts[0][0]
            break
    container_titles = work.get("container-title") or []
    work_type = (work.get("type") or "").lower()
    return normalize_result(
        title=title,
        authors=authors,
        abstract=abstract,
        year=year,
        source=container_titles[0] if container_titles else "Crossref",
        doi=work.get("DOI"),
        is_open_access=None,
        database="Crossref",
        cited_by_count=work.get("is-referenced-by-count"),
        is_preprint=(work_type == "posted-content") if work_type else None,
    )


async def search_crossref(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    url = "https://api.crossref.org/works"
    params = {
        "query": q.rstrip("?"),
        "rows": 10,
        "select": "title,author,abstract,published-print,published-online,DOI,container-title,type,"
                  "is-referenced-by-count",
    }
    date_filters = []
    if year_min is not None:
        date_filters.append(f"from-pub-date:{year_min}-01-01")
    if year_max is not None:
        date_filters.append(f"until-pub-date:{year_max}-12-31")
    if date_filters:
        params["filter"] = ",".join(date_filters)
    try:
        response = await client.get(url, params=params)
        if response.status_code == 429:
            source_failed("crossref", "rate limited")
            print("Crossref rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        data = response.json()
        items = data.get("message", {}).get("items", [])
        return [p for p in (parse_crossref_work(work) for work in items) if p]
    except Exception as error:
        source_failed("crossref", type(error).__name__)
        print(f"Crossref search failed: {error}")
        return []


# ============================================================
# CORE (an aggregator of open-access repositories: every record is open access)
# ============================================================

async def search_core(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    core_key = effective_key("CORE_API_KEY")
    if not core_key:
        print("CORE_API_KEY not configured. Skipping CORE.")
        return []
    url = "https://api.core.ac.uk/v3/search/works/"
    headers = {"Authorization": f"Bearer {core_key}"}
    query = q.rstrip("?")
    if year_min is not None:
        query = f"({query}) AND yearPublished>={year_min}"
    if year_max is not None:
        query = f"({query}) AND yearPublished<={year_max}"
    params = {"q": query, "limit": 10}
    try:
        response = await client.get(url, params=params, headers=headers)
        if response.status_code in (401, 403):
            source_failed("core", "key rejected")
        response.raise_for_status()
        data = response.json()
        results = []
        for work in (data.get("results") or []):
            authors = []
            for author in (work.get("authors") or []):
                name = author.get("name") if isinstance(author, dict) else str(author)
                if name:
                    authors.append(name)
            abstract = work.get("abstract")
            if not abstract:
                continue
            results.append(
                normalize_result(
                    title=work.get("title"),
                    authors=authors,
                    abstract=abstract,
                    year=work.get("yearPublished"),
                    source="CORE",
                    doi=work.get("doi"),
                    is_open_access=True,
                    database="CORE",
                    cited_by_count=work.get("citationCount"),
                )
            )
        return results
    except Exception as error:
        source_failed("core", type(error).__name__)
        print(f"CORE search failed: {error}")
        return []


# ============================================================
# PUBMED (NCBI E-utilities: esearch finds ids, efetch returns the records)
# ============================================================

NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PUBMED_STUDY_TERMS = {
    "meta_analysis": "meta-analysis[pt]",
    "systematic_review": "systematic review[pt]",
    "review": "review[pt]",
    "rct": "randomized controlled trial[pt]",
    "clinical_trial": "clinical trial[pt]",
    "case_report": "case reports[pt]",
}


def ncbi_params() -> dict:
    params = {"tool": APP_UA_NAME}
    email = NCBI_EMAIL or CONTACT_EMAIL
    if email:
        params["email"] = email
    ncbi_key = effective_key("NCBI_API_KEY")
    if ncbi_key:
        params["api_key"] = ncbi_key
    return params


def _xml_text(element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""


def parse_pubmed_article(article) -> dict | None:
    title = _xml_text(article.find(".//ArticleTitle"))
    sections = []
    for part in article.findall(".//Abstract/AbstractText"):
        text = _xml_text(part)
        if text:
            label = part.get("Label")
            sections.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(sections)
    if not title or not abstract:
        return None
    authors = []
    for author in article.findall(".//AuthorList/Author"):
        collective = author.findtext("CollectiveName")
        name = collective or f"{author.findtext('ForeName') or ''} {author.findtext('LastName') or ''}".strip()
        if name:
            authors.append(name)
    year = None
    for path in (".//Journal/JournalIssue/PubDate/Year", ".//Journal/JournalIssue/PubDate/MedlineDate",
                 ".//ArticleDate/Year"):
        match = re.search(r"\d{4}", article.findtext(path) or "")
        if match:
            year = int(match.group(0))
            break
    doi = None
    for article_id in article.findall(".//ArticleIdList/ArticleId"):
        if article_id.get("IdType") == "doi" and article_id.text:
            doi = article_id.text.strip()
            break
    if not doi:
        for location in article.findall(".//ELocationID"):
            if location.get("EIdType") == "doi" and location.text:
                doi = location.text.strip()
                break
    publication_types = [_xml_text(pt) for pt in article.findall(".//PublicationTypeList/PublicationType")]
    lowered = [pt.lower() for pt in publication_types]
    return normalize_result(
        title=title,
        authors=authors,
        abstract=abstract,
        year=year,
        source=_xml_text(article.find(".//Journal/Title")) or "PubMed",
        doi=doi,
        is_open_access=None,
        database="PubMed",
        study_types=map_publication_types(publication_types),
        is_preprint="preprint" in lowered,
        is_retracted="retracted publication" in lowered,
    )


async def search_pubmed(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    flt = filters or {}
    term = f"({q.rstrip('?')})"
    if flt.get("study_type") in _PUBMED_STUDY_TERMS:
        term += f" AND {_PUBMED_STUDY_TERMS[flt['study_type']]}"
    if flt.get("exclude_preprints"):
        term += " NOT preprint[pt]"
    params = {**ncbi_params(), "db": "pubmed", "term": term, "retmax": 10, "retmode": "json", "sort": "relevance"}
    if year_min is not None or year_max is not None:
        params.update({"datetype": "pdat", "mindate": year_min if year_min is not None else 1800,
                       "maxdate": year_max if year_max is not None else 3000})
    try:
        response = await client.get(f"{NCBI_EUTILS}/esearch.fcgi", params=params, timeout=10.0)
        if response.status_code == 429:
            source_failed("pubmed", "rate limited")
            print("PubMed rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        ids = [i for i in ((response.json().get("esearchresult") or {}).get("idlist") or []) if str(i).isdigit()]
        if not ids:
            return []
        response = await client.get(
            f"{NCBI_EUTILS}/efetch.fcgi",
            params={**ncbi_params(), "db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=15.0,
        )
        if response.status_code == 429:
            source_failed("pubmed", "rate limited")
            print("PubMed rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        root = ET.fromstring(response.text)
        return [p for p in (parse_pubmed_article(a) for a in root.findall(".//PubmedArticle")) if p]
    except Exception as error:
        source_failed("pubmed", type(error).__name__)
        print(f"PubMed search failed: {error}")
        return []


# ============================================================
# EUROPE PMC (no key needed; covers PubMed plus preprints and full-text archives)
# ============================================================

_EUROPEPMC_STUDY_TERMS = {
    "meta_analysis": 'PUB_TYPE:"Meta-Analysis"',
    "systematic_review": 'PUB_TYPE:"Systematic Review"',
    "review": 'PUB_TYPE:"review"',
    "rct": 'PUB_TYPE:"Randomized Controlled Trial"',
    "clinical_trial": 'PUB_TYPE:"Clinical Trial"',
    "case_report": 'PUB_TYPE:"Case Reports"',
}


def parse_europepmc_record(record: dict) -> dict | None:
    abstract = re.sub(r"<[^>]+>", " ", record.get("abstractText") or "")
    abstract = re.sub(r"\s+", " ", abstract).strip()
    title = re.sub(r"<[^>]+>", "", record.get("title") or "").strip()
    if not title or not abstract:
        return None
    authors = [a.strip() for a in (record.get("authorString") or "").rstrip(".").split(",") if a.strip()]
    year = int(record["pubYear"]) if str(record.get("pubYear") or "").isdigit() else None
    journal = ((record.get("journalInfo") or {}).get("journal") or {}).get("title") or record.get("journalTitle")
    publication_types = (record.get("pubTypeList") or {}).get("pubType") or []
    if isinstance(publication_types, str):
        publication_types = [publication_types]
    cited = record.get("citedByCount")
    return normalize_result(
        title=title,
        authors=authors[:50],
        abstract=abstract,
        year=year,
        source=journal or "Europe PMC",
        doi=record.get("doi"),
        is_open_access=(record.get("isOpenAccess") == "Y"),
        database="Europe PMC",
        cited_by_count=int(cited) if str(cited or "").isdigit() else None,
        study_types=map_publication_types(publication_types),
        is_preprint=(record.get("source") == "PPR") or ("preprint" in [str(p).lower() for p in publication_types]),
        is_retracted="retracted publication" in [str(p).lower() for p in publication_types],
    )


async def search_europepmc(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    flt = filters or {}
    parts = [f"({q.rstrip('?')})"]
    if year_min is not None or year_max is not None:
        parts.append(f"PUB_YEAR:[{year_min if year_min is not None else 1800} TO "
                     f"{year_max if year_max is not None else 3000}]")
    if oa_only:
        parts.append("OPEN_ACCESS:y")
    if flt.get("study_type") in _EUROPEPMC_STUDY_TERMS:
        parts.append(_EUROPEPMC_STUDY_TERMS[flt["study_type"]])
    query = " AND ".join(parts)
    if flt.get("exclude_preprints"):
        query += " NOT SRC:PPR"
    params = {"query": query, "format": "json", "resultType": "core", "pageSize": 10}
    try:
        response = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                    params=params, timeout=10.0)
        if response.status_code == 429:
            source_failed("europepmc", "rate limited")
            print("Europe PMC rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        records = (response.json().get("resultList") or {}).get("result") or []
        return [p for p in (parse_europepmc_record(r) for r in records) if p]
    except Exception as error:
        source_failed("europepmc", type(error).__name__)
        print(f"Europe PMC search failed: {error}")
        return []


# ============================================================
# DOI LOOKUP (a query that is just a DOI is fetched directly)
# ============================================================

DOI_PATTERN = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def extract_doi_query(query: str) -> str | None:
    """Return the DOI when the whole query is a DOI (bare, doi: prefixed, or a doi.org link)."""
    candidate = normalize_doi(query.strip())
    if candidate and DOI_PATTERN.fullmatch(candidate) and len(candidate) <= 255:
        return candidate.rstrip(".,;")
    return None


async def lookup_doi(client, doi: str) -> list[dict]:
    from urllib.parse import quote

    encoded = quote(doi, safe="/()._-;:")
    headers = {"User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{CONTACT_EMAIL})"}
    try:
        response = await client.get(f"https://api.openalex.org/works/doi:{encoded}",
                                    params={"mailto": CONTACT_EMAIL}, headers=headers, timeout=10.0)
        if response.status_code == 200:
            return [parse_openalex_work(response.json())]
    except Exception as error:
        print("OpenAlex DOI lookup note:", error)
    try:
        response = await client.get(f"https://api.crossref.org/works/{encoded}", headers=headers, timeout=10.0)
        if response.status_code == 200:
            paper = parse_crossref_work(response.json().get("message") or {}, require_abstract=False)
            return [paper] if paper else []
    except Exception as error:
        print("Crossref DOI lookup note:", error)
    return []


# ============================================================
# DOI / TITLE DEDUPLICATION
# ============================================================

def deduplicate_results(results):
    """Drop duplicates (same DOI, else same title) but merge what each source knows: if any source says
    the paper is open access (or retracted) it is, the highest citation count wins, study types are combined,
    and a missing abstract/year/field is filled from the duplicate."""
    unique_results = []
    by_doi = {}
    by_title = {}
    for result in results:
        doi = normalize_doi(result.get("doi"))
        title = (result.get("title") or "").strip().lower()
        # Same rule as before: a paper with a DOI is a duplicate only by DOI; one without by title.
        kept = by_doi.get(doi) if doi else (by_title.get(title) if title else None)
        if kept is not None:
            if result.get("is_open_access") is True:
                kept["is_open_access"] = True
            elif kept.get("is_open_access") is None and result.get("is_open_access") is False:
                kept["is_open_access"] = False
            if not kept.get("abstract") and result.get("abstract"):
                kept["abstract"] = result["abstract"]
            if kept.get("publication_year") is None and result.get("publication_year") is not None:
                kept["publication_year"] = result["publication_year"]
            if result.get("is_retracted"):
                kept["is_retracted"] = True
            if kept.get("is_preprint") is None and result.get("is_preprint") is not None:
                kept["is_preprint"] = result["is_preprint"]
            if isinstance(result.get("cited_by_count"), int) and (
                kept.get("cited_by_count") is None or result["cited_by_count"] > kept["cited_by_count"]
            ):
                kept["cited_by_count"] = result["cited_by_count"]
            if result.get("study_types"):
                merged = set(kept.get("study_types") or []) | set(result["study_types"])
                kept["study_types"] = [t for t in STUDY_TYPES if t in merged]
            if not kept.get("field") and result.get("field"):
                kept["field"] = result["field"]
            continue
        unique_results.append(result)
        if doi:
            by_doi[doi] = result
        elif title:
            by_title[title] = result
    return unique_results


# ============================================================
# RESULT FILTERS AND SORTING
# ============================================================

def apply_result_filters(results, year_min=None, year_max=None, oa_only=False, filters=None):
    """Safety net: enforce every filter again on the merged list (sources can be loose, and some sources
    cannot filter at all). Papers whose value is unknown are dropped for that filter, never guessed."""
    flt = filters or {}
    kept = []
    for r in results:
        y = r.get("publication_year")
        if year_min is not None and (y is None or y < year_min):
            continue
        if year_max is not None and (y is None or y > year_max):
            continue
        if oa_only and r.get("is_open_access") is not True:
            continue  # only papers a source confirmed as open access
        if flt.get("min_citations"):
            cited = r.get("cited_by_count")
            if not isinstance(cited, int) or cited < flt["min_citations"]:
                continue
        if flt.get("exclude_preprints") and r.get("is_preprint") is True:
            continue
        if flt.get("study_type") and flt["study_type"] not in (r.get("study_types") or []):
            continue
        if flt.get("field") and r.get("field") != flt["field"]:
            continue
        kept.append(r)
    return kept


def sort_results(results, sort="relevance"):
    if sort == "newest":
        return sorted(results, key=lambda p: (p.get("publication_year") is not None, p.get("publication_year") or 0),
                      reverse=True)
    if sort == "most_cited":
        return sorted(results, key=lambda p: (isinstance(p.get("cited_by_count"), int), p.get("cited_by_count") or 0),
                      reverse=True)
    return results


# ============================================================
# SEARCH QUERY REFINEMENT
# ============================================================

def clean_academic_query(query: str) -> str:
    q = query.strip()
    # Normalize common technical terms and typos
    q = re.sub(r'\bverisons\b', 'versions', q, flags=re.IGNORECASE)

    # Strip conversational question phrases and filler words
    filler_words = [
        r'\bhow many\b', r'\bwhat is\b', r'\bwhat are\b', r'\bwhy is\b', r'\bwhy are\b',
        r'\btell me about\b', r'\bcan you\b', r'\bcould you\b', r'\bplease\b',
        r'\bexplain\b', r'\bdescribe\b', r'\bis there\b', r'\bare there\b',
        r'\blist of\b', r'\blist\b', r'\btotal\b', r'\bfind\b', r'\bsearch for\b',
    ]
    cleaned = q
    for pat in filler_words:
        cleaned = re.sub(pat, ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ?.,!").strip()

    # For specialized keywords like YOLO, append object detection context if missing
    if re.search(r'\byolo\b', cleaned, re.IGNORECASE) and not re.search(r'object\s+detection', cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} object detection"

    return cleaned if len(cleaned) >= 2 else q


# ============================================================
# REDIS CACHING (WITH IN-MEMORY FALLBACK)
# ============================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
redis_client = None

try:
    import redis
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    redis_client.ping()
    print(f"Connected to Redis cache at {REDIS_URL}")
except Exception as e:
    print(f"Redis cache notice ({e}). Using in-memory fallback cache.")
    redis_client = None

SEARCH_CACHE_TTL = 7200  # 2 hours in seconds
IN_MEMORY_CACHE: dict[str, dict] = {}


def get_from_search_cache(cache_key: str) -> dict | None:
    # 1. Try Redis first
    if redis_client is not None:
        try:
            val = redis_client.get(f"search:{cache_key}")
            if val:
                return json.loads(val)
        except Exception as e:
            print(f"Redis get error: {e}")

    # 2. Fallback to in-memory cache
    cached = IN_MEMORY_CACHE.get(cache_key)
    if not cached:
        return None
    if time.time() > cached.get("expires_at", 0):
        IN_MEMORY_CACHE.pop(cache_key, None)
        return None
    return cached.get("data")


def set_in_search_cache(cache_key: str, data: dict, ttl: int = SEARCH_CACHE_TTL):
    # 1. Store in Redis
    if redis_client is not None:
        try:
            redis_client.setex(f"search:{cache_key}", ttl, json.dumps(data))
        except Exception as e:
            print(f"Redis set error: {e}")

    # 2. Store in memory fallback
    if len(IN_MEMORY_CACHE) > 500:
        now = time.time()
        for k in [k for k, v in IN_MEMORY_CACHE.items() if v.get("expires_at", 0) < now]:
            IN_MEMORY_CACHE.pop(k, None)
        if len(IN_MEMORY_CACHE) > 500:
            for k in list(IN_MEMORY_CACHE.keys())[:100]:
                IN_MEMORY_CACHE.pop(k, None)

    IN_MEMORY_CACHE[cache_key] = {
        "data": data,
        "expires_at": time.time() + ttl,
    }


def generate_ask_cache_key(question: str, papers: list[dict], conversation_history: list[dict], mode: str = "quick") -> str:
    raw = json.dumps({
        "v": 3,  # v3 answers include takeaways and the evidence meter; older cached answers are not reused
        "m": mode,
        "q": question.strip().lower(),
        "p": [(p.get("title", ""), (p.get("abstract") or "")[:200]) for p in papers],
        "h": [(h.get("question", ""), h.get("answer", "")) for h in conversation_history],
    }, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def takeaway_cache_key(title: str, abstract: str) -> str:
    """Per-paper key. Uses the first 300 abstract characters so /ask (which trims abstracts to 400) and
    /takeaways (which sends more) share entries."""
    raw = json.dumps([str(title or "").strip().lower(), str(abstract or "").strip()[:300]])
    return "tk:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# SOURCE REGISTRY, ON/OFF SWITCHES AND HEALTH (Phase 6b)
# Every paper source is listed here. Admins can switch a source off from the admin panel (saved in the database,
# no redeploy). Health numbers (calls, failures, speed, results) are kept in memory since the last restart.
# ============================================================

SOURCE_REGISTRY = {
    "openalex": {"label": "OpenAlex", "key": None, "key_required": False},
    "semantic_scholar": {"label": "Semantic Scholar", "key": "SEMANTIC_SCHOLAR_API_KEY", "key_required": False},
    "arxiv": {"label": "arXiv", "key": None, "key_required": False},
    "crossref": {"label": "Crossref", "key": None, "key_required": False},
    "core": {"label": "CORE", "key": "CORE_API_KEY", "key_required": True},
    "pubmed": {"label": "PubMed", "key": "NCBI_API_KEY", "key_required": False},
    "europepmc": {"label": "Europe PMC", "key": None, "key_required": False},
    "doaj": {"label": "DOAJ (open-access journals)", "key": None, "key_required": False},
    "eric": {"label": "ERIC (education research)", "key": None, "key_required": False},
    "openaire": {"label": "OpenAIRE (European research)", "key": None, "key_required": False},
    "ajol": {"label": "AJOL (African Journals Online)", "key": None, "key_required": False},
    "unpaywall": {"label": "Unpaywall (free PDF links)", "key": None, "key_required": False},
}
SEARCH_SOURCES = [name for name in SOURCE_REGISTRY if name != "unpaywall"]


class SourceSetting(Base):
    """A row means an admin changed the default (every source is on by default)."""
    __tablename__ = "source_settings"

    name = Column(String(40), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(Integer, nullable=True)


try:
    Base.metadata.create_all(bind=engine, tables=[SourceSetting.__table__])
except Exception as _source_table_error:
    print("Source settings table note:", _source_table_error)

_SOURCE_SWITCHES: dict[str, bool] | None = None
_SOURCE_SWITCHES_LOCK = threading.Lock()
_SOURCE_HEALTH: dict[str, dict] = {}
_SOURCE_HEALTH_LOCK = threading.Lock()


def _load_source_switches() -> dict[str, bool]:
    global _SOURCE_SWITCHES
    with _SOURCE_SWITCHES_LOCK:
        if _SOURCE_SWITCHES is None:
            switches = {}
            db = SessionLocal()
            try:
                for row in db.query(SourceSetting).all():
                    switches[row.name] = bool(row.enabled)
            except Exception as error:
                print("Source switches note:", type(error).__name__)
            finally:
                db.close()
            _SOURCE_SWITCHES = switches
        return _SOURCE_SWITCHES


def reset_source_switch_cache():
    global _SOURCE_SWITCHES
    with _SOURCE_SWITCHES_LOCK:
        _SOURCE_SWITCHES = None


def source_enabled(name: str) -> bool:
    return _load_source_switches().get(name, True)


def _health(name: str) -> dict:
    return _SOURCE_HEALTH.setdefault(name, {"calls": 0, "failures": 0, "results": 0, "total_ms": 0,
                                            "last_ok_at": None, "last_error": None, "last_error_at": None})


def source_failed(name: str, reason: str):
    """Called by a source when a request fails (rate limit, timeout, bad response). Never includes keys or URLs."""
    with _SOURCE_HEALTH_LOCK:
        h = _health(name)
        h["failures"] += 1
        h["last_error"] = str(reason)[:120]
        h["last_error_at"] = _utcnow().isoformat()


async def tracked(name: str, coro):
    """Run one source call and record how long it took and how many papers it returned."""
    started = time.perf_counter()
    try:
        result = await coro
    except Exception as error:
        source_failed(name, type(error).__name__)
        result = []
    elapsed = int((time.perf_counter() - started) * 1000)
    with _SOURCE_HEALTH_LOCK:
        h = _health(name)
        h["calls"] += 1
        h["total_ms"] += elapsed
        count = len(result) if isinstance(result, list) else 0
        h["results"] += count
        if count:
            h["last_ok_at"] = _utcnow().isoformat()
    return result


_QUERY_SYNTAX = re.compile(r'[\\/:\[\](){}"^~*?!<>=|&+]')


def plain_query(q: str) -> str:
    """Keywords only: removes query-language characters so user text cannot change a source's search syntax."""
    text = _QUERY_SYNTAX.sub(" ", q or "")
    text = re.sub(r"\b(AND|OR|NOT|TO)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:300]


# ============================================================
# DOAJ (Directory of Open Access Journals; every article is open access)
# ============================================================

async def search_doaj(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    from urllib.parse import quote

    query = plain_query(q)
    if not query:
        return []
    if year_min is not None or year_max is not None:
        query = f"({query}) AND bibjson.year:[{year_min if year_min is not None else 1000} TO {year_max if year_max is not None else 3000}]"
    try:
        response = await client.get(f"https://doaj.org/api/search/articles/{quote(query, safe='')}",
                                    params={"pageSize": 10}, timeout=10.0)
        if response.status_code == 429:
            source_failed("doaj", "rate limited")
            return []
        response.raise_for_status()
        results = []
        for item in (response.json().get("results") or []):
            bib = item.get("bibjson") or {}
            abstract = re.sub(r"<[^>]+>", " ", bib.get("abstract") or "")
            abstract = re.sub(r"\s+", " ", abstract).strip()
            if not abstract:
                continue
            doi = next((i.get("id") for i in (bib.get("identifier") or []) if (i.get("type") or "").lower() == "doi"), None)
            year = bib.get("year")
            results.append(normalize_result(
                title=bib.get("title"),
                authors=[a.get("name") for a in (bib.get("author") or []) if a.get("name")][:50],
                abstract=abstract,
                year=int(year) if str(year or "").isdigit() else None,
                source=(bib.get("journal") or {}).get("title") or "DOAJ",
                doi=doi,
                is_open_access=True,
                database="DOAJ",
                is_preprint=False,
            ))
        return results
    except Exception as error:
        source_failed("doaj", type(error).__name__)
        print("DOAJ search failed:", type(error).__name__)
        return []


# ============================================================
# ERIC (US Department of Education research database)
# ============================================================

async def search_eric(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    query = plain_query(q)
    if not query:
        return []
    if year_min is not None or year_max is not None:
        query = f"({query}) AND publicationdateyear:[{year_min if year_min is not None else 1000} TO {year_max if year_max is not None else 3000}]"
    params = {"search": query, "rows": 10, "format": "json",
              "fields": "id,title,author,description,publicationdateyear,source,peerreviewed,url,e_fulltextauth"}
    try:
        response = await client.get("https://api.ies.ed.gov/eric/", params=params, timeout=10.0)
        if response.status_code == 429:
            source_failed("eric", "rate limited")
            return []
        response.raise_for_status()
        results = []
        for doc in ((response.json().get("response") or {}).get("docs") or []):
            abstract = (doc.get("description") or "").strip()
            if not abstract:
                continue
            year = doc.get("publicationdateyear")
            results.append(normalize_result(
                title=doc.get("title"),
                authors=[str(a) for a in (doc.get("author") or [])][:50],
                abstract=abstract,
                year=int(year) if str(year or "").isdigit() else None,
                source=doc.get("source") or "ERIC",
                doi=None,
                # ERIC full text hosted by ERIC (e_fulltextauth = 1) is free to read; otherwise unknown.
                is_open_access=True if str(doc.get("e_fulltextauth")) == "1" else None,
                database="ERIC",
            ))
        return results
    except Exception as error:
        source_failed("eric", type(error).__name__)
        print("ERIC search failed:", type(error).__name__)
        return []


# ============================================================
# OPENAIRE (European research graph: publications from repositories and EU-funded projects)
# ============================================================

async def search_openaire(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    query = plain_query(q)
    if not query:
        return []
    params = {"search": query, "type": "publication", "pageSize": 10}
    if year_min is not None:
        params["fromPublicationDate"] = f"{year_min}-01-01"
    if year_max is not None:
        params["toPublicationDate"] = f"{year_max}-12-31"
    try:
        response = await client.get("https://api.openaire.eu/graph/v1/researchProducts", params=params, timeout=12.0)
        if response.status_code == 429:
            source_failed("openaire", "rate limited")
            return []
        response.raise_for_status()
        results = []
        for item in (response.json().get("results") or []):
            descriptions = item.get("descriptions") or []
            abstract = re.sub(r"<[^>]+>", " ", descriptions[0] if descriptions else "")
            abstract = re.sub(r"\s+", " ", abstract).strip()
            if not abstract:
                continue
            doi = next((p.get("value") for p in (item.get("pids") or []) if (p.get("scheme") or "").lower() == "doi"), None)
            access = ((item.get("bestAccessRight") or {}).get("label") or "").upper()
            date = str(item.get("publicationDate") or "")
            container = (item.get("container") or {}).get("name") if isinstance(item.get("container"), dict) else None
            results.append(normalize_result(
                title=item.get("mainTitle"),
                authors=[a.get("fullName") for a in (item.get("authors") or []) if a.get("fullName")][:50],
                abstract=abstract,
                year=int(date[:4]) if date[:4].isdigit() else None,
                source=container or item.get("publisher") or "OpenAIRE",
                doi=doi,
                is_open_access=True if access == "OPEN" else (False if access in ("CLOSED", "RESTRICTED") else None),
                database="OpenAIRE",
            ))
        return results
    except Exception as error:
        source_failed("openaire", type(error).__name__)
        print("OpenAIRE search failed:", type(error).__name__)
        return []


# ============================================================
# AJOL (African Journals Online), searched through OpenAlex's index of AJOL-hosted journals
# ============================================================

AJOL_OPENALEX_PUBLISHER = "P4310321518"


async def search_ajol(client, q, year_min=None, year_max=None, oa_only=False, filters=None):
    return await search_openalex(
        client, q, year_min, year_max, oa_only, filters,
        extra_filter=f"primary_location.source.host_organization_lineage:{AJOL_OPENALEX_PUBLISHER}",
        database_label="AJOL", health_name="ajol",
    )


# ============================================================
# UNPAYWALL: a free, legal PDF link for papers that have a DOI
# ============================================================

UNPAYWALL_MAX_PAPERS = 15
UNPAYWALL_ENABLED = os.getenv("UNPAYWALL_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


async def add_free_pdf_links(results: list[dict]) -> None:
    """Adds `pdf_url` (and marks open access) for up to 15 top papers with a DOI. Results are cached for 7 days.
    Skipped when the source is switched off or no contact email is configured (Unpaywall requires one)."""
    email = NCBI_EMAIL or CONTACT_EMAIL
    if not UNPAYWALL_ENABLED or not email or not source_enabled("unpaywall"):
        return
    targets = [p for p in results[:UNPAYWALL_MAX_PAPERS] if p.get("doi") and not p.get("pdf_url")]
    if not targets:
        return
    semaphore = asyncio.Semaphore(5)

    async def one(client, paper):
        doi = normalize_doi(paper["doi"])
        if not doi:
            return False
        cache_key = "upw:" + hashlib.sha256(doi.encode("utf-8")).hexdigest()
        cached = get_from_search_cache(cache_key)
        if cached is None:
            async with semaphore:
                try:
                    from urllib.parse import quote

                    response = await client.get(f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
                                                params={"email": email}, timeout=6.0)
                    if response.status_code == 404:
                        cached = {"pdf": None, "oa": False}
                    else:
                        response.raise_for_status()
                        data = response.json()
                        best = data.get("best_oa_location") or {}
                        cached = {"pdf": best.get("url_for_pdf") or best.get("url"), "oa": bool(data.get("is_oa"))}
                except Exception as error:
                    source_failed("unpaywall", type(error).__name__)
                    return False
            set_in_search_cache(cache_key, cached, ttl=7 * 86400)
        pdf = cached.get("pdf")
        if isinstance(pdf, str) and pdf.startswith("https://") and len(pdf) <= 500:
            paper["pdf_url"] = pdf
        if cached.get("oa"):
            paper["is_open_access"] = True
        return bool(paper.get("pdf_url"))

    async def run_all(client):
        # The Sources tab counts free PDF links found, not papers checked.
        found = await asyncio.gather(*(one(client, p) for p in targets))
        return [hit for hit in found if hit]

    async with httpx.AsyncClient(follow_redirects=True,
                                 headers={"User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{email})"}) as client:
        await tracked("unpaywall", run_all(client))


# ---------------------------------------------------------------- admin: sources tab

class SourceSwitchRequest(BaseModel):
    enabled: bool


@app.get("/admin/sources", tags=["Admin"], include_in_schema=False, summary="Paper sources",
         description="Every source with its on/off switch, key status and health since the last restart.")
def admin_sources(request: Request, authorization: str | None = Header(None)):
    require_admin(request, authorization)
    panel = _load_panel_keys()
    out = []
    for name, meta in SOURCE_REGISTRY.items():
        key_name = meta["key"]
        key_source = None
        if key_name:
            key_source = "panel" if key_name in panel else ("env" if globals().get(key_name) else "none")
        with _SOURCE_HEALTH_LOCK:
            h = dict(_health(name))
        calls = h["calls"]
        out.append({
            "name": name, "label": meta["label"], "enabled": source_enabled(name),
            "key_name": key_name, "key_source": key_source, "key_required": meta["key_required"],
            "calls": calls, "failures": h["failures"], "results": h["results"],
            "avg_ms": int(h["total_ms"] / calls) if calls else None,
            "last_ok_at": h["last_ok_at"], "last_error": h["last_error"], "last_error_at": h["last_error_at"],
            "needs_email": name == "unpaywall" and not (NCBI_EMAIL or CONTACT_EMAIL),
        })
    return {"sources": out}


@app.post("/admin/sources/{name}", tags=["Admin"], include_in_schema=False, summary="Switch a source on or off",
          description="Saved in the database and applied at once. At least one search source must stay on.")
def admin_switch_source(name: str, body: SourceSwitchRequest, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    if name not in SOURCE_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown source.")
    if not body.enabled and name in SEARCH_SOURCES:
        others_on = [s for s in SEARCH_SOURCES if s != name and source_enabled(s)]
        if not others_on:
            raise HTTPException(status_code=400, detail="At least one search source must stay on.")
    db = SessionLocal()
    try:
        row = db.query(SourceSetting).filter(SourceSetting.name == name).first()
        if row is None:
            row = SourceSetting(name=name)
            db.add(row)
        row.enabled = body.enabled
        row.updated_at = _utcnow().replace(tzinfo=None)
        row.updated_by = admin.id
        db.commit()
    finally:
        db.close()
    reset_source_switch_cache()
    audit(admin, "source_on" if body.enabled else "source_off", request, target=name)
    return {"name": name, "enabled": body.enabled}


# ============================================================
# RESEARCH MODE (rules, no AI call): quick, deep or medical
# ============================================================

_MEDICAL_PATTERN = re.compile(
    r"\b(patients?|treatments?|therap(y|ies|eutic)|dos(e|es|age|ing)|drugs?|medications?|medicines?|"
    r"diseases?|disorders?|symptoms?|syndromes?|diagnos(is|ed|tic)|clinical|pregnan(t|cy)|cancers?|tumou?rs?|"
    r"vaccines?|vaccination|infections?|diabet(es|ic)|hypertension|blood pressure|cardiac|heart attack|strokes?|"
    r"asthma|depression|anxiety|insomnia|dementia|alzheimer'?s?|surgery|surgical|side effects?|adverse effects?|"
    r"antibiotics?|supplements?|vitamins?|mortality|prognosis|chemotherapy|hiv|tuberculosis|malaria|covid(-19)?|"
    r"placebo|nursing|hospitali[sz]ation|obesity|melatonin|ibuprofen|paracetamol|metformin|statins?)\b",
    re.IGNORECASE,
)
_DEEP_PATTERN = re.compile(
    r"\b(compare|comparison|comparing|versus|vs\.?|across|state of (the )?(research|art|evidence)|"
    r"what is known|what do we know|literature review|systematic|review of|overview of|landscape|trends?|"
    r"pros and cons|advantages and disadvantages|differences? between|similarities|debate|controvers(y|ial)|"
    r"determinants|implications|over time|evolution of)\b",
    re.IGNORECASE,
)


def detect_research_mode(question: str) -> str:
    """Pick quick, deep or medical from the wording alone. Medical wins over deep (it adds the safety note)."""
    text = (question or "").strip()
    if _MEDICAL_PATTERN.search(text):
        return "medical"
    words = len(text.split())
    parts = text.count("?") + len(re.findall(r"\band\b", text, re.IGNORECASE))
    if _DEEP_PATTERN.search(text) or words >= 25 or (words >= 14 and parts >= 2):
        return "deep"
    return "quick"


# Medical ranking: stronger study designs move up, within relevance.
_EVIDENCE_BONUS = {"meta_analysis": 12, "systematic_review": 10, "rct": 8, "clinical_trial": 4, "review": 2}


def evidence_bonus(paper: dict) -> int:
    return max([_EVIDENCE_BONUS.get(t, 0) for t in (paper.get("study_types") or [])] or [0])


def rank_by_evidence(results: list[dict]) -> list[dict]:
    """Relevance score plus a bonus for meta-analyses, systematic reviews and RCTs. Without scores (re-ranking off)
    the order is a stable sort by evidence level."""
    if any(isinstance(p.get("similarity_score"), (int, float)) for p in results):
        return sorted(results, key=lambda p: (p.get("similarity_score") or 0) + evidence_bonus(p), reverse=True)
    return sorted(results, key=evidence_bonus, reverse=True)


@app.get(
    "/mode",
    tags=["Search"],
    summary="Suggest a research mode",
    description="Returns `quick`, `deep` or `medical` for a question, from its wording (no AI call). Used by Auto mode.",
    responses={**_ok({"mode": "medical"}), **E_RATE},
)
async def suggest_mode(q: str = Query(..., max_length=2000)):
    return {"mode": detect_research_mode(q)}


# ============================================================
# SEARCH ENDPOINT
# ============================================================

@app.get(
    "/search",
    tags=["Search"],
    summary="Search eleven academic databases",
    description="Searches OpenAlex, Semantic Scholar, arXiv, Crossref, CORE (CORE needs a server-side key), PubMed, Europe PMC, "
                "DOAJ, ERIC, OpenAIRE and AJOL (sources an admin switched off are skipped), adds free PDF links from Unpaywall, "
                "merges duplicates by DOI or title, and ranks by semantic similarity when the embedding model is enabled. "
                "Open to guests.\n\n"
                "`year_min` / `year_max` restrict the publication year (inclusive). `open_access_only=true` returns only papers a "
                "source confirmed as open access (`is_open_access: true`). `min_citations`, `exclude_preprints`, `study_type` "
                "(" + ", ".join(STUDY_TYPES) + ") and `field` narrow the results further; a paper whose value is unknown is "
                "left out by that filter. `sort` is `relevance` (default), `newest` or `most_cited`.\n\n"
                "`medical=true` ranks meta-analyses, systematic reviews and RCTs higher within relevance. "
                "`search_by` is `topic` (default), `title` or `author`; the prefixes `author:`, `title:` and `doi:` in `q` do the "
                "same. A query that is only a DOI is looked up directly. Papers without an abstract may be returned with "
                "`abstract: null`; they are never used as evidence by `/ask`.",
    responses={
        **_ok({"query": "sleep and memory", "count": 1, "search_mode": "topic", "source_counts": {"OpenAlex": 1},
               "results": [EXAMPLE_PAPER]}),
        **_err(400, "Empty query or unknown filter value.", "Query cannot be empty."),
        **E_RATE,
    },
)
async def search(
    q: str = Query(..., max_length=300),
    year_min: int | None = Query(default=None, ge=0, le=3000),
    year_max: int | None = Query(default=None, ge=0, le=3000),
    open_access_only: bool = False,
    min_citations: int | None = Query(default=None, ge=0, le=1_000_000),
    exclude_preprints: bool = False,
    study_type: str | None = Query(default=None, max_length=40),
    field: str | None = Query(default=None, max_length=60),
    sort: str = Query(default="relevance", pattern="^(relevance|newest|most_cited)$"),
    search_by: str = Query(default="topic", pattern="^(topic|title|author)$"),
    medical: bool = False,
):

    if not q.strip():

        raise HTTPException(
            status_code=400,
            detail=(
                "Search query cannot be empty."
            ),
        )

    if study_type is not None and study_type not in STUDY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown study type.")
    if field is not None and field not in FIELDS_OF_STUDY:
        raise HTTPException(status_code=400, detail="Unknown field of study.")

    query = q.strip()

    # Work out what the user typed: a DOI, an author, a title or a topic.
    mode, term = search_by, query
    prefix = re.match(r"^(author|title|doi)\s*:\s*(.+)$", query, re.IGNORECASE)
    if prefix and prefix.group(1).lower() in ("author", "title"):
        mode, term = prefix.group(1).lower(), prefix.group(2).strip()
    doi = extract_doi_query(query)
    if doi:
        mode, term = "doi", doi
    elif prefix and prefix.group(1).lower() == "doi":
        raise HTTPException(status_code=400, detail="That does not look like a valid DOI.")
    if not term:
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    filters = {
        "min_citations": min_citations or None,
        "exclude_preprints": exclude_preprints,
        "study_type": study_type,
        "field": field,
    }
    cache_key = json.dumps(
        [mode, term.lower(), year_min, year_max, open_access_only, min_citations, exclude_preprints,
         study_type, field, sort, medical],
        separators=(",", ":"),
    )

    cached_response = get_from_search_cache(cache_key)
    if cached_response is not None:
        return cached_response

    timeout = httpx.Timeout(
        30.0,
        connect=10.0,
    )

    try:

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{CONTACT_EMAIL})"
            },
        ) as client:

            # Filters are sent to each source so its result slots go to matching papers.
            f = {"year_min": year_min, "year_max": year_max, "oa_only": open_access_only, "filters": filters}

            def add(name, make):
                # Only sources an admin has not switched off; each call is timed for the Sources tab.
                if source_enabled(name):
                    tasks.append(tracked(name, make()))

            tasks = []
            if mode == "doi":
                tasks = [lookup_doi(client, term)]
            elif mode == "author":
                add("openalex", lambda: search_openalex_by_author(client, term, **f))
            elif mode == "title":
                add("openalex", lambda: search_openalex(client, term, mode="title", **f))
                add("semantic_scholar", lambda: search_semantic_scholar(client, term, **f))
            else:
                academic_keyword = clean_academic_query(term)
                add("openalex", lambda: search_openalex(client, academic_keyword, **f))
                add("semantic_scholar", lambda: search_semantic_scholar(client, academic_keyword, **f))
                add("arxiv", lambda: search_arxiv(client, academic_keyword, **f))
                add("core", lambda: search_core(client, academic_keyword, **f))
                add("europepmc", lambda: search_europepmc(client, academic_keyword, **f))
                add("doaj", lambda: search_doaj(client, academic_keyword, **f))
                add("openaire", lambda: search_openaire(client, academic_keyword, **f))
                add("ajol", lambda: search_ajol(client, academic_keyword, **f))
                if not open_access_only:
                    # Crossref, PubMed and ERIC rarely say whether a paper is open access, so they are skipped
                    # for OA-only searches.
                    add("crossref", lambda: search_crossref(client, academic_keyword, **f))
                    add("pubmed", lambda: search_pubmed(client, academic_keyword, **f))
                    add("eric", lambda: search_eric(client, academic_keyword, **f))

                # If user query has distinct keywords, also search raw query
                if academic_keyword != term:
                    add("semantic_scholar", lambda: search_semantic_scholar(client, term, **f))
                    add("openalex", lambda: search_openalex(client, term, **f))
                    add("arxiv", lambda: search_arxiv(client, term, **f))

            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            all_results = []
            for item in gathered:
                if isinstance(item, list):
                    all_results.extend(item)

    except Exception as error:
        print("Research search failed:", error)  # details stay in the server log
        raise HTTPException(
            status_code=500,
            detail="Research search failed. Please try again.",
        )

    results = deduplicate_results(
        all_results
    )

    # --------------------------------------------------------
    # Filters (a direct DOI lookup is returned as found)
    # --------------------------------------------------------
    if mode != "doi":
        results = apply_result_filters(results, year_min, year_max, open_access_only, filters)

    # --------------------------------------------------------
    # Semantic Re-Ranking (FastEmbed Local Embeddings)
    # --------------------------------------------------------
    if mode in ("topic", "title"):
        results = rerank_papers_by_similarity(
            term,
            results,
        )
    else:
        # Author results keep OpenAlex's most-cited order; a DOI lookup has one paper.
        for result in results:
            result.setdefault("similarity_score", None)

    if medical and sort == "relevance" and mode in ("topic", "title"):
        results = rank_by_evidence(results)
    results = sort_results(results, sort)
    await add_free_pdf_links(results)

    # Papers per database (not per journal).
    source_counts = {}
    for result in results:
        database = result.get("database") or result.get("description") or "Unknown source"
        source_counts[database] = source_counts.get(database, 0) + 1

    response_data = {
        "query": query,
        "search_mode": mode,
        "count": len(results),
        "source_counts": source_counts,
        "results": results,
    }

    # Store in memory cache
    set_in_search_cache(cache_key, response_data)

    return response_data


# ============================================================
# SAVE SEARCH HISTORY
# ============================================================

def save_search_history(
    session_id,
    question,
    answer,
    citations=None,
    user_id=None,
    papers=None,
):
    db = SessionLocal()
    try:
        db.add(
            SearchHistory(
                session_id=session_id,
                user_id=user_id,
                question=question,
                answer=answer,
                citations=citations or [],
                papers=papers or [],
            )
        )
        db.commit()
        print("Search history saved.")
    except Exception as error:
        db.rollback()
        print(
            "PostgreSQL history save failed: "
            f"{error}"
        )
    finally:
        db.close()


# ============================================================
# GET SEARCH HISTORY (SECURE & SCOPED)
# ============================================================

@app.get(
    "/history/{session_id}",
    tags=["History"],
    summary="List past questions and answers",
    description="**Signed in:** returns all your history (the `session_id` in the path is ignored). **Guest:** returns only the "
                "entries filed under that `session_id`. A guest can never read an account's history.",
    responses={**_ok({"history": [{"id": 12, "session_id": "3f9c...", "user_id": None, "question": "Does sleep affect memory?",
                                   "answer": "Yes [1]...", "citations": [{"paper_id": 1, "title": "..."}], "papers": [EXAMPLE_PAPER],
                                   "created_at": "2026-10-14T09:30:00"}]})},
)
def get_search_history(
    session_id: str,
    request: Request,
    authorization: str | None = Header(None),
):
    """Signed-in users get their own history (the path session_id is ignored for them).
    Guests get only the unclaimed entries of the session_id they present."""
    verified_user_id = optional_user_id(request, authorization)

    db = SessionLocal()
    try:
        if verified_user_id:
            history = (
                db.query(SearchHistory)
                .filter(SearchHistory.user_id == verified_user_id)
                .order_by(SearchHistory.created_at.desc())
                .all()
            )
        else:
            if not SESSION_ID_RE.match(session_id):
                return {"history": []}
            history = (
                db.query(SearchHistory)
                .filter(SearchHistory.session_id == session_id, SearchHistory.user_id.is_(None))
                .order_by(SearchHistory.created_at.desc())
                .all()
            )

        return {
            "history": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "user_id": item.user_id,
                    "question": item.question,
                    "answer": item.answer,
                    "citations": item.citations or [],
                    "papers": item.papers or [],
                    "created_at": item.created_at,
                }
                for item in history
            ]
        }
    except Exception as error:
        print("Search history retrieval error:", error)
        raise HTTPException(status_code=500, detail="Could not retrieve search history.")
    finally:
        db.close()


@app.delete(
    "/history/entry/{entry_id}",
    tags=["History"],
    summary="Delete one history entry",
    description="Signed in: deletes one of your entries. Guest: pass the entry's `session_id` as a query parameter.",
    responses={**_ok({"message": "History entry deleted."}), **_err(404, "Not found, or not yours.", "History entry not found.")},
)
def delete_history_entry(
    entry_id: int,
    request: Request,
    session_id: str = Query(default="", max_length=255),
    authorization: str | None = Header(None),
):
    """Delete ONE history entry. Signed-in users can only delete their own; guests must present the
    session_id the entry belongs to."""
    verified_user_id = optional_user_id(request, authorization)
    db = SessionLocal()
    try:
        query = db.query(SearchHistory).filter(SearchHistory.id == entry_id)
        if verified_user_id:
            query = query.filter(SearchHistory.user_id == verified_user_id)
        else:
            if not SESSION_ID_RE.match(session_id):
                raise HTTPException(status_code=404, detail="History entry not found.")
            query = query.filter(SearchHistory.session_id == session_id, SearchHistory.user_id.is_(None))
        deleted = query.delete(synchronize_session=False)
        db.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="History entry not found.")
        return {"message": "History entry deleted."}
    except HTTPException:
        raise
    except Exception as error:
        db.rollback()
        print("Search history entry deletion error:", error)
        raise HTTPException(status_code=500, detail="Could not delete history entry.")
    finally:
        db.close()


@app.delete(
    "/history/{session_id}",
    tags=["History"],
    summary="Clear history",
    description="Signed in: clears **all** your history. Guest: clears that `session_id`'s unclaimed entries.",
    responses=_ok({"message": "Search history deleted successfully."}),
)
def delete_search_history(
    session_id: str,
    request: Request,
    authorization: str | None = Header(None),
):
    """Clear history: everything for a signed-in user, or the guest session's unclaimed entries."""
    verified_user_id = optional_user_id(request, authorization)

    db = SessionLocal()
    try:
        if verified_user_id:
            db.query(SearchHistory).filter(SearchHistory.user_id == verified_user_id).delete(synchronize_session=False)
        else:
            if not SESSION_ID_RE.match(session_id):
                return {"message": "Nothing to delete."}
            db.query(SearchHistory).filter(
                SearchHistory.session_id == session_id,
                SearchHistory.user_id.is_(None)
            ).delete(synchronize_session=False)
        db.commit()
        return {"message": "Search history deleted successfully."}
    except Exception as error:
        db.rollback()
        print("Search history deletion error:", error)
        raise HTTPException(status_code=500, detail="Could not delete search history.")
    finally:
        db.close()


# ============================================================
# AI PROVIDER LAYER (shared by /ask and /deep-dive)
# ============================================================

def get_ai_clients():
    """Pick the provider: Gemini first, Groq as fallback. 503 (generic text) if neither is configured."""
    gemini_client = get_gemini_client()
    groq_client = get_groq_client()
    if not gemini_client and not groq_client:
        print(f"No AI API key configured. Set GEMINI_API_KEY or GROQ_API_KEY in {ENV_FILE}")  # server log only
        raise HTTPException(
            status_code=503,
            detail="The AI service is temporarily unavailable. Please try again later.",
        )
    return gemini_client, groq_client


def parse_ai_json(content: str | None, required_key: str) -> dict:
    if not content:
        raise ValueError("AI model returned an empty response.")
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    result = json.loads(content.strip())
    if not isinstance(result, dict):
        raise ValueError(f"AI response is missing '{required_key}'.")
    value = result.get(required_key)
    if isinstance(value, str) and value.strip():
        return result
    if isinstance(value, list) and value:
        return result
    raise ValueError(f"AI response is missing '{required_key}'.")


def gemini_safe_schema(schema):
    """Gemini's response_schema rejects JSON-Schema's `additionalProperties` (the SDK sends it as
    `additional_properties`, which the API answers with 400 INVALID_ARGUMENT). Groq's strict mode
    requires it, so the shared schemas keep it and the Gemini copy has it removed, recursively."""
    if isinstance(schema, dict):
        return {k: gemini_safe_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [gemini_safe_schema(v) for v in schema]
    return schema


def generate_structured_ai(
    gemini_client,
    groq_client,
    system_prompt: str,
    user_prompt: str,
    retry_prompt: str,
    response_schema: dict,
    required_key: str,
) -> dict:
    """Call the provider; on ANY failure (rate limit, json_validate_failed, truncated or invalid JSON)
    retry exactly once with the shorter prompt. Raises the last error if both attempts fail."""

    def call_gemini(prompt_text: str):
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=f"{system_prompt}\n\n{prompt_text}")],
                )
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=gemini_safe_schema(response_schema),
                temperature=0.2,
                max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            ),
        )
        return response.text

    def call_groq(prompt_text: str):
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_answer",
                    "strict": True,
                    "schema": response_schema,
                },
            },
            temperature=0.2,
            max_completion_tokens=AI_MAX_OUTPUT_TOKENS,
            reasoning_effort="low",
        )
        return completion.choices[0].message.content

    def attempt(call):
        try:
            return parse_ai_json(call(user_prompt), required_key)
        except Exception as first_error:
            text = str(first_error)
            if "429" in text or "RESOURCE_EXHAUSTED" in text or "rate_limit" in text.lower():
                raise  # quota/rate limit: retrying only burns more quota, fail over now
            print("AI first attempt failed, retrying with a shorter prompt:", first_error)
            return parse_ai_json(call(retry_prompt), required_key)

    if gemini_client:
        try:
            return attempt(call_gemini)
        except Exception as gemini_error:
            if not groq_client:
                raise
            print("Gemini failed, falling back to Groq:", gemini_error)
    return attempt(call_groq)


# ============================================================
# AI RESEARCH QUESTION (MULTI-TURN / FOLLOW-UP CONTEXT)
# ============================================================

ASK_SYSTEM_PROMPT = (
    "You are an expert AI research scientist synthesizing academic papers. "
    "Answer ONLY from the supplied papers. If they do not contain enough information to answer, say so plainly; "
    "never invent findings, statistics, papers or citations. "
    "Cite papers using [1], [2], etc. matching their paper_id. "
    "Also give every supplied paper a one-sentence takeaway (at most 30 words) stating its main finding from its "
    "abstract only; if the abstract states no finding, the takeaway is exactly 'Not stated in the abstract.' "
    "If the question can be answered yes or no, set meter.is_yes_no to true and label EVERY paper 'yes' (its findings "
    "support a yes), 'no' (they support a no) or 'possibly' (mixed, partial or unclear); otherwise set "
    "meter.is_yes_no to false and meter.labels to an empty list. "
    "Return a valid JSON object with 'answer' (string in markdown), 'citations' (list of {paper_id, title}), "
    "'takeaways' (list of {paper_id, takeaway}) and 'meter' ({is_yes_no, labels: list of {paper_id, label}})."
)

MEDICAL_PROMPT_ADDENDUM = (
    " This is a health or medical question. Prefer evidence from meta-analyses, systematic reviews and randomised "
    "controlled trials, and say what kind of study each key finding comes from (study_types, when given, tells you). "
    "Point out when evidence is weak, small, old or only from animals or lab studies. Never give personal medical "
    "advice, doses or treatment instructions for an individual."
)

MEDICAL_DISCLAIMER = (
    "\n\n> **Not medical advice.** This summary of research papers is for information only. "
    "Talk to a qualified health professional about your own situation."
)

METER_LABELS = ("yes", "no", "possibly")
METER_MIN_PAPERS = 3

METER_SCHEMA = {
    "type": "object",
    "properties": {
        "is_yes_no": {"type": "boolean"},
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "label": {"type": "string", "enum": list(METER_LABELS)},
                },
                "required": ["paper_id", "label"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["is_yes_no", "labels"],
    "additionalProperties": False,
}


def clean_meter(raw, valid_ids) -> dict | None:
    """Counts of yes / no / possibly over known papers, one label each. None when the question is not yes/no or
    fewer than METER_MIN_PAPERS papers were labelled."""
    if not isinstance(raw, dict) or raw.get("is_yes_no") is not True:
        return None
    labels, seen = [], set()
    for item in raw.get("labels") or []:
        if not isinstance(item, dict):
            continue
        pid, label = item.get("paper_id"), str(item.get("label") or "").strip().lower()
        if pid in valid_ids and pid not in seen and label in METER_LABELS:
            seen.add(pid)
            labels.append({"paper_id": pid, "label": label})
    if len(labels) < METER_MIN_PAPERS:
        return None
    counts = {name: sum(1 for x in labels if x["label"] == name) for name in METER_LABELS}
    return {**counts, "total": len(labels), "labels": sorted(labels, key=lambda x: x["paper_id"])}

TAKEAWAY_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "paper_id": {"type": "integer"},
        "takeaway": {"type": "string"},
    },
    "required": ["paper_id", "takeaway"],
    "additionalProperties": False,
}

ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["paper_id", "title"],
                "additionalProperties": False,
            },
        },
        "takeaways": {"type": "array", "items": TAKEAWAY_ITEM_SCHEMA},
        "meter": METER_SCHEMA,
    },
    "required": ["answer", "citations", "takeaways", "meter"],
    "additionalProperties": False,
}

MAX_TAKEAWAY_CHARS = 300


def clean_takeaways(raw, valid_ids) -> list[dict]:
    """Keep one takeaway per known paper_id, trimmed. Anything the model invents is dropped."""
    out, seen = [], set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        pid, text = item.get("paper_id"), item.get("takeaway")
        if pid in valid_ids and pid not in seen and isinstance(text, str) and text.strip():
            seen.add(pid)
            out.append({"paper_id": pid, "takeaway": text.strip()[:MAX_TAKEAWAY_CHARS]})
    return out


def sanitize_paper_for_storage(paper: dict) -> dict:
    """Keep only the fields the UI needs, with bounded sizes, before persisting client-supplied papers."""
    authors = paper.get("authors")
    year = paper.get("publication_year")
    score = paper.get("similarity_score")
    cited = paper.get("cited_by_count")
    study_types = paper.get("study_types")
    return {
        "title": str(paper.get("title") or "Untitled")[:500],
        "authors": [str(a)[:200] for a in authors[:50]] if isinstance(authors, list) else [],
        "description": str(paper.get("description") or "")[:200],
        "publication_year": year if isinstance(year, int) else None,
        "doi": str(paper["doi"])[:255] if paper.get("doi") else None,
        "abstract": str(paper["abstract"])[:5000] if paper.get("abstract") else None,
        "similarity_score": score if isinstance(score, (int, float)) else None,
        "database": str(paper.get("database") or "")[:50] or None,
        "cited_by_count": cited if isinstance(cited, int) and not isinstance(cited, bool) and cited >= 0 else None,
        "study_types": [t for t in STUDY_TYPES if t in study_types] if isinstance(study_types, list) else [],
        "is_preprint": paper["is_preprint"] if isinstance(paper.get("is_preprint"), bool) else None,
        "is_retracted": paper.get("is_retracted") is True,
        "field": paper["field"] if paper.get("field") in FIELDS_OF_STUDY else None,
        "pdf_url": paper["pdf_url"] if isinstance(paper.get("pdf_url"), str) and paper["pdf_url"].startswith("https://")
        and len(paper["pdf_url"]) <= 500 else None,
    }


@app.post(
    "/ask",
    tags=["AI"],
    summary="Ask a question about papers",
    description="Send the question and the papers from `/search` (up to 50; the first 6 with an abstract are used). The answer is written "
                "**only from those abstracts** and cites them as `[1]`, `[2]`... by `paper_id` (their position in `results`, starting at 1). "
                "Citations the model invents are dropped. `takeaways` gives a one-sentence finding for each paper used. For yes/no questions `meter` counts papers labelled yes, no or possibly (null otherwise, or when fewer than 3 papers were labelled). `mode: medical` adds evidence-level guidance and a not-medical-advice note. Include earlier turns in `conversation_history` for follow-ups. "
                "Identity comes from the cookie only: a `user_id` in the body is ignored. Signed-in answers are saved to your account, "
                "guest answers to `session_id`. If the first AI attempt fails it is retried once with fewer papers.",
    responses={
        **_ok({"answer": "One night of sleep loss reduces next-day recall [1].", "citations": [{"paper_id": 1, "title": EXAMPLE_PAPER["title"]}],
               "takeaways": [{"paper_id": 1, "takeaway": "One night of sleep loss cut next-day recall by 20%."}]}),
        **_err(400, "Empty question, or none of the papers has an abstract to base an answer on.",
               "None of these papers include an abstract, so there is nothing to base an answer on. Try a different search."),
        **E_RATE, **E_AI,
    },
)
async def ask(
    request: AskRequest,
    req: Request,
    authorization: str | None = Header(None),
):
    """Guest-friendly: identity is read from the signed cookie only. Signed-in users' answers are
    saved to their account; guests' to their session_id."""
    verified_user_id = optional_user_id(req, authorization)

    gemini_client, groq_client = get_ai_clients()

    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if not request.session_id.strip():
        raise HTTPException(status_code=400, detail="Session ID cannot be empty.")

    papers = []
    for index, result in enumerate(request.results, start=1):
        abstract = result.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip():
            continue  # no abstract = no evidence to ground an answer in
        if result.get("is_retracted") is True:
            continue  # a retracted paper is never used as evidence (it still shows in the results, flagged)
        clean_abstract = abstract.strip()
        # Truncate to stay safely within token limits (e.g. Groq TPM)
        if len(clean_abstract) > 400:
            clean_abstract = clean_abstract[:400] + "..."
        authors = result.get("authors")
        papers.append(
            {
                "paper_id": index,
                "title": str(result.get("title") or "Untitled")[:300],
                "authors": [str(a)[:100] for a in authors[:3]] if isinstance(authors, list) else [],
                "publication_year": result.get("publication_year") if isinstance(result.get("publication_year"), int) else None,
                "source": str(result.get("description") or "Academic Database")[:100],
                "abstract": clean_abstract,
                **({"study_types": [t for t in result["study_types"] if t in STUDY_TYPES][:6]}
                   if isinstance(result.get("study_types"), list) and result["study_types"] else {}),
            }
        )
        if len(papers) >= 6:
            break

    if not papers:
        raise HTTPException(
            status_code=400,
            detail="None of these papers include an abstract, so there is nothing to base an answer on. Try a different search.",
        )

    stored_papers = [sanitize_paper_for_storage(r) for r in request.results[:50]]

    ask_cache_key = generate_ask_cache_key(
        request.question,
        papers,
        [turn.model_dump() for turn in request.conversation_history],
        request.mode,
    )
    cached_ask_response = get_from_search_cache(ask_cache_key)
    if cached_ask_response is not None:
        save_search_history(
            request.session_id,
            request.question,
            cached_ask_response["answer"],
            citations=cached_ask_response.get("citations", []),
            user_id=verified_user_id,
            papers=stored_papers,
        )
        return cached_ask_response

    enforce_ai_quota(req, verified_user_id)  # only real provider calls count; cache hits above are free

    user_prompt = f"Question: {request.question}\n\nPapers:\n{json.dumps(papers, indent=2)}"
    if request.conversation_history:
        history_str = "\n".join(
            [f"Q: {t.question[:150]}\nA: {t.answer[:250]}..." for t in request.conversation_history[-2:]]
        )
        user_prompt = f"Previous Conversation:\n{history_str}\n\nCurrent Question: {request.question}\n\nPapers:\n{json.dumps(papers, indent=2)}"

    # Retry prompt: fewer papers, shorter abstracts, shorter answer.
    short_papers = [{**p, "abstract": (p.get("abstract") or "")[:200]} for p in papers[:3]]
    retry_prompt = (
        f"Question: {request.question}\n\nPapers:\n{json.dumps(short_papers)}\n\n"
        "Keep the answer under 150 words."
    )

    try:
        result = await asyncio.to_thread(
            generate_structured_ai,
            gemini_client,
            groq_client,
            ASK_SYSTEM_PROMPT + (MEDICAL_PROMPT_ADDENDUM if request.mode == "medical" else ""),
            user_prompt,
            retry_prompt,
            ASK_SCHEMA,
            "answer",
        )

        valid_titles = {p["paper_id"]: p["title"] for p in papers}
        valid_citations = []
        for citation in result.get("citations") or []:
            pid = citation.get("paper_id") if isinstance(citation, dict) else None
            if pid in valid_titles:
                valid_citations.append({"paper_id": pid, "title": valid_titles[pid]})
        takeaways = clean_takeaways(result.get("takeaways"), set(valid_titles))
        answer = result["answer"].rstrip()
        if request.mode == "medical":
            answer += MEDICAL_DISCLAIMER
        result = {
            "answer": answer,
            "citations": valid_citations,
            "takeaways": takeaways,
            "meter": clean_meter(result.get("meter"), set(valid_titles)),
        }
        for item in takeaways:
            # Reused by POST /takeaways, so these papers are never charged again.
            paper = next(p for p in papers if p["paper_id"] == item["paper_id"])
            set_in_search_cache(takeaway_cache_key(paper["title"], paper["abstract"]), {"t": item["takeaway"]}, ttl=86400)

        save_search_history(
            request.session_id,
            request.question,
            result["answer"],
            citations=result["citations"],
            user_id=verified_user_id,
            papers=stored_papers,
        )

        set_in_search_cache(ask_cache_key, result, ttl=7200)
        return result

    except json.JSONDecodeError as error:
        print("AI returned invalid JSON:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)
    except Exception as error:
        print("AI request failed:", error)  # the real error stays in the server logs only
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)


# ============================================================
# DEEP RESEARCH (plan 3 to 5 searches, run them, write a structured review)
# ============================================================

DEEP_MAX_RESULTS = 50
DEEP_MAX_EVIDENCE = 15
DEEP_SEARCH_CONCURRENCY = 2  # gentle on the source APIs (PubMed allows 3 requests/second without a key)

DEEP_PLAN_PROMPT = (
    "You plan academic literature searches. Split the research question into 3 to 5 short, distinct search queries "
    "(keywords, at most 12 words each) that together cover its parts. Return a valid JSON object with 'queries' "
    "(list of strings)."
)

DEEP_PLAN_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
    "additionalProperties": False,
}

DEEP_SYSTEM_PROMPT = (
    "You are an expert research scientist writing a short structured literature review. Use ONLY the supplied papers; "
    "never invent findings, statistics, papers or citations, and say plainly where the papers do not answer the question. "
    "Cite papers as [1], [2] matching their paper_id. Write the 'answer' in markdown with exactly these sections: "
    "'## Overview' (2 to 4 sentences), '## Key findings' (bullets with citations), '## Evidence table' (a markdown table "
    "with columns Paper, Study type, Population, Main finding; one row per cited paper, Paper written as [n]; write "
    "'Not stated' when the abstract does not say), '## Conflicting findings' (or say none were found), and "
    "'## Research gaps' (what the papers do not cover). "
    "Also give every supplied paper a one-sentence takeaway (at most 30 words) from its abstract only; if the abstract "
    "states no finding, the takeaway is exactly 'Not stated in the abstract.' "
    "If the question can be answered yes or no, set meter.is_yes_no to true and label EVERY paper 'yes', 'no' or "
    "'possibly'; otherwise set meter.is_yes_no to false and meter.labels to an empty list. "
    "Return a valid JSON object with 'answer', 'citations' (list of {paper_id, title}), 'takeaways' (list of "
    "{paper_id, takeaway}) and 'meter' ({is_yes_no, labels: list of {paper_id, label}})."
)


def clean_search_queries(raw, limit: int = 5) -> list[str]:
    out, seen = [], set()
    for item in raw or []:
        if not isinstance(item, str):
            continue
        text = re.sub(r"\s+", " ", item).strip(" \"'?.")[:200]
        if len(text) >= 3 and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
        if len(out) >= limit:
            break
    return out


@app.post(
    "/deep-research",
    tags=["AI"],
    summary="Deep research: several searches and a structured review",
    description="The AI splits the question into 3 to 5 searches; the server runs them across all sources (with the filters given), "
                "merges and re-ranks the papers, and writes a review from the top 15 abstracts with Overview, Key findings, an "
                "Evidence table, Conflicting findings and Research gaps. Returns the merged `results` (up to 50; `paper_id` = "
                "position) plus `answer`, `citations`, `takeaways`, `meter` and `sub_queries`. Uses 2 AI calls and counts toward "
                "the daily AI caps and a separate, smaller deep research cap. Medical questions (detected, or `medical: true`) "
                "rank stronger study designs first and end with a not-medical-advice note. Cached for 2 hours.",
    responses={
        **_ok({"mode": "deep", "medical": False, "sub_queries": ["remote work productivity"], "count": 1,
               "papers_used": 1, "source_counts": {"OpenAlex": 1}, "results": [EXAMPLE_PAPER],
               "answer": "## Overview\n...", "citations": [{"paper_id": 1, "title": EXAMPLE_PAPER["title"]}],
               "takeaways": [], "meter": None}),
        **_err(400, "Empty question, unknown filter value, or no papers with an abstract were found.",
               "No papers with an abstract were found for this question. Try different wording or fewer filters."),
        **E_RATE, **E_AI,
    },
)
async def deep_research(request: DeepResearchRequest, req: Request, authorization: str | None = Header(None)):
    user_id = optional_user_id(req, authorization)
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if request.study_type is not None and request.study_type not in STUDY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown study type.")
    if request.field is not None and request.field not in FIELDS_OF_STUDY:
        raise HTTPException(status_code=400, detail="Unknown field of study.")
    medical = request.medical if request.medical is not None else detect_research_mode(question) == "medical"

    filters = {
        "year_min": request.year_min, "year_max": request.year_max, "open_access_only": request.open_access_only,
        "min_citations": request.min_citations, "exclude_preprints": request.exclude_preprints,
        "study_type": request.study_type, "field": request.field,
    }
    cache_key = "deepr:" + hashlib.sha256(
        json.dumps([question.lower(), medical, filters], sort_keys=True).encode("utf-8")).hexdigest()
    cached = get_from_search_cache(cache_key)
    if cached is not None:
        save_search_history(request.session_id, question, cached["answer"], citations=cached.get("citations", []),
                            user_id=user_id, papers=[sanitize_paper_for_storage(r) for r in cached.get("results", [])])
        return cached

    gemini_client, groq_client = get_ai_clients()
    enforce_deep_quota(req, user_id)

    # 1. Plan the searches (if planning fails, search the question itself).
    enforce_ai_quota(req, user_id)
    queries = []
    try:
        plan = await asyncio.to_thread(
            generate_structured_ai, gemini_client, groq_client, DEEP_PLAN_PROMPT,
            f"Research question: {question}", f"Research question: {question[:300]}", DEEP_PLAN_SCHEMA, "queries",
        )
        queries = clean_search_queries(plan.get("queries"))
    except Exception as error:
        print("Deep research planning failed, searching the question directly:", error)
    if not queries:
        queries = [clean_academic_query(question)[:300]]

    # 2. Run the searches (the same code as GET /search, so filters and caching behave the same).
    semaphore = asyncio.Semaphore(DEEP_SEARCH_CONCURRENCY)

    async def run_one(sub_query: str) -> dict:
        async with semaphore:
            try:
                return await search(
                    q=sub_query[:300], year_min=request.year_min, year_max=request.year_max,
                    open_access_only=request.open_access_only, min_citations=request.min_citations,
                    exclude_preprints=request.exclude_preprints, study_type=request.study_type, field=request.field,
                    sort="relevance", search_by="topic", medical=medical,
                )
            except HTTPException:
                return {"results": []}

    responses = await asyncio.gather(*(run_one(q) for q in queries))
    # Copies, so merging never changes papers held in the search cache.
    merged = deduplicate_results([dict(p) for r in responses for p in (r.get("results") or [])])
    merged = rerank_papers_by_similarity(question, merged)
    if medical:
        merged = rank_by_evidence(merged)
    results = merged[:DEEP_MAX_RESULTS]

    evidence = []
    for index, paper in enumerate(results, start=1):
        abstract = paper.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip() or paper.get("is_retracted") is True:
            continue
        evidence.append({
            "paper_id": index,
            "title": str(paper.get("title") or "Untitled")[:300],
            "authors": [str(a)[:100] for a in (paper.get("authors") or [])[:3]],
            "publication_year": paper.get("publication_year") if isinstance(paper.get("publication_year"), int) else None,
            "source": str(paper.get("description") or "Academic Database")[:100],
            **({"study_types": paper["study_types"][:6]} if paper.get("study_types") else {}),
            "abstract": abstract.strip()[:500],
        })
        if len(evidence) >= DEEP_MAX_EVIDENCE:
            break
    if not evidence:
        raise HTTPException(status_code=400, detail="No papers with an abstract were found for this question. "
                                                    "Try different wording or fewer filters.")

    # 3. Write the structured review.
    enforce_ai_quota(req, user_id)
    system_prompt = DEEP_SYSTEM_PROMPT + (MEDICAL_PROMPT_ADDENDUM if medical else "")
    user_prompt = f"Question: {question}\n\nPapers:\n{json.dumps(evidence, indent=2)}"
    retry_prompt = (f"Question: {question}\n\nPapers:\n"
                    + json.dumps([{**p, "abstract": p["abstract"][:250]} for p in evidence[:8]])
                    + "\n\nKeep the answer under 300 words.")
    try:
        result = await asyncio.to_thread(
            generate_structured_ai, gemini_client, groq_client, system_prompt, user_prompt, retry_prompt,
            ASK_SCHEMA, "answer",
        )
    except Exception as error:
        print("Deep research synthesis failed:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)

    valid_titles = {p["paper_id"]: p["title"] for p in evidence}
    citations, seen = [], set()
    for citation in result.get("citations") or []:
        pid = citation.get("paper_id") if isinstance(citation, dict) else None
        if pid in valid_titles and pid not in seen:
            seen.add(pid)
            citations.append({"paper_id": pid, "title": valid_titles[pid]})
    takeaways = clean_takeaways(result.get("takeaways"), set(valid_titles))
    for item in takeaways:
        paper = next(p for p in evidence if p["paper_id"] == item["paper_id"])
        set_in_search_cache(takeaway_cache_key(paper["title"], paper["abstract"]), {"t": item["takeaway"]}, ttl=86400)
    answer = result["answer"].rstrip() + (MEDICAL_DISCLAIMER if medical else "")

    source_counts = {}
    for paper in results:
        name = paper.get("database") or paper.get("description") or "Unknown source"
        source_counts[name] = source_counts.get(name, 0) + 1

    response = {
        "mode": "deep",
        "medical": medical,
        "sub_queries": queries,
        "count": len(results),
        "papers_used": len(evidence),
        "source_counts": source_counts,
        "results": results,
        "answer": answer,
        "citations": citations,
        "takeaways": takeaways,
        "meter": clean_meter(result.get("meter"), set(valid_titles)),
    }
    save_search_history(request.session_id, question, answer, citations=citations, user_id=user_id,
                        papers=[sanitize_paper_for_storage(r) for r in results])
    set_in_search_cache(cache_key, response, ttl=7200)
    return response


# ============================================================
# PER-PAPER DEEP DIVE
# ============================================================

DEEP_DIVE_SYSTEM_PROMPT = (
    "You are an expert research analyst. Using ONLY the title and abstract provided, write a concise markdown "
    "deep dive with these sections: Summary, Key findings, Methods, Limitations, Why it matters. "
    "If the abstract does not state something, say it is not stated; never invent details. "
    "Also fill 'glance', a quick summary with four fields of at most 25 words each: study_type (the design, e.g. "
    "randomised controlled trial, cohort study, meta-analysis), population (who or what was studied), sample_size "
    "(the number studied, as stated), and main_finding. Use exactly 'Not stated' for anything the abstract does not say. "
    "Return a valid JSON object with 'deep_dive' (string in markdown) and 'glance'."
)

GLANCE_FIELDS = ("study_type", "population", "sample_size", "main_finding")
GLANCE_MAX_CHARS = 300

DEEP_DIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "deep_dive": {"type": "string"},
        "glance": {
            "type": "object",
            "properties": {f: {"type": "string"} for f in GLANCE_FIELDS},
            "required": list(GLANCE_FIELDS),
            "additionalProperties": False,
        },
    },
    "required": ["deep_dive", "glance"],
    "additionalProperties": False,
}


def clean_glance(raw) -> dict:
    """Always all four fields; anything missing, empty or not text becomes 'Not stated'."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for f in GLANCE_FIELDS:
        value = raw.get(f)
        out[f] = value.strip()[:GLANCE_MAX_CHARS] if isinstance(value, str) and value.strip() else NOT_STATED
    return out


@app.post(
    "/deep-dive",
    tags=["AI"],
    summary="Analyse one paper",
    description="Markdown analysis (summary, key findings, methods, limitations, why it matters) written only from the title and abstract "
                "you send, plus `glance`: `study_type`, `population`, `sample_size` and `main_finding` (each `Not stated` when the "
                "abstract does not say). With no abstract the endpoint answers without calling the AI and `glance` is null. "
                "Cached per paper for 24 hours. Open to guests.",
    responses={**_ok({"deep_dive": "## Summary\n...", "glance": {"study_type": "Randomised controlled trial",
                                                                  "population": "Healthy adults", "sample_size": "40",
                                                                  "main_finding": "Sleep loss cut next-day recall."}}),
               **E_RATE, **E_AI},
)
async def deep_dive(request: DeepDiveRequest, req: Request, authorization: str | None = Header(None)):
    """Guest-friendly, rate limited. Analyses one paper from its title and abstract only."""
    gemini_client, groq_client = get_ai_clients()

    title = request.title.strip()
    abstract = request.abstract.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Paper title is required.")
    if not abstract or abstract.lower() == "no abstract available.":
        return {
            "deep_dive": "No abstract is available for this paper, so a grounded analysis can't be generated. "
            "Open the paper through its DOI to read the full text.",
            "glance": None,
        }

    cache_key = "dd:" + hashlib.sha256(json.dumps([title.lower(), abstract[:4000]]).encode("utf-8")).hexdigest()
    cached = get_from_search_cache(cache_key)
    if cached is not None:
        return cached

    enforce_ai_quota(req, optional_user_id(req, authorization))

    user_prompt = f"Title: {title}\n\nAbstract:\n{abstract[:4000]}"
    retry_prompt = f"Title: {title}\n\nAbstract:\n{abstract[:1200]}\n\nKeep it under 150 words."

    try:
        result = await asyncio.to_thread(
            generate_structured_ai,
            gemini_client,
            groq_client,
            DEEP_DIVE_SYSTEM_PROMPT,
            user_prompt,
            retry_prompt,
            DEEP_DIVE_SCHEMA,
            "deep_dive",
        )
        response = {"deep_dive": result["deep_dive"], "glance": clean_glance(result.get("glance"))}
        set_in_search_cache(cache_key, response, ttl=86400)
        return response
    except Exception as error:
        print("Deep dive failed:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)


# ============================================================
# TAKEAWAYS AND COMPARISON (shared helpers)
# ============================================================

NOT_STATED = "Not stated"


def usable_papers(papers: list[PaperInput], abstract_chars: int) -> list[dict]:
    """Papers that can be evidence: an abstract is present and the paper is not retracted."""
    if len({p.paper_id for p in papers}) != len(papers):
        raise HTTPException(status_code=400, detail="Each paper_id may appear only once.")
    out = []
    for p in papers:
        abstract = (p.abstract or "").strip()
        if not abstract or abstract.lower() == "no abstract available." or p.is_retracted:
            continue
        out.append({
            "paper_id": p.paper_id,
            "title": p.title.strip()[:300] or "Untitled",
            "publication_year": p.publication_year,
            "abstract": abstract[:abstract_chars],
        })
    return out


# ============================================================
# ONE-LINE TAKEAWAYS (up to 10 papers per call, cached per paper)
# ============================================================

TAKEAWAYS_SYSTEM_PROMPT = (
    "You summarise academic papers. For EVERY supplied paper write one sentence (at most 30 words) stating its "
    "main finding, using ONLY that paper's abstract. Never invent results, numbers or details. If the abstract "
    "states no finding, the takeaway is exactly 'Not stated in the abstract.' "
    "Return a valid JSON object with 'takeaways' (list of {paper_id, takeaway})."
)

TAKEAWAYS_SCHEMA = {
    "type": "object",
    "properties": {"takeaways": {"type": "array", "items": TAKEAWAY_ITEM_SCHEMA}},
    "required": ["takeaways"],
    "additionalProperties": False,
}


@app.post(
    "/takeaways",
    tags=["AI"],
    summary="One-line takeaways for papers",
    description="Send up to 10 papers (`paper_id` = position in `/search` results). Returns a one-sentence takeaway per paper, written "
                "only from its abstract. Papers without an abstract, or retracted, get none. Takeaways are cached per paper "
                "(including those `/ask` already wrote), and only uncached papers use an AI call. Open to guests.",
    responses={**_ok({"takeaways": [{"paper_id": 1, "takeaway": "One night of sleep loss cut next-day recall by 20%."}]}),
               **E_RATE, **E_AI},
)
async def takeaways(request: TakeawaysRequest, req: Request, authorization: str | None = Header(None)):
    papers = usable_papers(request.papers, 1200)
    found, missing = {}, []
    for p in papers:
        cached = get_from_search_cache(takeaway_cache_key(p["title"], p["abstract"]))
        if cached and isinstance(cached.get("t"), str):
            found[p["paper_id"]] = cached["t"]
        else:
            missing.append(p)

    if missing:
        gemini_client, groq_client = get_ai_clients()
        enforce_ai_quota(req, optional_user_id(req, authorization))
        user_prompt = f"Papers:\n{json.dumps(missing, indent=2)}"
        retry_prompt = "Papers:\n" + json.dumps([{**p, "abstract": p["abstract"][:400]} for p in missing])
        try:
            result = await asyncio.to_thread(
                generate_structured_ai,
                gemini_client,
                groq_client,
                TAKEAWAYS_SYSTEM_PROMPT,
                user_prompt,
                retry_prompt,
                TAKEAWAYS_SCHEMA,
                "takeaways",
            )
        except Exception as error:
            print("Takeaways failed:", error)
            raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)
        by_id = {p["paper_id"]: p for p in missing}
        for item in clean_takeaways(result.get("takeaways"), set(by_id)):
            found[item["paper_id"]] = item["takeaway"]
            paper = by_id[item["paper_id"]]
            set_in_search_cache(takeaway_cache_key(paper["title"], paper["abstract"]), {"t": item["takeaway"]},
                                ttl=86400)

    return {"takeaways": [{"paper_id": p["paper_id"], "takeaway": found[p["paper_id"]]}
                          for p in papers if p["paper_id"] in found]}


# ============================================================
# COMPARE 2 TO 5 PAPERS
# ============================================================

COMPARE_FIELDS = ("study_type", "population", "sample_size", "methods", "main_finding", "limitations")

COMPARE_SYSTEM_PROMPT = (
    "You compare academic papers using ONLY their titles and abstracts. For EVERY supplied paper fill: study_type, "
    "population (who or what was studied), sample_size, methods, main_finding and limitations, each in at most "
    f"25 words. If the abstract does not state something, write exactly '{NOT_STATED}'. Never invent details, numbers "
    "or papers. Then write 'summary': 2 to 3 sentences on how the papers agree or differ, citing them as [1], [2] by "
    "their paper_id. Return a valid JSON object with 'summary' and 'papers' (list of objects with paper_id and the "
    "six fields)."
)

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"paper_id": {"type": "integer"}, **{f: {"type": "string"} for f in COMPARE_FIELDS}},
                "required": ["paper_id", *COMPARE_FIELDS],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "papers"],
    "additionalProperties": False,
}


@app.post(
    "/compare",
    tags=["AI"],
    summary="Compare 2 to 5 papers",
    description="Send 2 to 5 papers (`paper_id` = position in `/search` results). Returns one row per paper (study type, population, "
                "sample size, methods, main finding, limitations), written only from the abstracts, with `Not stated` where the "
                "abstract is silent, plus a short summary citing the papers as `[n]`. Papers without an abstract, or retracted, "
                "are left out; at least two must remain. Cached. Open to guests.",
    responses={
        **_ok({"summary": "Both trials found better recall after sleep [1][2].",
               "rows": [{"paper_id": 1, "title": EXAMPLE_PAPER["title"], "publication_year": 2024,
                         **{f: NOT_STATED for f in COMPARE_FIELDS}}]}),
        **_err(400, "Fewer than two papers have an abstract.",
               "Compare needs at least two papers with an abstract. Retracted papers are left out."),
        **E_RATE, **E_AI,
    },
)
async def compare(request: CompareRequest, req: Request, authorization: str | None = Header(None)):
    papers = usable_papers(request.papers, 1500)
    if len(papers) < 2:
        raise HTTPException(status_code=400,
                            detail="Compare needs at least two papers with an abstract. Retracted papers are left out.")

    cache_key = "cmp:" + hashlib.sha256(json.dumps(
        [(p["paper_id"], p["title"].lower(), p["abstract"][:1000]) for p in papers]).encode("utf-8")).hexdigest()
    cached = get_from_search_cache(cache_key)
    if cached is not None:
        return cached

    gemini_client, groq_client = get_ai_clients()
    enforce_ai_quota(req, optional_user_id(req, authorization))
    user_prompt = f"Papers:\n{json.dumps(papers, indent=2)}"
    retry_prompt = "Papers:\n" + json.dumps([{**p, "abstract": p["abstract"][:500]} for p in papers])
    try:
        result = await asyncio.to_thread(
            generate_structured_ai,
            gemini_client,
            groq_client,
            COMPARE_SYSTEM_PROMPT,
            user_prompt,
            retry_prompt,
            COMPARE_SCHEMA,
            "summary",
        )
    except Exception as error:
        print("Compare failed:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)

    by_id = {}
    for item in result.get("papers") or []:
        if isinstance(item, dict) and item.get("paper_id") in {p["paper_id"] for p in papers}:
            by_id.setdefault(item["paper_id"], item)
    rows = []
    for p in papers:
        item = by_id.get(p["paper_id"], {})
        row = {"paper_id": p["paper_id"], "title": p["title"], "publication_year": p["publication_year"]}
        for f in COMPARE_FIELDS:
            value = item.get(f)
            row[f] = value.strip()[:400] if isinstance(value, str) and value.strip() else NOT_STATED
        rows.append(row)

    response = {"summary": str(result["summary"]).strip()[:2000], "rows": rows}
    set_in_search_cache(cache_key, response, ttl=7200)
    return response


# ============================================================
# ADMIN (Phase 5)
#
# Security model:
# - Admin rights are granted only on the server (backend/make_admin.py). No web route can make someone an admin.
# - The admin area needs the normal sign-in PLUS a 6-digit authenticator-app code (TOTP, RFC 6238). Unlocking sets a
#   separate short-lived `admin_token` cookie (30 minutes) that is tied to the current sign-in session: signing out,
#   changing the password, "log out everywhere" or losing admin rights ends it.
# - Every admin action is written to admin_audit_log. API key values are never logged, returned or shown.
# - API keys saved in the panel are encrypted with AES-256-GCM using SETTINGS_ENCRYPTION_KEY (from .env). A database
#   dump alone does not reveal them. Each key change needs a fresh authenticator code and is tested against the
#   provider before it is saved, so a wrong key never goes live. With no panel value, the .env value is used.
# ============================================================

ADMIN_SESSION_MINUTES = int(os.getenv("ADMIN_SESSION_MINUTES", "30"))
ADMIN_CODE_MAX_FAILURES = 5          # wrong authenticator codes per admin ...
ADMIN_CODE_LOCK_SECONDS = 900        # ... within 15 minutes, then locked until the window ends
SETTINGS_ENCRYPTION_KEY = os.getenv("SETTINGS_ENCRYPTION_KEY", "").strip()
TOTP_PERIOD = 30
TOTP_DIGITS = 6


class AppSetting(Base):
    """API keys saved from the admin panel (encrypted). A missing row means the .env value is used."""
    __tablename__ = "app_settings"

    name = Column(String(64), primary_key=True)
    value_encrypted = Column(Text, nullable=False)
    last4 = Column(String(8), nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(Integer, nullable=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, nullable=True, index=True)
    admin_email = Column(String(255), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    target = Column(String(255), nullable=True)
    detail = Column(String(500), nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), index=True)


# New tables are created now; the new user columns are added by init_db() on startup.
try:
    Base.metadata.create_all(bind=engine, tables=[AppSetting.__table__, AdminAuditLog.__table__])
except Exception as _admin_tables_error:
    print("Admin tables note:", _admin_tables_error)


# ---------------------------------------------------------------- encryption (AES-256-GCM)

def _settings_cipher():
    """AESGCM cipher from SETTINGS_ENCRYPTION_KEY, or None when it is not configured or invalid."""
    if not SETTINGS_ENCRYPTION_KEY:
        return None
    try:
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.urlsafe_b64decode(SETTINGS_ENCRYPTION_KEY + "=" * (-len(SETTINGS_ENCRYPTION_KEY) % 4))
        if len(raw) != 32:
            print("SETTINGS_ENCRYPTION_KEY must decode to 32 bytes; admin features are disabled.")
            return None
        return AESGCM(raw)
    except Exception as error:
        print("SETTINGS_ENCRYPTION_KEY note:", type(error).__name__)
        return None


def encrypt_setting(name: str, value: str) -> str:
    import base64

    cipher = _settings_cipher()
    if cipher is None:
        raise RuntimeError("SETTINGS_ENCRYPTION_KEY is not configured.")
    nonce = secrets.token_bytes(12)
    # The setting name is authenticated data: a ciphertext cannot be moved to a different setting.
    sealed = cipher.encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))
    return "v1:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt_setting(name: str, stored: str) -> str | None:
    import base64

    cipher = _settings_cipher()
    if cipher is None or not stored or not stored.startswith("v1:"):
        return None
    try:
        blob = base64.urlsafe_b64decode(stored[3:])
        return cipher.decrypt(blob[:12], blob[12:], name.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------- authenticator codes (TOTP, RFC 6238)

def new_totp_secret() -> str:
    import base64

    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret_b32: str, step: int) -> str:
    import base64
    import struct

    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % (10 ** TOTP_DIGITS)
    return str(number).zfill(TOTP_DIGITS)


def match_totp(secret_b32: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """Return the matching time step (one step of clock drift allowed either way), or None. A step at or
    before last_step is refused, so a code can never be used twice."""
    code = (code or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    current = int((at if at is not None else time.time()) // TOTP_PERIOD)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp_code(secret_b32, step), code):
            return step
    return None


def totp_uri(secret_b32: str, email: str) -> str:
    from urllib.parse import quote

    issuer = quote(APP_NAME)
    return f"otpauth://totp/{issuer}:{quote(email)}?secret={secret_b32}&issuer={issuer}&period={TOTP_PERIOD}&digits={TOTP_DIGITS}"


# ---------------------------------------------------------------- admin session cookie

def _sign(purpose: str, payload_hex: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), f"{purpose}:{payload_hex}".encode("utf-8"), hashlib.sha256).hexdigest()


def create_admin_token(user: "User", session_jti: str) -> tuple[str, int]:
    expires = int(time.time()) + ADMIN_SESSION_MINUTES * 60
    payload = {"uid": user.id, "sid": session_jti, "pv": password_version(user.hashed_password), "exp": expires}
    payload_hex = json.dumps(payload, separators=(",", ":")).encode("utf-8").hex()
    return f"{payload_hex}.{_sign('admin', payload_hex)}", expires


def read_admin_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    try:
        payload_hex, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign("admin", payload_hex)):
            return None
        payload = json.loads(bytes.fromhex(payload_hex).decode("utf-8"))
        return payload if payload.get("exp", 0) >= int(time.time()) else None
    except Exception:
        return None


def _admin_cookie_kwargs() -> dict:
    kwargs = {"key": "admin_token", "path": "/", "httponly": True, "secure": COOKIE_SECURE,
              # Strict: the admin cookie is never sent on requests started by other sites.
              "samesite": "strict" if COOKIE_SAMESITE != "none" else "none"}
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    return kwargs


def session_user_and_jti(request: Request, authorization: str | None = None) -> tuple["User", str]:
    """The signed-in user and their session id. 401 when not signed in."""
    token = extract_token_from_request(request, authorization)
    payload = verify_access_token(token) if token else None
    user = _load_session_user(payload)
    if not user or not payload.get("jti"):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user, payload["jti"]


def require_admin(request: Request, authorization: str | None = None) -> "User":
    """Signed in, verified, not disabled, still an admin, and unlocked with an authenticator code for this session."""
    user, jti = session_user_and_jti(request, authorization)
    if not getattr(user, "is_admin", False) or not user.is_verified:
        raise HTTPException(status_code=403, detail="Not authorized.")
    admin = read_admin_token(request.cookies.get("admin_token"))
    if (not admin or admin.get("uid") != user.id or admin.get("sid") != jti
            or admin.get("pv") != password_version(user.hashed_password)):
        raise HTTPException(status_code=403, detail="Admin area locked. Enter your authenticator code.")
    return user


def audit(admin: "User | None", action: str, request: Request | None, target: str | None = None,
          detail: str | None = None):
    db = SessionLocal()
    try:
        db.add(AdminAuditLog(
            admin_user_id=admin.id if admin else None,
            admin_email=admin.email if admin else None,
            action=action[:64],
            target=(target or "")[:255] or None,
            detail=(detail or "")[:500] or None,
            ip=(get_client_ip(request) if request is not None else None),
        ))
        db.commit()
    except Exception as error:
        print("Audit log note:", error)
    finally:
        db.close()


def _check_admin_code(user_id: int, code: str) -> None:
    """Verify an authenticator code for this admin (lockout after repeated failures; codes are single-use)."""
    fail_key = f"adminfail:{user_id}"
    if counter_get(fail_key) >= ADMIN_CODE_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many wrong codes. Please wait 15 minutes.",
                            headers={"Retry-After": str(max(1, counter_ttl(fail_key)))})
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        secret = decrypt_setting(f"totp:{user_id}", user.totp_secret) if user and user.totp_secret else None
        if not secret:
            raise HTTPException(status_code=403, detail="No authenticator is set up for this account. "
                                                        "Run make_admin.py on the server.")
        step = match_totp(secret, code, user.totp_last_step)
        if step is None:
            counter_incr(fail_key, ADMIN_CODE_LOCK_SECONDS)
            raise HTTPException(status_code=401, detail="Wrong or already used code. Wait for the next code and try again.")
        user.totp_last_step = step
        db.commit()
        counter_reset(fail_key)
    finally:
        db.close()


# ---------------------------------------------------------------- API keys editable from the panel

def _test_gemini(key: str):
    genai.Client(api_key=key).models.get(model=GEMINI_MODEL)


def _test_groq(key: str):
    Groq(api_key=key).models.list()


def _test_http(url: str, params: dict | None = None, headers: dict | None = None):
    response = httpx.get(url, params=params, headers=headers, timeout=15.0, follow_redirects=True)
    if response.status_code in (401, 403):
        raise ValueError("The provider rejected this key.")
    response.raise_for_status()


def _test_core(key: str):
    _test_http("https://api.core.ac.uk/v3/search/works/", {"q": "test", "limit": 1}, {"Authorization": f"Bearer {key}"})


def _test_semantic_scholar(key: str):
    _test_http("https://api.semanticscholar.org/graph/v1/paper/search", {"query": "test", "limit": 1}, {"x-api-key": key})


def _test_ncbi(key: str):
    response = httpx.get(f"{NCBI_EUTILS}/einfo.fcgi", params={"db": "pubmed", "retmode": "json", "api_key": key},
                         timeout=15.0)
    if response.status_code != 200 or "error" in response.text[:500].lower():
        raise ValueError("The provider rejected this key.")


MANAGED_KEYS = {
    "GEMINI_API_KEY": {"label": "Gemini (main AI)", "test": _test_gemini},
    "GROQ_API_KEY": {"label": "Groq (AI fallback)", "test": _test_groq},
    "CORE_API_KEY": {"label": "CORE (papers)", "test": _test_core},
    "SEMANTIC_SCHOLAR_API_KEY": {"label": "Semantic Scholar (papers)", "test": _test_semantic_scholar},
    "NCBI_API_KEY": {"label": "PubMed / NCBI (papers)", "test": _test_ncbi},
}

_PANEL_KEYS: dict[str, str] | None = None   # decrypted panel values, loaded once (single worker)
_PANEL_KEYS_LOCK = threading.Lock()


def _load_panel_keys() -> dict[str, str]:
    global _PANEL_KEYS
    with _PANEL_KEYS_LOCK:
        if _PANEL_KEYS is None:
            values = {}
            db = SessionLocal()
            try:
                for row in db.query(AppSetting).filter(AppSetting.name.in_(list(MANAGED_KEYS))).all():
                    value = decrypt_setting(row.name, row.value_encrypted)
                    if value:
                        values[row.name] = value
            except Exception as error:
                print("Panel keys note:", type(error).__name__)
            finally:
                db.close()
            _PANEL_KEYS = values
        return _PANEL_KEYS


def effective_key(name: str) -> str:
    """The key in use: a value saved in the admin panel wins, otherwise the .env value."""
    return _load_panel_keys().get(name) or globals().get(name, "") or ""


def reset_panel_key_cache():
    global _PANEL_KEYS
    with _PANEL_KEYS_LOCK:
        _PANEL_KEYS = None


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 8 else ""


# ---------------------------------------------------------------- request models

class AdminCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class AdminKeyRequest(BaseModel):
    value: str = Field(min_length=8, max_length=500)
    code: str = Field(min_length=6, max_length=8)


E_ADMIN = {
    **_err(401, "Not signed in, or a wrong / already used authenticator code.", "Authentication required."),
    **_err(403, "Not an admin, or the admin area is locked.", "Admin area locked. Enter your authenticator code."),
}


# ---------------------------------------------------------------- routes

@app.get("/admin/status", tags=["Admin"], include_in_schema=False, summary="Admin access status",
          description="Whether the signed-in user is an admin and whether the admin area is unlocked for this session.", responses=E_ADMIN)
def admin_status(request: Request, authorization: str | None = Header(None)):
    user, jti = session_user_and_jti(request, authorization)
    is_admin = bool(getattr(user, "is_admin", False)) and user.is_verified
    admin = read_admin_token(request.cookies.get("admin_token")) if is_admin else None
    unlocked = bool(admin and admin.get("uid") == user.id and admin.get("sid") == jti
                    and admin.get("pv") == password_version(user.hashed_password))
    return {
        "is_admin": is_admin,
        "unlocked": unlocked,
        "expires_at": admin.get("exp") if unlocked else None,
        "encryption_configured": _settings_cipher() is not None,
        "session_minutes": ADMIN_SESSION_MINUTES,
    }


@app.post("/admin/unlock", tags=["Admin"], include_in_schema=False, summary="Unlock the admin area with an authenticator code",
          description="Checks a 6-digit authenticator code and sets a 30-minute `admin_token` cookie tied to this sign-in. Each code works once; 5 wrong codes lock for 15 minutes.",
          responses={**_ok({"unlocked": True, "expires_at": 1767225600}), **E_ADMIN, **E_RATE})
def admin_unlock(body: AdminCodeRequest, request: Request, response: Response, authorization: str | None = Header(None)):
    user, jti = session_user_and_jti(request, authorization)
    if not getattr(user, "is_admin", False) or not user.is_verified:
        raise HTTPException(status_code=403, detail="Not authorized.")
    if _settings_cipher() is None:
        raise HTTPException(status_code=503, detail="Admin is not configured on the server (SETTINGS_ENCRYPTION_KEY).")
    try:
        _check_admin_code(user.id, body.code)
    except HTTPException as error:
        if error.status_code == 401:
            audit(user, "unlock_failed", request)
        raise
    token, expires = create_admin_token(user, jti)
    response.set_cookie(value=token, max_age=ADMIN_SESSION_MINUTES * 60, **_admin_cookie_kwargs())
    audit(user, "unlock", request)
    return {"unlocked": True, "expires_at": expires}


@app.post("/admin/lock", tags=["Admin"], include_in_schema=False, summary="Lock the admin area",
          description="Clears the admin cookie. The normal sign-in stays.", responses=_ok({"unlocked": False}))
def admin_lock(response: Response):
    kwargs = _admin_cookie_kwargs()
    response.delete_cookie(**{k: v for k, v in kwargs.items()})
    return {"unlocked": False}


@app.get("/admin/overview", tags=["Admin"], include_in_schema=False, summary="Dashboard numbers",
          description="Users, answers saved, AI calls and deep research runs today against the daily caps.", responses=E_ADMIN)
def admin_overview(request: Request, authorization: str | None = Header(None)):
    require_admin(request, authorization)
    now = _utcnow().replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = _utcnow().strftime("%Y%m%d")
    db = SessionLocal()
    try:
        users = db.query(User)
        data = {
            "users": {
                "total": users.count(),
                "verified": users.filter(User.is_verified.is_(True)).count(),
                "new_7_days": users.filter(User.created_at >= now - timedelta(days=7)).count(),
                "active_today": users.filter(User.last_login_at >= today).count(),
                "disabled": users.filter(User.is_disabled.is_(True)).count(),
                "admins": users.filter(User.is_admin.is_(True)).count(),
            },
            "answers": {
                "today": db.query(SearchHistory).filter(SearchHistory.created_at >= today).count(),
                "last_7_days": db.query(SearchHistory).filter(SearchHistory.created_at >= now - timedelta(days=7)).count(),
            },
        }
    finally:
        db.close()
    data["ai"] = {
        "calls_today": counter_get(f"ai:g:{day}"), "daily_cap": AI_DAILY_LIMIT_GLOBAL,
        "guest_cap": AI_DAILY_LIMIT_GUEST, "user_cap": AI_DAILY_LIMIT_USER,
    }
    data["deep"] = {
        "runs_today": counter_get(f"deep:g:{day}"), "daily_cap": DEEP_DAILY_LIMIT_GLOBAL,
        "guest_cap": DEEP_DAILY_LIMIT_GUEST, "user_cap": DEEP_DAILY_LIMIT_USER,
    }
    db = SessionLocal()
    try:
        from sqlalchemy import func

        count, chars = db.query(func.count(UploadedDocument.id), func.coalesce(func.sum(UploadedDocument.chars), 0)).one()
        data["uploads"] = {"documents": int(count or 0), "text_mb": round(int(chars or 0) / 1_000_000, 2)}
    finally:
        db.close()
    data["counters_shared"] = redis_client is not None
    return data


def _user_row(user: "User", answers: int) -> dict:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "is_verified": bool(user.is_verified),
        "is_admin": bool(user.is_admin), "is_disabled": bool(user.is_disabled),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "answers": answers,
    }


@app.get("/admin/users", tags=["Admin"], include_in_schema=False, summary="List users",
          description="Paged user list with optional search on email or name, newest first.", responses=E_ADMIN)
def admin_users(request: Request, q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000),
                page_size: int = Query(default=25, ge=1, le=100), authorization: str | None = Header(None)):
    require_admin(request, authorization)
    from sqlalchemy import func, or_

    db = SessionLocal()
    try:
        query = db.query(User)
        term = q.strip().lower()
        if term:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.filter(or_(func.lower(User.email).like(like, escape="\\"),
                                     func.lower(User.full_name).like(like, escape="\\")))
        total = query.count()
        users = query.order_by(User.created_at.desc(), User.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        ids = [u.id for u in users]
        counts = dict(db.query(SearchHistory.user_id, func.count(SearchHistory.id))
                      .filter(SearchHistory.user_id.in_(ids)).group_by(SearchHistory.user_id).all()) if ids else {}
        return {"total": total, "page": page, "page_size": page_size,
                "users": [_user_row(u, int(counts.get(u.id, 0))) for u in users]}
    finally:
        db.close()


def _target_user(db, user_id: int) -> "User":
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@app.post("/admin/users/{user_id}/disable", tags=["Admin"], include_in_schema=False, summary="Disable an account (reversible)",
          description="Blocks sign-in and ends the user's sessions. Reversible. Admin accounts cannot be disabled here.", responses=E_ADMIN)
def admin_disable_user(user_id: int, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    db = SessionLocal()
    try:
        user = _target_user(db, user_id)
        if user.id == admin.id or user.is_admin:
            raise HTTPException(status_code=400, detail="Admin accounts cannot be disabled here.")
        user.is_disabled = True
        user.tokens_valid_after = _utcnow().replace(tzinfo=None)  # ends their sessions now
        db.commit()
        audit(admin, "user_disable", request, target=user.email)
        return {"id": user.id, "is_disabled": True}
    finally:
        db.close()


@app.post("/admin/users/{user_id}/enable", tags=["Admin"], include_in_schema=False, summary="Re-enable an account",
          description="Allows a disabled account to sign in again.", responses=E_ADMIN)
def admin_enable_user(user_id: int, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    db = SessionLocal()
    try:
        user = _target_user(db, user_id)
        user.is_disabled = False
        db.commit()
        audit(admin, "user_enable", request, target=user.email)
        return {"id": user.id, "is_disabled": False}
    finally:
        db.close()


@app.post("/admin/users/{user_id}/logout-all", tags=["Admin"], include_in_schema=False, summary="Sign a user out everywhere",
          description="Ends every session of this user.", responses=E_ADMIN)
def admin_logout_user(user_id: int, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Use \"Log out of all devices\" in your own settings instead.")
    db = SessionLocal()
    try:
        user = _target_user(db, user_id)
        user.tokens_valid_after = _utcnow().replace(tzinfo=None)
        db.commit()
        audit(admin, "user_logout_all", request, target=user.email)
        return {"id": user.id, "signed_out": True}
    finally:
        db.close()


@app.post("/admin/users/{user_id}/resend-verification", tags=["Admin"], include_in_schema=False, summary="Resend the verification email",
          description="Sends a new verification link to an unverified account (email limits apply).",
          responses=E_ADMIN)
def admin_resend_verification(user_id: int, request: Request, background_tasks: BackgroundTasks,
                              authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    db = SessionLocal()
    try:
        user = _target_user(db, user_id)
        if user.is_verified:
            raise HTTPException(status_code=400, detail="This account is already verified.")
        if not email_allowed("verify", user.email):
            raise HTTPException(status_code=429, detail="Too many emails to this address. Try again later.")
        token = secrets.token_urlsafe(32)
        user.verification_token = hash_token(token)
        user.verification_token_expires_at = _utcnow() + timedelta(hours=VERIFICATION_TOKEN_HOURS)
        db.commit()
        background_tasks.add_task(send_verification_email, user.email, token, user.full_name)
        audit(admin, "user_resend_verification", request, target=user.email)
        return {"id": user.id, "sent": True}
    finally:
        db.close()


@app.get("/admin/audit", tags=["Admin"], include_in_schema=False, summary="Admin audit log",
          description="Every admin action, newest first. Key values are never recorded.", responses=E_ADMIN)
def admin_audit(request: Request, page: int = Query(default=1, ge=1, le=10000),
                page_size: int = Query(default=50, ge=1, le=200), authorization: str | None = Header(None)):
    require_admin(request, authorization)
    db = SessionLocal()
    try:
        query = db.query(AdminAuditLog)
        total = query.count()
        rows = query.order_by(AdminAuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {"total": total, "page": page, "page_size": page_size, "entries": [
            {"id": r.id, "admin_email": r.admin_email, "action": r.action, "target": r.target, "detail": r.detail,
             "ip": r.ip, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
    finally:
        db.close()


@app.get("/admin/keys", tags=["Admin"], include_in_schema=False, summary="API key status (values are never returned)",
          description="Each managed key's source (panel, env or none) and last 4 characters. Values are never returned.", responses=E_ADMIN)
def admin_keys(request: Request, authorization: str | None = Header(None)):
    require_admin(request, authorization)
    db = SessionLocal()
    try:
        rows = {r.name: r for r in db.query(AppSetting).filter(AppSetting.name.in_(list(MANAGED_KEYS))).all()}
    finally:
        db.close()
    panel = _load_panel_keys()
    keys = []
    for name, meta in MANAGED_KEYS.items():
        env_value = globals().get(name, "") or ""
        row = rows.get(name)
        if name in panel:
            source, last4 = "panel", row.last4 if row else ""
        elif env_value:
            source, last4 = "env", _last4(env_value)
        else:
            source, last4 = "none", ""
        keys.append({"name": name, "label": meta["label"], "source": source, "last4": last4 or None,
                     "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
                     "env_set": bool(env_value)})
    return {"keys": keys, "editable": _settings_cipher() is not None}


@app.post("/admin/keys/{name}", tags=["Admin"], include_in_schema=False, summary="Replace an API key (tested first, write-only)",
          description="Needs a fresh authenticator code. The key is tested with the provider first; a rejected key is not saved. Stored encrypted (AES-256-GCM) and used at once, no restart.",
          responses={**E_ADMIN, **_err(400, "The provider rejected the key, so it was not saved.",
                                         "The provider rejected this key. Nothing was changed.")})
async def admin_set_key(name: str, body: AdminKeyRequest, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    if name not in MANAGED_KEYS:
        raise HTTPException(status_code=404, detail="Unknown key.")
    if _settings_cipher() is None:
        raise HTTPException(status_code=503, detail="Key editing needs SETTINGS_ENCRYPTION_KEY on the server.")
    value = body.value.strip()
    if any(ch.isspace() for ch in value) or not value.isprintable():
        raise HTTPException(status_code=400, detail="A key cannot contain spaces or line breaks.")
    _check_admin_code(admin.id, body.code)
    try:
        await asyncio.wait_for(asyncio.to_thread(MANAGED_KEYS[name]["test"], value), timeout=20)
    except Exception as error:
        print(f"Key test failed for {name}:", type(error).__name__)  # never the key or the provider's message
        audit(admin, "key_test_failed", request, target=name)
        raise HTTPException(status_code=400, detail="The provider rejected this key, or could not be reached. "
                                                    "Nothing was changed.")
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.name == name).first()
        if row is None:
            row = AppSetting(name=name)
            db.add(row)
        row.value_encrypted = encrypt_setting(name, value)
        row.last4 = _last4(value)
        row.updated_at = _utcnow().replace(tzinfo=None)
        row.updated_by = admin.id
        db.commit()
    finally:
        db.close()
    reset_panel_key_cache()
    audit(admin, "key_set", request, target=name, detail=f"ends in {_last4(value)}" if _last4(value) else None)
    return {"name": name, "source": "panel", "last4": _last4(value) or None}


@app.post("/admin/keys/{name}/clear", tags=["Admin"], include_in_schema=False, summary="Remove the panel value (the server configuration value is used again)",
          description="Needs a fresh authenticator code. removes the panel value so the server configuration value is used again.",
          responses=E_ADMIN)
def admin_clear_key(name: str, body: AdminCodeRequest, request: Request, authorization: str | None = Header(None)):
    admin = require_admin(request, authorization)
    if name not in MANAGED_KEYS:
        raise HTTPException(status_code=404, detail="Unknown key.")
    _check_admin_code(admin.id, body.code)
    db = SessionLocal()
    try:
        db.query(AppSetting).filter(AppSetting.name == name).delete()
        db.commit()
    finally:
        db.close()
    reset_panel_key_cache()
    audit(admin, "key_clear", request, target=name)
    return {"name": name, "source": "env" if globals().get(name) else "none"}


# ============================================================
# COLLECTIONS, SAVED CONVERSATIONS AND SHARE LINKS (Phase 6)
# Verified accounts only, like the library. A shared conversation is read-only, reachable only through an
# unguessable link (256-bit random token), can be switched off at any time, and never shows who shared it.
# ============================================================

MAX_COLLECTIONS_PER_USER = 50
MAX_THREADS_PER_USER = 200
MAX_THREAD_TURNS = 50
SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, index=True)
    collection_id = Column(Integer, nullable=False, index=True)
    bookmark_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Thread(Base):
    __tablename__ = "threads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    turns = Column(JSON, nullable=False, default=list)
    papers = Column(JSON, nullable=False, default=list)
    share_token = Column(String(64), nullable=True, unique=True, index=True)
    shared_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


try:
    Base.metadata.create_all(bind=engine, tables=[Collection.__table__, CollectionItem.__table__, Thread.__table__])
except Exception as _phase6_tables_error:
    print("Phase 6 tables note:", _phase6_tables_error)


class CollectionNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CollectionItemRequest(BaseModel):
    bookmark_id: int = Field(ge=1)


class ThreadTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=20000)
    citations: list[dict] = Field(default_factory=list, max_length=50)
    mode: str | None = Field(default=None, max_length=20)


class ThreadSaveRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    turns: list[ThreadTurn] = Field(min_length=1, max_length=MAX_THREAD_TURNS)
    papers: list[dict] = Field(default_factory=list, max_length=50)


class ThreadRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def _clean_name(value: str, limit: int) -> str:
    name = re.sub(r"\s+", " ", value or "").strip()[:limit]
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    return name


def _own_collection(db, uid: int, collection_id: int) -> "Collection":
    collection = db.query(Collection).filter(Collection.id == collection_id, Collection.user_id == uid).first()
    if not collection:
        raise HTTPException(status_code=404, detail="Folder not found.")
    return collection


def _own_thread(db, uid: int, thread_id: int) -> "Thread":
    thread = db.query(Thread).filter(Thread.id == thread_id, Thread.user_id == uid).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return thread


def _clean_turns(turns: list[ThreadTurn]) -> list[dict]:
    out = []
    for t in turns:
        citations = []
        for c in t.citations[:50]:
            pid = c.get("paper_id") if isinstance(c, dict) else None
            if isinstance(pid, int) and not isinstance(pid, bool) and 1 <= pid <= 1000:
                citations.append({"paper_id": pid, "title": str(c.get("title") or "")[:300]})
        out.append({
            "question": t.question.strip(),
            "answer": t.answer.strip(),
            "citations": citations,
            "mode": t.mode if t.mode in ("quick", "deep", "medical") else None,
        })
    return out


def _thread_summary(t: "Thread") -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "turn_count": len(t.turns or []),
        "paper_count": len(t.papers or []),
        "shared": bool(t.share_token),
        "share_token": t.share_token,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# ---------------------------------------------------------------- collections

@app.get("/collections", tags=["Library"], summary="List your folders",
         description="Your folders, oldest first, each with how many saved papers it holds. Verified accounts only.",
         responses={**_ok({"collections": [{"id": 1, "name": "Thesis", "count": 4, "created_at": "2026-11-11T09:30:00"}]}),
                    **E_401, **E_403})
def list_collections(request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    from sqlalchemy import func

    db = SessionLocal()
    try:
        rows = db.query(Collection).filter(Collection.user_id == uid).order_by(Collection.id).all()
        ids = [c.id for c in rows]
        counts = dict(db.query(CollectionItem.collection_id, func.count(CollectionItem.id))
                      .filter(CollectionItem.collection_id.in_(ids)).group_by(CollectionItem.collection_id).all()) if ids else {}
        return {"collections": [{"id": c.id, "name": c.name, "count": int(counts.get(c.id, 0)),
                                 "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]}
    finally:
        db.close()


@app.post("/collections", tags=["Library"], summary="Create a folder",
          description=f"Creates a folder for saved papers. Names are up to 100 characters; at most {MAX_COLLECTIONS_PER_USER} "
                      "folders per account. Verified accounts only.",
          responses={**_ok({"id": 1, "name": "Thesis", "count": 0}), **E_401, **E_403})
def create_collection(body: CollectionNameRequest, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    name = _clean_name(body.name, 100)
    db = SessionLocal()
    try:
        if db.query(Collection).filter(Collection.user_id == uid).count() >= MAX_COLLECTIONS_PER_USER:
            raise HTTPException(status_code=400, detail=f"You can have up to {MAX_COLLECTIONS_PER_USER} folders.")
        collection = Collection(user_id=uid, name=name)
        db.add(collection)
        db.commit()
        db.refresh(collection)
        return {"id": collection.id, "name": collection.name, "count": 0}
    finally:
        db.close()


@app.post("/collections/{collection_id}/rename", tags=["Library"], summary="Rename a folder",
          description="Renames one of your folders. Someone else's folder returns 404. Verified accounts only.",
          responses={**_ok({"id": 1, "name": "Thesis chapter 2"}), **E_401, **E_403})
def rename_collection(collection_id: int, body: CollectionNameRequest, request: Request,
                      authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        collection = _own_collection(db, uid, collection_id)
        collection.name = _clean_name(body.name, 100)
        db.commit()
        return {"id": collection.id, "name": collection.name}
    finally:
        db.close()


@app.delete("/collections/{collection_id}", tags=["Library"], summary="Delete a folder",
            description="Deletes the folder only. The papers in it stay in your library. Verified accounts only.",
            responses={**_ok({"message": "Folder deleted."}), **E_401, **E_403})
def delete_collection(collection_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        collection = _own_collection(db, uid, collection_id)
        db.query(CollectionItem).filter(CollectionItem.collection_id == collection.id).delete(synchronize_session=False)
        db.delete(collection)
        db.commit()
        return {"message": "Folder deleted."}
    finally:
        db.close()


@app.post("/collections/{collection_id}/items", tags=["Library"], summary="Add a saved paper to a folder",
          description="Adds one of your saved papers (`bookmark_id`) to one of your folders. Adding it twice is harmless. "
                      "Verified accounts only.",
          responses={**_ok({"collection_id": 1, "bookmark_id": 3, "added": True}), **E_401, **E_403})
def add_collection_item(collection_id: int, body: CollectionItemRequest, request: Request,
                        authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        _own_collection(db, uid, collection_id)
        if not db.query(Bookmark).filter(Bookmark.id == body.bookmark_id, Bookmark.user_id == uid).first():
            raise HTTPException(status_code=404, detail="Saved paper not found.")
        exists = db.query(CollectionItem).filter(CollectionItem.collection_id == collection_id,
                                                 CollectionItem.bookmark_id == body.bookmark_id).first()
        if not exists:
            db.add(CollectionItem(collection_id=collection_id, bookmark_id=body.bookmark_id))
            db.commit()
        return {"collection_id": collection_id, "bookmark_id": body.bookmark_id, "added": True}
    finally:
        db.close()


@app.delete("/collections/{collection_id}/items/{bookmark_id}", tags=["Library"], summary="Remove a paper from a folder",
            description="Takes the paper out of the folder. It stays in your library. Verified accounts only.",
            responses={**_ok({"message": "Removed from folder."}), **E_401, **E_403})
def remove_collection_item(collection_id: int, bookmark_id: int, request: Request,
                           authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        _own_collection(db, uid, collection_id)
        db.query(CollectionItem).filter(CollectionItem.collection_id == collection_id,
                                        CollectionItem.bookmark_id == bookmark_id).delete(synchronize_session=False)
        db.commit()
        return {"message": "Removed from folder."}
    finally:
        db.close()


# ---------------------------------------------------------------- saved conversations

@app.get("/threads", tags=["Library"], summary="List saved conversations",
         description="Your saved conversations, most recently updated first (without the full text). Verified accounts only.",
         responses={**_ok({"threads": [{"id": 1, "title": "Sleep and memory", "turn_count": 2, "paper_count": 30,
                                       "shared": False, "share_token": None}]}), **E_401, **E_403})
def list_threads(request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        rows = db.query(Thread).filter(Thread.user_id == uid).order_by(Thread.updated_at.desc(), Thread.id.desc()).all()
        return {"threads": [_thread_summary(t) for t in rows]}
    finally:
        db.close()


@app.post("/threads", tags=["Library"], summary="Save a conversation",
          description=f"Saves the questions, answers and papers of a conversation (up to {MAX_THREAD_TURNS} turns and 50 papers). "
                      "The title defaults to the first question. Verified accounts only.",
          responses={**_ok({"id": 1, "title": "Sleep and memory", "turn_count": 1}), **E_401, **E_403})
def create_thread(body: ThreadSaveRequest, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    turns = _clean_turns(body.turns)
    title = _clean_name(body.title or turns[0]["question"], 200)
    db = SessionLocal()
    try:
        if db.query(Thread).filter(Thread.user_id == uid).count() >= MAX_THREADS_PER_USER:
            raise HTTPException(status_code=400, detail=f"You can save up to {MAX_THREADS_PER_USER} conversations. "
                                                        "Delete some to save more.")
        thread = Thread(user_id=uid, title=title, turns=turns,
                        papers=[sanitize_paper_for_storage(p) for p in body.papers[:50]])
        db.add(thread)
        db.commit()
        db.refresh(thread)
        return _thread_summary(thread)
    finally:
        db.close()


@app.get("/threads/{thread_id}", tags=["Library"], summary="Open a saved conversation",
         description="Returns one of your saved conversations in full. Someone else's returns 404. Verified accounts only.",
         responses={**_ok({"id": 1, "title": "Sleep and memory", "turns": [], "papers": []}), **E_401, **E_403})
def get_thread(thread_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        t = _own_thread(db, uid, thread_id)
        return {**_thread_summary(t), "turns": t.turns or [], "papers": t.papers or []}
    finally:
        db.close()


@app.post("/threads/{thread_id}", tags=["Library"], summary="Update a saved conversation",
          description="Replaces the turns and papers (for example after more follow-up questions). A share link keeps "
                      "working and shows the update. Verified accounts only.",
          responses={**_ok({"id": 1, "title": "Sleep and memory", "turn_count": 3}), **E_401, **E_403})
def update_thread(thread_id: int, body: ThreadSaveRequest, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        t = _own_thread(db, uid, thread_id)
        t.turns = _clean_turns(body.turns)
        t.papers = [sanitize_paper_for_storage(p) for p in body.papers[:50]]
        if body.title:
            t.title = _clean_name(body.title, 200)
        t.updated_at = _utcnow().replace(tzinfo=None)
        db.commit()
        return _thread_summary(t)
    finally:
        db.close()


@app.post("/threads/{thread_id}/rename", tags=["Library"], summary="Rename a saved conversation",
          description="Changes the title. Verified accounts only.",
          responses={**_ok({"id": 1, "title": "New title"}), **E_401, **E_403})
def rename_thread(thread_id: int, body: ThreadRenameRequest, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        t = _own_thread(db, uid, thread_id)
        t.title = _clean_name(body.title, 200)
        db.commit()
        return _thread_summary(t)
    finally:
        db.close()


@app.delete("/threads/{thread_id}", tags=["Library"], summary="Delete a saved conversation",
            description="Deletes it. A share link to it stops working at once. Verified accounts only.",
            responses={**_ok({"message": "Conversation deleted."}), **E_401, **E_403})
def delete_thread(thread_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        db.delete(_own_thread(db, uid, thread_id))
        db.commit()
        return {"message": "Conversation deleted."}
    finally:
        db.close()


@app.post("/threads/{thread_id}/share", tags=["Library"], summary="Turn on a share link",
          description="Creates a read-only link (`/share/<token>`) anyone can open. The token is 256-bit random. Sharing again "
                      "returns the same link. Verified accounts only.",
          responses={**_ok({"id": 1, "shared": True, "share_token": "Xy..."}), **E_401, **E_403})
def share_thread(thread_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        t = _own_thread(db, uid, thread_id)
        if not t.share_token:
            t.share_token = secrets.token_urlsafe(32)
            t.shared_at = _utcnow().replace(tzinfo=None)
            db.commit()
        return _thread_summary(t)
    finally:
        db.close()


@app.post("/threads/{thread_id}/unshare", tags=["Library"], summary="Turn off the share link",
          description="The old link stops working immediately. Sharing again later creates a new, different link. "
                      "Verified accounts only.",
          responses={**_ok({"id": 1, "shared": False, "share_token": None}), **E_401, **E_403})
def unshare_thread(thread_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        t = _own_thread(db, uid, thread_id)
        t.share_token = None
        t.shared_at = None
        db.commit()
        return _thread_summary(t)
    finally:
        db.close()


@app.get("/share/{token}", tags=["Library"], summary="Open a shared conversation",
         description="Public, read-only. Returns the title, questions, answers and papers of a shared conversation, and nothing "
                     "about who shared it. Unknown or switched-off links return 404.",
         responses={**_ok({"title": "Sleep and memory", "turns": [], "papers": [], "shared_at": "2026-11-11T09:30:00"}),
                    **_err(404, "Unknown or switched-off link.", "This shared conversation is not available.")})
def open_shared_thread(token: str, response: Response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if not SHARE_TOKEN_RE.fullmatch(token or ""):
        raise HTTPException(status_code=404, detail="This shared conversation is not available.")
    db = SessionLocal()
    try:
        t = db.query(Thread).filter(Thread.share_token == token).first()
        if not t:
            raise HTTPException(status_code=404, detail="This shared conversation is not available.")
        return {"title": t.title, "turns": t.turns or [], "papers": t.papers or [],
                "shared_at": t.shared_at.isoformat() if t.shared_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None}
    finally:
        db.close()


# ============================================================
# PDF UPLOADS AND FULL-TEXT CHAT (Phase 8)
#
# Only the extracted TEXT is kept: the PDF is read in memory and thrown away, never written to disk.
# Text is split into ~1,200 character sections with page numbers. Questions are answered from the best-matching
# sections (keyword ranking, optionally re-ranked by the local embedding model). Verified accounts only.
# PDFs are processed one at a time with page and size limits, to keep memory use low on a shared server.
# Scanned PDFs (pictures of pages, no text inside) are refused with a clear message instead of running OCR.
# ============================================================

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_DOCUMENTS_PER_USER = int(os.getenv("MAX_DOCUMENTS_PER_USER", "50"))
MAX_UPLOADS_PER_DAY = int(os.getenv("MAX_UPLOADS_PER_DAY", "20"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "300"))
MAX_DOCUMENT_CHARS = 1_000_000
CHUNK_CHARS = 1200
MIN_WORDS_PER_PAGE = 25   # below this on average the PDF is treated as scanned
_PDF_SEMAPHORE = asyncio.Semaphore(1)


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(300), nullable=False)
    filename = Column(String(255), nullable=True)
    pages = Column(Integer, nullable=False, default=0)
    words = Column(Integer, nullable=False, default=0)
    chars = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    page = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)


try:
    Base.metadata.create_all(bind=engine, tables=[UploadedDocument.__table__, DocumentChunk.__table__])
except Exception as _phase8_tables_error:
    print("Phase 8 tables note:", _phase8_tables_error)


class PdfRejected(Exception):
    """A user-facing reason the PDF cannot be used."""


_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def _clean_page_text(raw: str) -> str:
    text = (raw or "").replace("\x00", " ")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)          # re-join words split across lines
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(data: bytes) -> dict:
    """Read a PDF in memory. Returns title, pages (list of (page number, text)), words and chars.
    Raises PdfRejected with a plain reason when the file cannot be used."""
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data.startswith(b"%PDF-"):
        raise PdfRejected("This file is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PdfRejected("This PDF is password protected. Remove the password and upload it again.")
            except PdfRejected:
                raise
            except Exception:
                raise PdfRejected("This PDF is password protected. Remove the password and upload it again.")
        total = len(reader.pages)
    except PdfRejected:
        raise
    except (PdfReadError, Exception):
        raise PdfRejected("This PDF could not be read. It may be damaged.")
    if total == 0:
        raise PdfRejected("This PDF has no pages.")
    if total > MAX_PDF_PAGES:
        raise PdfRejected(f"This PDF has {total} pages. The limit is {MAX_PDF_PAGES}.")

    pages, chars = [], 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = _clean_page_text(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            room = MAX_DOCUMENT_CHARS - chars
            if room <= 0:
                break
            text = text[:room]
            pages.append((number, text))
            chars += len(text)

    words = sum(len(t.split()) for _, t in pages)
    if words / total < MIN_WORDS_PER_PAGE:
        raise PdfRejected("This looks like a scanned PDF (pictures of pages), so there is no text to read yet.")

    title = ""
    try:
        meta_title = (reader.metadata or {}).get("/Title")
        if isinstance(meta_title, str) and 8 <= len(meta_title.strip()) <= 300 and not meta_title.lower().endswith((".doc", ".docx", ".pdf", ".tex")):
            title = meta_title.strip()
    except Exception:
        pass
    if not title and pages:
        first_line = next((line.strip() for line in pages[0][1].split("\n") if len(line.strip()) >= 12), "")
        title = first_line[:200]
    return {"title": title, "pages": pages, "page_count": total, "words": words, "chars": chars}


def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Split page text into ~CHUNK_CHARS sections at paragraph or sentence breaks, keeping the page number."""
    chunks = []
    for number, text in pages:
        buffer = ""
        for part in re.split(r"(?<=[.!?])\s+|\n\n", text):
            part = part.strip()
            if not part:
                continue
            if buffer and len(buffer) + len(part) + 1 > CHUNK_CHARS:
                chunks.append((number, buffer))
                buffer = ""
            while len(part) > CHUNK_CHARS:            # a very long sentence or table row
                chunks.append((number, part[:CHUNK_CHARS]))
                part = part[CHUNK_CHARS:]
            buffer = f"{buffer} {part}".strip()
        if buffer:
            chunks.append((number, buffer))
    return chunks


_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("the a an and or of to in on for with by from is are was were be been this that these those it its as at "
            "what which who how why does do did can could should would about into than then there their they we our "
            "you your i me my not no yes".split())


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1]


def rank_chunks(question: str, chunks: list[dict], top: int = 8) -> list[dict]:
    """BM25 keyword ranking over the selected documents' sections. Falls back to the first sections when the
    question shares no words with the text (for "summarise this paper" style questions)."""
    q = list(dict.fromkeys(_terms(question)))
    if not chunks:
        return []
    docs = [_terms(c["text"]) for c in chunks]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q}
    scored = []
    for chunk, words in zip(chunks, docs):
        counts = {}
        for w in words:
            if w in df:
                counts[w] = counts.get(w, 0) + 1
        score = 0.0
        for t in q:
            tf = counts.get(t, 0)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(words) / avg))
        scored.append((score, chunk))
    if not any(score > 0 for score, _ in scored):
        return chunks[:top]
    best = [c for score, c in sorted(scored, key=lambda x: x[0], reverse=True) if score > 0][: top * 3]
    if embedding_model and len(best) > top:
        try:
            q_vec = list(embedding_model.embed([question]))[0]
            vecs = list(embedding_model.embed([c["text"][:800] for c in best]))
            best = [c for _, c in sorted(zip([compute_cosine_similarity(q_vec, v) for v in vecs], best),
                                         key=lambda x: x[0], reverse=True)]
        except Exception as error:
            print("Section re-ranking note:", type(error).__name__)
    return best[:top]


def _document_row(d: "UploadedDocument") -> dict:
    return {"id": d.id, "title": d.title, "filename": d.filename, "pages": d.pages, "words": d.words,
            "created_at": d.created_at.isoformat() if d.created_at else None}


@app.post("/documents", tags=["Library"], summary="Upload a PDF",
          description=f"Send the PDF itself as the request body (`Content-Type: application/pdf`, at most {MAX_UPLOAD_MB} MB "
                      f"and {MAX_PDF_PAGES} pages); an optional `X-Filename` header names it. Only the extracted text is "
                      "kept; the file is discarded. Scanned PDFs (no text inside) and password-protected PDFs are refused. "
                      f"At most {MAX_DOCUMENTS_PER_USER} documents per account and {MAX_UPLOADS_PER_DAY} uploads a day. "
                      "Verified accounts only.",
          responses={**_ok({"id": 1, "title": "Sleep and memory", "pages": 12, "words": 8400}),
                     **_err(413, "File larger than the upload limit.", "This PDF is larger than 20 MB."),
                     **_err(422, "Not a usable PDF (scanned, damaged, password protected, too many pages).",
                            "This looks like a scanned PDF (pictures of pages), so there is no text to read yet."),
                     **E_401, **E_403, **E_RATE})
async def upload_document(request: Request, authorization: str | None = Header(None),
                          x_filename: str | None = Header(default=None, max_length=255)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Upload the PDF file itself (Content-Type: application/pdf).")
    db = SessionLocal()
    try:
        if db.query(UploadedDocument).filter(UploadedDocument.user_id == uid).count() >= MAX_DOCUMENTS_PER_USER:
            raise HTTPException(status_code=400, detail=f"You can keep up to {MAX_DOCUMENTS_PER_USER} documents. "
                                                        "Delete some to upload more.")
    finally:
        db.close()
    day = _utcnow().strftime("%Y%m%d")
    if counter_get(f"upload:u:{uid}:{day}") >= MAX_UPLOADS_PER_DAY:
        raise HTTPException(status_code=429, detail=f"You can upload up to {MAX_UPLOADS_PER_DAY} PDFs a day.")

    data = bytearray()
    async for piece in request.stream():
        data.extend(piece)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"This PDF is larger than {MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    counter_incr(f"upload:u:{uid}:{day}", _seconds_until_utc_midnight())

    async with _PDF_SEMAPHORE:  # one PDF at a time keeps memory use predictable
        try:
            result = await asyncio.wait_for(asyncio.to_thread(extract_pdf, bytes(data)), timeout=90)
        except PdfRejected as reason:
            raise HTTPException(status_code=422, detail=str(reason))
        except asyncio.TimeoutError:
            raise HTTPException(status_code=422, detail="This PDF took too long to read. Try a smaller file.")
        finally:
            del data

    filename = re.sub(r"[^\w .()-]", "", (x_filename or "")).strip()[:255] or None
    title = re.sub(r"\s+", " ", result["title"] or (filename or "Untitled document").rsplit(".", 1)[0]).strip()[:300]
    db = SessionLocal()
    try:
        document = UploadedDocument(user_id=uid, title=title or "Untitled document", filename=filename,
                                    pages=result["page_count"], words=result["words"], chars=result["chars"])
        db.add(document)
        db.flush()
        for seq, (page, text_part) in enumerate(chunk_pages(result["pages"])):
            db.add(DocumentChunk(document_id=document.id, seq=seq, page=page, text=text_part))
        db.commit()
        db.refresh(document)
        return _document_row(document)
    finally:
        db.close()


@app.get("/documents", tags=["Library"], summary="List your uploaded documents",
         description="Newest first, with page and word counts. Verified accounts only.",
         responses={**_ok({"documents": [{"id": 1, "title": "Sleep and memory", "pages": 12, "words": 8400}],
                           "limit": MAX_DOCUMENTS_PER_USER}), **E_401, **E_403})
def list_documents(request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        rows = db.query(UploadedDocument).filter(UploadedDocument.user_id == uid).order_by(UploadedDocument.id.desc()).all()
        return {"documents": [_document_row(d) for d in rows], "limit": MAX_DOCUMENTS_PER_USER}
    finally:
        db.close()


@app.get("/documents/{document_id}", tags=["Library"], summary="One uploaded document",
         description="Details plus the start of the extracted text, so you can check it was read correctly. "
                     "Someone else's document returns 404. Verified accounts only.",
         responses={**_ok({"id": 1, "title": "Sleep and memory", "preview": "Abstract. We studied..."}), **E_401, **E_403})
def get_document(document_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        d = db.query(UploadedDocument).filter(UploadedDocument.id == document_id, UploadedDocument.user_id == uid).first()
        if not d:
            raise HTTPException(status_code=404, detail="Document not found.")
        first = (db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id)
                 .order_by(DocumentChunk.seq).limit(3).all())
        return {**_document_row(d), "preview": " ".join(c.text for c in first)[:1500]}
    finally:
        db.close()


@app.delete("/documents/{document_id}", tags=["Library"], summary="Delete an uploaded document",
            description="Deletes the document and all its text at once. Verified accounts only.",
            responses={**_ok({"message": "Document deleted."}), **E_401, **E_403})
def delete_document(document_id: int, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        d = db.query(UploadedDocument).filter(UploadedDocument.id == document_id, UploadedDocument.user_id == uid).first()
        if not d:
            raise HTTPException(status_code=404, detail="Document not found.")
        db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).delete(synchronize_session=False)
        db.delete(d)
        db.commit()
        return {"message": "Document deleted."}
    finally:
        db.close()


class DocumentAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[int] = Field(min_length=1, max_length=5)
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=20)


DOCUMENT_ASK_PROMPT = (
    "You answer questions about the user's uploaded documents using ONLY the numbered excerpts supplied. "
    "Cite excerpts as [1], [2] matching their excerpt_id. If the excerpts do not contain the answer, say so plainly and "
    "suggest what to ask instead; never invent content, numbers or references. Excerpts are partial: do not claim a "
    "document says nothing about a topic unless the excerpts make that clear. Write the 'answer' in markdown. "
    "Return a valid JSON object with 'answer' and 'citations' (list of excerpt_id integers)."
)

DOCUMENT_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "citations"],
    "additionalProperties": False,
}


@app.post("/documents/ask", tags=["AI"], summary="Ask about your uploaded documents",
          description="Answers from the full text of 1 to 5 of your uploaded documents. The best-matching sections (up to 8) "
                      "are sent to the AI; the answer cites them as `[n]`, and `citations` gives each one's document and "
                      "page. One AI call, counted toward the daily AI caps. Verified accounts only.",
          responses={**_ok({"answer": "The trial found faster sleep onset [1].",
                            "citations": [{"n": 1, "document_id": 1, "title": "Sleep and memory", "page": 4}]}),
                     **E_401, **E_403, **E_RATE, **E_AI})
async def ask_documents(body: DocumentAskRequest, request: Request, authorization: str | None = Header(None)):
    uid = resolve_user_id(request, authorization, require_verified=True)
    ids = list(dict.fromkeys(body.document_ids))
    db = SessionLocal()
    try:
        docs = {d.id: d for d in db.query(UploadedDocument).filter(UploadedDocument.user_id == uid,
                                                                   UploadedDocument.id.in_(ids)).all()}
        if len(docs) != len(ids):
            raise HTTPException(status_code=404, detail="Document not found.")
        chunks = [{"document_id": c.document_id, "page": c.page, "text": c.text}
                  for c in db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(ids))
                  .order_by(DocumentChunk.document_id, DocumentChunk.seq).all()]
    finally:
        db.close()
    if not chunks:
        raise HTTPException(status_code=400, detail="These documents have no readable text.")

    selected = rank_chunks(body.question, chunks)
    excerpts = [{"excerpt_id": i, "document": docs[c["document_id"]].title[:200], "page": c["page"], "text": c["text"]}
                for i, c in enumerate(selected, start=1)]
    history = [turn.model_dump() for turn in body.conversation_history[-2:]]
    cache_key = "docask:" + hashlib.sha256(json.dumps(
        [uid, body.question.strip().lower(), [(e["document"], e["page"], e["text"][:200]) for e in excerpts], history]
    ).encode("utf-8")).hexdigest()
    cached = get_from_search_cache(cache_key)
    if cached is not None:
        return cached

    gemini_client, groq_client = get_ai_clients()
    enforce_ai_quota(request, uid)
    prefix = ""
    if history:
        prefix = "Previous conversation:\n" + "\n".join(f"Q: {t['question'][:150]}\nA: {t['answer'][:250]}" for t in history) + "\n\n"
    user_prompt = f"{prefix}Question: {body.question}\n\nExcerpts:\n{json.dumps(excerpts, indent=2)}"
    retry_prompt = (f"Question: {body.question}\n\nExcerpts:\n"
                    + json.dumps([{**e, "text": e["text"][:500]} for e in excerpts[:4]]) + "\n\nKeep the answer short.")
    try:
        result = await asyncio.to_thread(generate_structured_ai, gemini_client, groq_client, DOCUMENT_ASK_PROMPT,
                                         user_prompt, retry_prompt, DOCUMENT_ASK_SCHEMA, "answer")
    except Exception as error:
        print("Document question failed:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)

    by_id = {e["excerpt_id"]: e for e in excerpts}
    cited, seen = [], set()
    for n in result.get("citations") or []:
        if isinstance(n, int) and not isinstance(n, bool) and n in by_id and n not in seen:
            seen.add(n)
            chunk = selected[n - 1]
            cited.append({"n": n, "document_id": chunk["document_id"], "title": docs[chunk["document_id"]].title,
                          "page": chunk["page"]})
    response = {"answer": result["answer"].strip(), "citations": sorted(cited, key=lambda c: c["n"]),
                "sections_used": len(excerpts)}
    set_in_search_cache(cache_key, response, ttl=7200)
    return response


# ============================================================
# CITATION MAP (Phase 8): what a paper cites and what cites it, from OpenAlex
# ============================================================

GRAPH_SIDE_LIMIT = 15


def _graph_node(work: dict, kind: str) -> dict:
    wid = str(work.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
    return {
        "id": wid,
        "title": (work.get("title") or work.get("display_name") or "Untitled")[:300],
        "year": work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count") if isinstance(work.get("cited_by_count"), int) else None,
        "doi": work.get("doi"),
        "kind": kind,
    }


@app.get("/graph", tags=["Search"], summary="Citation map for a paper",
         description=f"Give a `doi`. Returns the paper, up to {GRAPH_SIDE_LIMIT} papers it cites (`reference`) and up to "
                     f"{GRAPH_SIDE_LIMIT} most-cited papers that cite it (`citing`), from OpenAlex. Cached for 24 hours. "
                     "Open to guests.",
         responses={**_ok({"seed": {"id": "W1", "title": "Paper", "kind": "seed"}, "nodes": [], "edges": [],
                           "reference_total": 40, "citing_total": 120}),
                    **_err(404, "Unknown DOI.", "This paper was not found in OpenAlex.")})
async def citation_graph(doi: str = Query(..., max_length=255)):
    from urllib.parse import quote

    bare = normalize_doi(doi)
    if not bare or not DOI_PATTERN.fullmatch(bare):
        raise HTTPException(status_code=400, detail="That does not look like a valid DOI.")
    cache_key = "graph:" + hashlib.sha256(bare.encode("utf-8")).hexdigest()
    cached = get_from_search_cache(cache_key)
    if cached is not None:
        return cached
    headers = {"User-Agent": f"{APP_UA_NAME}/1.0 (mailto:{CONTACT_EMAIL})"}
    select = "id,title,display_name,publication_year,cited_by_count,doi,referenced_works"
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        response = await client.get(f"https://api.openalex.org/works/doi:{quote(bare, safe='/()._-;:')}",
                                    params={"select": select, "mailto": CONTACT_EMAIL})
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="This paper was not found in OpenAlex.")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="The citation data could not be loaded. Please try again.")
        seed_work = response.json()
        seed = _graph_node(seed_work, "seed")
        ref_ids = [str(r).rstrip("/").rsplit("/", 1)[-1] for r in (seed_work.get("referenced_works") or [])]
        ref_ids = [r for r in ref_ids if re.fullmatch(r"W\d+", r)]

        async def fetch(params):
            try:
                r = await client.get("https://api.openalex.org/works", params={**params, "select": select.replace(",referenced_works", ""),
                                                                                "mailto": CONTACT_EMAIL})
                return r.json() if r.status_code == 200 else {}
            except Exception:
                return {}

        refs_data, citing_data = await asyncio.gather(
            fetch({"filter": "openalex_id:" + "|".join(ref_ids[:50]), "per-page": 50}) if ref_ids else asyncio.sleep(0, result={}),
            fetch({"filter": f"cites:{seed['id']}", "sort": "cited_by_count:desc", "per-page": GRAPH_SIDE_LIMIT}),
        )
    references = sorted((_graph_node(w, "reference") for w in refs_data.get("results") or []),
                        key=lambda n: n["cited_by_count"] or 0, reverse=True)[:GRAPH_SIDE_LIMIT]
    citing = [_graph_node(w, "citing") for w in (citing_data.get("results") or [])][:GRAPH_SIDE_LIMIT]
    nodes = [seed] + references + citing
    edges = [{"from": seed["id"], "to": n["id"]} for n in references] + [{"from": n["id"], "to": seed["id"]} for n in citing]
    data = {"seed": seed, "nodes": nodes, "edges": edges, "reference_total": len(ref_ids),
            "citing_total": (citing_data.get("meta") or {}).get("count", len(citing))}
    set_in_search_cache(cache_key, data, ttl=86400)
    return data
