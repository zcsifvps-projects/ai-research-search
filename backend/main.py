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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
CORE_API_KEY = os.getenv("CORE_API_KEY", "").strip()

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
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "ScholarPulse Research").strip()


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
        if not user:
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
      <h2 style="color: #7C2A2A; margin-top: 0;">ScholarPulse — Verify Your Account</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Please verify your email address to unlock research bookmarks and synchronization.</p>
      <div style="text-align: center; margin: 30px 0;">
        <a href="{verify_url}" style="background-color: #7C2A2A; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 14px; display: inline-block;">Verify Email Address</a>
      </div>
      <p style="color: #6E6459; font-size: 12px;">Or copy this URL into your browser:</p>
      <p style="color: #6E6459; font-size: 11px; word-break: break-all;"><a href="{verify_url}" style="color: #7C2A2A;">{verify_url}</a></p>
    </div>
    """
    text = f"{greeting}\n\nPlease verify your ScholarPulse account at:\n{verify_url}\n"
    return send_email(to_email, "Verify your ScholarPulse account", html, text)


def send_welcome_email(to_email: str, full_name: str | None = None):
    greeting = f"Welcome {full_name}!" if full_name else "Welcome to ScholarPulse!"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">{greeting}</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Your email is verified and your account is active.</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Search five academic databases at once: OpenAlex, Semantic Scholar, arXiv, Crossref and CORE.</p>
      <div style="text-align: center; margin: 30px 0;">
        <a href="{APP_URL}" style="background-color: #7C2A2A; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 14px; display: inline-block;">Start Researching</a>
      </div>
    </div>
    """
    text = f"{greeting}\n\nYour account is active. Explore research at {APP_URL}\n"
    return send_email(to_email, "Welcome to ScholarPulse Research Engine", html, text)


def send_account_exists_email(to_email: str, full_name: str | None = None):
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">ScholarPulse — You already have an account</h2>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="color: #221D18; font-size: 14px; line-height: 1.6;">Someone tried to create a ScholarPulse account with this email address, which is already registered. If that was you, just <a href="{APP_URL}" style="color: #7C2A2A;">sign in</a> (or use "Forgot password" on the sign-in form). If it wasn't you, you can ignore this message; nothing was changed.</p>
    </div>
    """
    text = f"{greeting}\n\nSomeone tried to create a ScholarPulse account with this email, which is already registered. If that was you, sign in at {APP_URL} (or use Forgot password). If not, ignore this message.\n"
    return send_email(to_email, "You already have a ScholarPulse account", html, text)


def send_password_reset_email(to_email: str, token: str, full_name: str | None = None):
    reset_url = f"{APP_URL}/?reset_token={token}"
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #E4DCCB; border-radius: 12px; background-color: #FAF6EF;">
      <h2 style="color: #7C2A2A; margin-top: 0;">ScholarPulse — Reset Password</h2>
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
    return send_email(to_email, "Reset your ScholarPulse password", html, text)


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
            "/search": (60, 60),
            "/auth/login": (10, 60),
            "/auth/register": (10, 60),
            "/auth/forgot-password": (5, 60),
            "/auth/reset-password": (10, 60),
            "/auth/resend-verification": (5, 60),
            "/auth/verify-email": (10, 60),
            "/auth/": (30, 60),
            "/bookmarks": (60, 60),
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
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
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
}
EXAMPLE_BOOKMARK = {
    "id": 3, "user_id": 7, "title": EXAMPLE_PAPER["title"], "authors": EXAMPLE_PAPER["authors"],
    "abstract": EXAMPLE_PAPER["abstract"], "publication_year": 2024, "source": "Journal of Sleep Research",
    "doi": EXAMPLE_PAPER["doi"], "created_at": "2026-10-14T09:30:00",
}

API_DESCRIPTION = """
ScholarPulse searches five academic databases at once (**OpenAlex, Semantic Scholar, arXiv, Crossref, CORE**) and
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
    {"name": "Search", "description": "Federated search across five academic databases."},
    {"name": "AI", "description": "Answers and paper analysis grounded in abstracts. Open to guests, rate limited."},
    {"name": "History", "description": "Past questions and answers, per signed-in user or per guest `session_id`."},
    {"name": "Library", "description": "Saved papers. Requires a verified account."},
]

