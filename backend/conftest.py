"""Test bootstrap: runs before any test module imports main.
Everything uses a throwaway SQLite database and never reads the developer's real backend/.env."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="scholarpulse-tests-")
os.environ.update(
    {
        # Never read the developer's real backend/.env (real API keys, real SMTP credentials)
        "ENV_FILE": os.path.join(_TMP, "no-such.env"),
        "AUTH_SECRET": "t" * 48,
        "CORS_ORIGINS": "http://localhost:3000",
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_TMP}/test.db",  # set TEST_DATABASE_URL to run against Postgres
        "APP_URL": "http://localhost:3000",
        "COOKIE_SECURE": "false",  # the test client talks plain http
        "DEV_MODE": "false",
        "ENABLE_DOCS": "false",
        "ENABLE_RERANKING": "false",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "TRUSTED_PROXY_COUNT": "0",
        "GEMINI_API_KEY": "",
        "GROQ_API_KEY": "",
    }
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