PROTECTED_OPERATIONS = {
    ("get", "/auth/me"), ("post", "/auth/change-password"), ("delete", "/auth/delete-account"), ("post", "/auth/logout-all"),
    ("post", "/history/sync"), ("post", "/bookmarks"), ("get", "/bookmarks"), ("delete", "/bookmarks/{bookmark_id}"),
}

app = FastAPI(
    title="ScholarPulse API",
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
    allow_headers=["Content-Type", "Authorization"],
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
    if not GEMINI_API_KEY:
        return None
    return genai.Client(api_key=GEMINI_API_KEY)


def get_groq_client():
    if not GROQ_API_KEY:
        return None
    return Groq(api_key=GROQ_API_KEY)


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


class DeepDiveRequest(BaseModel):
    title: str = Field(max_length=500, examples=[EXAMPLE_PAPER["title"]])
    abstract: str = Field(max_length=10000, examples=[EXAMPLE_PAPER["abstract"]])


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
    responses=_ok({"message": "ScholarPulse AI Research API is running"}),
)
def root():
    return {"message": "ScholarPulse AI Research API is running"}


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

        if password_needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(req.password)
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
        **_ok({"token": "<same value as the cookie>", "user": {**EXAMPLE_USER, "is_verified": True}, "message": "Email verified successfully! Welcome to ScholarPulse."}),
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
            "message": "Email verified successfully! Welcome to ScholarPulse.",
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
    description="Your saved papers, newest first. Verified accounts only.",
    responses={**_ok({"bookmarks": [EXAMPLE_BOOKMARK]}), **E_401, **E_403},
)
def list_bookmarks(
    request: Request,
    authorization: str | None = Header(None),
):
    uid = resolve_user_id(request, authorization, require_verified=True)
    db = SessionLocal()
    try:
        items = (
            db.query(Bookmark)
            .filter(Bookmark.user_id == uid)
            .order_by(Bookmark.created_at.desc())
            .all()
        )
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
):
    """Common paper shape. is_open_access is True/False when the source states it, None when unknown."""
    return {
        "title": (title or "Untitled").strip(),
        "authors": authors or [],
        "abstract": abstract.strip() if abstract else None,
        "publication_year": year,
        "description": source or "Unknown source",
        "doi": doi,
        "is_open_access": is_open_access,
    }


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

async def search_openalex(client, q, year_min=None, year_max=None, oa_only=False):
    url = "https://api.openalex.org/works"
    params = {
        "search": q.rstrip("?"),
        "per-page": 15,
        "mailto": CONTACT_EMAIL,
    }
    # Filters are applied by OpenAlex itself, so the result slots are not wasted on papers we then discard.
    filters = []
    if year_min is not None:
        filters.append(f"publication_year:>{year_min - 1}")
    if year_max is not None:
        filters.append(f"publication_year:<{year_max + 1}")
    if oa_only:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    headers = {
        "User-Agent": f"ScholarPulse/1.0 (mailto:{CONTACT_EMAIL})"
    }

    try:
        response = await client.get(url, params=params, headers=headers, timeout=10.0)
        if response.status_code == 429:
            print("OpenAlex rate limited (429). Skipping.")
            return []
        response.raise_for_status()
        data = response.json()
        results = []

        for work in data.get("results", []):
            primary_location = work.get("primary_location") or {}
            source = primary_location.get("source") or {}
            authors = [
                a.get("author", {}).get("display_name")
                for a in work.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]
            # No abstract means None. Never invent one: the AI must only be given real evidence.
            abstract = get_abstract(work)

            results.append(
                normalize_result(
                    title=work.get("title"),
                    authors=authors,
                    abstract=abstract,
                    year=work.get("publication_year"),
                    source=source.get("display_name") or "OpenAlex",
                    doi=work.get("doi"),
                    is_open_access=bool((work.get("open_access") or {}).get("is_oa")),
                )
            )

        return results
    except Exception as error:
        print("OpenAlex search note:", error)
        return []


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================

async def search_semantic_scholar(client, q, year_min=None, year_max=None, oa_only=False):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": q.rstrip("?"),
        "limit": 10,
        "fields": "title,authors,abstract,year,externalIds,venue,isOpenAccess,openAccessPdf",
    }
    if year_min is not None or year_max is not None:
        # Semantic Scholar range syntax: "2023-", "-2020", "2020-2023"
        params["year"] = f"{year_min if year_min is not None else ''}-{year_max if year_max is not None else ''}"
    if oa_only:
        params["openAccessPdf"] = ""  # flag parameter: only papers with a public PDF

    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    try:
        response = await client.get(url, params=params, headers=headers)
        if response.status_code == 429:
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

            results.append(
                normalize_result(
                    title=paper.get("title"),
                    authors=authors,
                    abstract=abstract,
                    year=paper.get("year"),
                    source=paper.get("venue") or "Semantic Scholar",
                    doi=external_ids.get("DOI"),
                    is_open_access=bool(paper.get("isOpenAccess") or paper.get("openAccessPdf")),
                )
            )

        return results

    except Exception as error:
        print(f"Semantic Scholar search failed: {error}")
        return []


# ============================================================
# ARXIV (every arXiv paper is open access)
# ============================================================

async def search_arxiv(client, q, year_min=None, year_max=None, oa_only=False):
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
                )
            )
        return results
    except Exception as error:
        print("arXiv search note:", error)
        return []


# ============================================================
# CROSSREF (does not say whether a paper is open access: is_open_access stays None)
# ============================================================

async def search_crossref(client, q, year_min=None, year_max=None, oa_only=False):
    url = "https://api.crossref.org/works"
    params = {
        "query": q.rstrip("?"),
        "rows": 10,
        "select": "title,author,abstract,published-print,published-online,DOI,container-title",
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
            print("Crossref rate limit reached (429). Skipping.")
            return []
        response.raise_for_status()
        data = response.json()
        items = data.get("message", {}).get("items", [])
        results = []
        for work in items:
            titles = work.get("title") or []
            title = titles[0] if titles else "Untitled"
            authors = []
            for author in (work.get("author") or []):
                name = f"{author.get('given', '') or ''} {author.get('family', '') or ''}".strip()
                if name:
                    authors.append(name)
            abstract = work.get("abstract")
            if not abstract:
                continue
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()
            if not abstract:
                continue
            year = None
            for date_field in ("published-print", "published-online"):
                date_parts = (work.get(date_field) or {}).get("date-parts") or []
                if date_parts and date_parts[0]:
                    year = date_parts[0][0]
                    break
            container_titles = work.get("container-title") or []
            source = container_titles[0] if container_titles else "Crossref"
            results.append(
                normalize_result(
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    year=year,
                    source=source,
                    doi=work.get("DOI"),
                    is_open_access=None,
                )
            )
        return results
    except Exception as error:
        print(f"Crossref search failed: {error}")
        return []


# ============================================================
# CORE (an aggregator of open-access repositories: every record is open access)
# ============================================================

async def search_core(client, q, year_min=None, year_max=None, oa_only=False):
    if not CORE_API_KEY:
        print("CORE_API_KEY not configured. Skipping CORE.")
        return []
    url = "https://api.core.ac.uk/v3/search/works"
    headers = {"Authorization": f"Bearer {CORE_API_KEY}"}
    query = q.rstrip("?")
    if year_min is not None:
        query = f"({query}) AND yearPublished>={year_min}"
    if year_max is not None:
        query = f"({query}) AND yearPublished<={year_max}"
    params = {"q": query, "limit": 10}
    try:
        response = await client.get(url, params=params, headers=headers)
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
                )
            )
        return results
    except Exception as error:
        print(f"CORE search failed: {error}")
        return []


# ============================================================
# DOI / TITLE DEDUPLICATION
# ============================================================

def deduplicate_results(results):
    """Drop duplicates (same DOI, else same title) but merge what each source knows: if any source says
    the paper is open access it is, and a missing abstract/year is filled from the duplicate."""
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
            continue
        unique_results.append(result)
        if doi:
            by_doi[doi] = result
        elif title:
            by_title[title] = result
    return unique_results


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


def generate_ask_cache_key(question: str, papers: list[dict], conversation_history: list[dict]) -> str:
    raw = json.dumps({
        "q": question.strip().lower(),
        "p": [(p.get("title", ""), (p.get("abstract") or "")[:200]) for p in papers],
        "h": [(h.get("question", ""), h.get("answer", "")) for h in conversation_history],
    }, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# SEARCH ENDPOINT
# ============================================================

@app.get(
    "/search",
    tags=["Search"],
    summary="Search five academic databases",
    description="Searches OpenAlex, Semantic Scholar, arXiv, Crossref and CORE (CORE needs a server-side key), merges duplicates "
                "by DOI or title, and ranks by semantic similarity when the embedding model is enabled. Open to guests.\n\n"
                "`year_min` / `year_max` restrict the publication year (inclusive). `open_access_only=true` returns only papers a "
                "source confirmed as open access (`is_open_access: true`). Papers without an abstract may be returned with "
                "`abstract: null`; they are never used as evidence by `/ask`.",
    responses={
        **_ok({"query": "sleep and memory", "count": 1, "source_counts": {"OpenAlex": 1}, "results": [EXAMPLE_PAPER]}),
        **_err(400, "Empty query.", "Query cannot be empty."),
        **E_RATE,
    },
)
async def search(
    q: str = Query(..., max_length=300),
    year_min: int | None = Query(default=None, ge=0, le=3000),
    year_max: int | None = Query(default=None, ge=0, le=3000),
    open_access_only: bool = False,
):

    if not q.strip():

        raise HTTPException(
            status_code=400,
            detail=(
                "Search query cannot be empty."
            ),
        )

    query = q.strip()
    cache_key = f"{query.lower()}:{year_min}:{year_max}:{open_access_only}"

    cached_response = get_from_search_cache(cache_key)
    if cached_response is not None:
        return cached_response

    academic_keyword = clean_academic_query(query)

    timeout = httpx.Timeout(
        30.0,
        connect=10.0,
    )

    try:

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": f"ScholarPulse/1.0 (mailto:{CONTACT_EMAIL})"
            },
        ) as client:

            # Year and open-access filters are sent to each source so its result slots go to matching papers.
            f = {"year_min": year_min, "year_max": year_max, "oa_only": open_access_only}
            tasks = [
                search_openalex(client, academic_keyword, **f),
                search_semantic_scholar(client, academic_keyword, **f),
                search_arxiv(client, academic_keyword, **f),
                search_core(client, academic_keyword, **f),
            ]
            if not open_access_only:
                # Crossref cannot tell us whether a paper is open access, so it is skipped for OA-only searches.
                tasks.append(search_crossref(client, academic_keyword, **f))

            # If user query has distinct keywords, also search raw query
            if academic_keyword != query:
                tasks.extend([
                    search_semantic_scholar(client, query, **f),
                    search_openalex(client, query, **f),
                    search_arxiv(client, query, **f),
                ])

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
    # Filters (Year and Open Access)
    # --------------------------------------------------------
    # Safety net: enforce the filters again on the merged list (sources can be loose about dates).
    if year_min is not None or year_max is not None or open_access_only:
        filtered = []
        for r in results:
            y = r.get("publication_year")
            if year_min is not None and (y is None or y < year_min):
                continue
            if year_max is not None and (y is None or y > year_max):
                continue
            if open_access_only and r.get("is_open_access") is not True:
                continue  # only papers a source confirmed as open access
            filtered.append(r)
        results = filtered

    # --------------------------------------------------------
    # Semantic Re-Ranking (FastEmbed Local Embeddings)
    # --------------------------------------------------------
    results = rerank_papers_by_similarity(
        query,
        results,
    )

    source_counts = {}

    for result in results:

        source = (
            result.get(
                "description"
            )
            or "Unknown source"
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

    response_data = {
        "query": query,
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
    groq_client = get_groq_client() if not gemini_client else None
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
    if not isinstance(result, dict) or not isinstance(result.get(required_key), str) or not result[required_key].strip():
        raise ValueError(f"AI response is missing '{required_key}'.")
    return result


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
                response_schema=response_schema,
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
        )
        return completion.choices[0].message.content

    call = call_gemini if gemini_client else call_groq
    try:
        return parse_ai_json(call(user_prompt), required_key)
    except Exception as first_error:
        print("AI first attempt failed, retrying with a shorter prompt:", first_error)
        return parse_ai_json(call(retry_prompt), required_key)


# ============================================================
# AI RESEARCH QUESTION (MULTI-TURN / FOLLOW-UP CONTEXT)
# ============================================================

ASK_SYSTEM_PROMPT = (
    "You are an expert AI research scientist synthesizing academic papers. "
    "Answer ONLY from the supplied papers. If they do not contain enough information to answer, say so plainly; "
    "never invent findings, statistics, papers or citations. "
    "Cite papers using [1], [2], etc. matching their paper_id. "
    "Return a valid JSON object with 'answer' (string in markdown) and 'citations' (list of {paper_id, title})."
)

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
    },
    "required": ["answer", "citations"],
    "additionalProperties": False,
}


def sanitize_paper_for_storage(paper: dict) -> dict:
    """Keep only the fields the UI needs, with bounded sizes, before persisting client-supplied papers."""
    authors = paper.get("authors")
    year = paper.get("publication_year")
    score = paper.get("similarity_score")
    return {
        "title": str(paper.get("title") or "Untitled")[:500],
        "authors": [str(a)[:200] for a in authors[:50]] if isinstance(authors, list) else [],
        "description": str(paper.get("description") or "")[:200],
        "publication_year": year if isinstance(year, int) else None,
        "doi": str(paper["doi"])[:255] if paper.get("doi") else None,
        "abstract": str(paper["abstract"])[:5000] if paper.get("abstract") else None,
        "similarity_score": score if isinstance(score, (int, float)) else None,
    }


@app.post(
    "/ask",
    tags=["AI"],
    summary="Ask a question about papers",
    description="Send the question and the papers from `/search` (up to 50; the first 6 with an abstract are used). The answer is written "
                "**only from those abstracts** and cites them as `[1]`, `[2]`... by `paper_id` (their position in `results`, starting at 1). "
                "Citations the model invents are dropped. Include earlier turns in `conversation_history` for follow-ups. "
                "Identity comes from the cookie only: a `user_id` in the body is ignored. Signed-in answers are saved to your account, "
                "guest answers to `session_id`. If the first AI attempt fails it is retried once with fewer papers.",
    responses={
        **_ok({"answer": "One night of sleep loss reduces next-day recall [1].", "citations": [{"paper_id": 1, "title": EXAMPLE_PAPER["title"]}]}),
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
            ASK_SYSTEM_PROMPT,
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
        result = {"answer": result["answer"], "citations": valid_citations}

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
# PER-PAPER DEEP DIVE
# ============================================================

DEEP_DIVE_SYSTEM_PROMPT = (
    "You are an expert research analyst. Using ONLY the title and abstract provided, write a concise markdown "
    "deep dive with these sections: Summary, Key findings, Methods, Limitations, Why it matters. "
    "If the abstract does not state something, say it is not stated; never invent details. "
    "Return a valid JSON object with a single key 'deep_dive' (string in markdown)."
)

DEEP_DIVE_SCHEMA = {
    "type": "object",
    "properties": {"deep_dive": {"type": "string"}},
    "required": ["deep_dive"],
    "additionalProperties": False,
}


@app.post(
    "/deep-dive",
    tags=["AI"],
    summary="Analyse one paper",
    description="Markdown analysis (summary, key findings, methods, limitations, why it matters) written only from the title and abstract "
                "you send. With no abstract the endpoint answers without calling the AI. Open to guests.",
    responses={**_ok({"deep_dive": "## Summary\n..."}), **E_RATE, **E_AI},
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
            "Open the paper through its DOI to read the full text."
        }

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
        return {"deep_dive": result["deep_dive"]}
    except Exception as error:
        print("Deep dive failed:", error)
        raise HTTPException(status_code=502, detail=GENERIC_AI_ERROR)
