"""Security and behaviour tests for the ScholarPulse API.

Run from backend/:   pytest -q
Uses a throwaway SQLite database and fake AI clients: no Postgres, Redis or API keys needed.
"""
import os
import subprocess
import sys
import types as pytypes

import pytest
from fastapi.testclient import TestClient

import main

PASSWORD = "correct-horse-battery"
PAPERS = [
    {"title": "Paper One", "authors": ["A. Author"], "description": "OpenAlex", "publication_year": 2021,
     "doi": "10.1/one", "abstract": "Sleep deprivation impairs memory consolidation."},
    {"title": "Paper Two", "authors": ["B. Author"], "description": "arXiv", "publication_year": 2022,
     "doi": None, "abstract": "Regular exercise improves sleep quality."},
]


# ---------------------------------------------------------------- helpers / fakes

class FakeGemini:
    def __init__(self, text=None, error=None):
        self.calls = []
        self._text, self._error = text, error
        self.models = pytypes.SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return pytypes.SimpleNamespace(text=self._text)


class FakeGroq:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = pytypes.SimpleNamespace(completions=pytypes.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = pytypes.SimpleNamespace(content=outcome)
        return pytypes.SimpleNamespace(choices=[pytypes.SimpleNamespace(message=message)])


GOOD_JSON = '{"answer": "Sleep matters [1].", "citations": [{"paper_id": 1, "title": "x"}, {"paper_id": 99, "title": "fake"}]}'


def limiter():
    if main.app.middleware_stack is None:  # Starlette builds the stack lazily on the first request
        main.app.middleware_stack = main.app.build_middleware_stack()
    layer = main.app.middleware_stack
    while layer is not None and not isinstance(layer, main.RateLimiterMiddleware):
        layer = getattr(layer, "app", None)
    assert layer is not None, "rate limiter not installed"
    return layer


OUTBOX = []


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Fresh limiter, caches and counters per test; emails are captured instead of sent."""
    limiter().request_history.clear()
    main.IN_MEMORY_CACHE.clear()
    main._COUNTERS.clear()
    OUTBOX.clear()
    monkeypatch.setattr(main, "send_verification_email", lambda to, token, name=None: OUTBOX.append({"kind": "verify", "to": to, "token": token}) or True)
    monkeypatch.setattr(main, "send_password_reset_email", lambda to, token, name=None: OUTBOX.append({"kind": "reset", "to": to, "token": token}) or True)
    monkeypatch.setattr(main, "send_account_exists_email", lambda to, name=None: OUTBOX.append({"kind": "exists", "to": to}) or True)
    monkeypatch.setattr(main, "send_welcome_email", lambda to, name=None: OUTBOX.append({"kind": "welcome", "to": to}) or True)
    yield
    limiter().request_history.clear()


def emailed_token(kind, email):
    return [m["token"] for m in OUTBOX if m["kind"] == kind and m["to"] == email][-1]


def kinds(email=None):
    return [m["kind"] for m in OUTBOX if email is None or m["to"] == email]


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


_counter = {"n": 0}


def make_user(verified=True, password=PASSWORD):
    """Register through the API (so the cookie is real). Returns (client, user_id, email)."""
    _counter["n"] += 1
    email = f"user{_counter['n']}@example.com"
    c = TestClient(main.app)
    limiter().request_history.clear()
    r = c.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    assert "set-cookie" not in r.headers  # registration never signs you in
    if verified:
        r = c.post("/auth/verify-email", json={"token": emailed_token("verify", email)})  # the emailed link signs you in
    else:
        r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    uid = r.json()["user"]["id"]
    limiter().request_history.clear()
    return c, uid, email


def use_fake_ai(monkeypatch, gemini=None, groq=None):
    monkeypatch.setattr(main, "get_gemini_client", lambda: gemini)
    monkeypatch.setattr(main, "get_groq_client", lambda: groq)


def ask_body(question="Does sleep affect memory?", **extra):
    return {"question": question, "results": PAPERS, "session_id": "guest-" + __import__("hashlib").sha1(question.encode()).hexdigest()[:24], **extra}


# ---------------------------------------------------------------- authentication on every protected route

PROTECTED = [
    ("get", "/auth/me", None),
    ("post", "/auth/change-password", {"current_password": "x" * 8, "new_password": "y" * 8}),
    ("delete", "/auth/delete-account", {"password": "whatever1"}),
    ("post", "/history/sync", {"session_id": "a" * 16}),
    ("get", "/bookmarks", None),
    ("post", "/bookmarks", {"title": "T"}),
    ("delete", "/bookmarks/1", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_routes_need_a_cookie(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = client.request(method.upper(), path, **kwargs)
    assert r.status_code == 401, (path, r.status_code, r.text)


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_garbage_and_forged_tokens_are_rejected(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    for bad in ("garbage", "abc.def", main.create_access_token(1, "a@b.c") + "x"):
        client.cookies.set("access_token", bad)
        assert client.request(method.upper(), path, **kwargs).status_code == 401


def test_expired_token_is_rejected(client, monkeypatch):
    c, uid, email = make_user()
    real_time = main.time.time
    monkeypatch.setattr(main.time, "time", lambda: real_time() + 31 * 24 * 3600)
    assert c.get("/auth/me").status_code == 401


def test_unverified_account_gets_403_on_library_and_sync():
    c, _, _ = make_user(verified=False)
    assert c.get("/auth/me").status_code == 200  # signed in, just not verified
    assert c.get("/bookmarks").status_code == 403
    assert c.post("/bookmarks", json={"title": "T"}).status_code == 403
    assert c.delete("/bookmarks/1").status_code == 403
    assert c.post("/history/sync", json={"session_id": "a" * 16}).status_code == 403


def test_verified_account_can_use_library():
    c, _, _ = make_user()
    r = c.post("/bookmarks", json={"title": "Saved paper", "doi": "10.1/x"})
    assert r.status_code == 200
    assert [b["title"] for b in c.get("/bookmarks").json()["bookmarks"]] == ["Saved paper"]


def test_bookmarks_are_isolated_between_users():
    a, _, _ = make_user()
    b, _, _ = make_user()
    bookmark_id = a.post("/bookmarks", json={"title": "Private"}).json()["id"]
    assert b.get("/bookmarks").json()["bookmarks"] == []
    assert b.delete(f"/bookmarks/{bookmark_id}").status_code == 404
    assert len(a.get("/bookmarks").json()["bookmarks"]) == 1


# ---------------------------------------------------------------- history scoping

def test_anonymous_cannot_read_or_delete_an_accounts_history(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    c, uid, _ = make_user()
    body = ask_body("Private account question")
    assert c.post("/ask", json=body).status_code == 200

    anon = TestClient(main.app)
    sid = body["session_id"]
    assert anon.get(f"/history/{sid}").json() == {"history": []}
    anon.delete(f"/history/{sid}")
    assert len(c.get(f"/history/{sid}").json()["history"]) == 1  # still there


def test_user_cannot_see_another_users_history(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    a, _, _ = make_user()
    b, _, _ = make_user()
    a.post("/ask", json=ask_body("Only for user A"))
    assert b.get("/history/anything").json()["history"] == []


def test_guest_history_is_scoped_to_its_session(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    guest = TestClient(main.app)
    body = ask_body("Guest question")
    guest.post("/ask", json=body)
    assert len(guest.get(f"/history/{body['session_id']}").json()["history"]) == 1
    assert guest.get("/history/some-other-session").json()["history"] == []
    assert guest.get("/history/anonymous").json()["history"] == []


def test_delete_single_history_entry_is_scoped(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    a, _, _ = make_user()
    b, _, _ = make_user()
    a.post("/ask", json=ask_body("Question one aaaa"))
    a.post("/ask", json=ask_body("Question two bbbb"))
    entries = a.get("/history/x").json()["history"]
    assert len(entries) == 2
    assert b.delete(f"/history/entry/{entries[0]['id']}").status_code == 404
    assert a.delete(f"/history/entry/{entries[0]['id']}").status_code == 200
    assert len(a.get("/history/x").json()["history"]) == 1  # deleting one no longer wipes all


def test_sync_claims_only_unclaimed_guest_history(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    guest = TestClient(main.app)
    body = ask_body("Before signing up")
    guest.post("/ask", json=body)
    c, uid, _ = make_user()
    assert c.post("/history/sync", json={"session_id": body["session_id"]}).json()["synced"] == 1
    assert c.post("/history/sync", json={"session_id": body["session_id"]}).json()["synced"] == 0


# ---------------------------------------------------------------- /ask identity

def test_ask_ignores_user_id_in_body(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    victim, victim_id, _ = make_user()
    guest = TestClient(main.app)
    body = ask_body("Spoof attempt", user_id=victim_id)
    assert guest.post("/ask", json=body).status_code == 200
    assert victim.get("/history/x").json()["history"] == []  # nothing was filed under the victim
    assert "user_id" not in main.AskRequest.model_fields


def test_ask_files_history_under_cookie_identity(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    c, uid, _ = make_user()
    c.post("/ask", json=ask_body("Cookie identity", user_id=99999))
    rows = c.get("/history/x").json()["history"]
    assert [r["user_id"] for r in rows] == [uid]


def test_ask_drops_invented_citations(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    r = TestClient(main.app).post("/ask", json=ask_body("Citation check"))
    assert r.status_code == 200
    assert r.json()["citations"] == [{"paper_id": 1, "title": "Paper One"}]


def test_ask_without_abstracts_is_rejected_not_hallucinated(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    body = {"question": "Anything?", "results": [{"title": "No abstract", "description": "OpenAlex"}], "session_id": "guest-session-0001-abcd"}
    assert TestClient(main.app).post("/ask", json=body).status_code == 400


def test_ask_input_limits(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    c = TestClient(main.app)
    assert c.post("/ask", json=ask_body("q" * 2001)).status_code == 422
    assert c.post("/ask", json={**ask_body("many"), "results": [PAPERS[0]] * 51}).status_code == 422


# ---------------------------------------------------------------- AI errors never leak

def test_no_ai_key_returns_generic_503_without_paths(monkeypatch):
    use_fake_ai(monkeypatch)
    r = TestClient(main.app).post("/ask", json=ask_body("No key configured"))
    assert r.status_code == 503
    text = r.text
    assert ".env" not in text and main.BASE_DIR not in text and "GEMINI_API_KEY" not in text and "Uvicorn" not in text


def test_gemini_error_is_generic(monkeypatch):
    secret = "403 PERMISSION_DENIED key=AIzaSECRET at /srv/app/backend/.env"
    use_fake_ai(monkeypatch, gemini=FakeGemini(error=RuntimeError(secret)))
    r = TestClient(main.app).post("/ask", json=ask_body("Provider error one"))
    assert r.status_code == 502
    assert r.json()["detail"] == main.GENERIC_AI_ERROR
    assert "AIzaSECRET" not in r.text and "/srv/app" not in r.text


def test_invalid_json_from_model_is_generic(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"answer": "cut off mid'))
    r = TestClient(main.app).post("/ask", json=ask_body("Truncated json"))
    assert r.status_code == 502 and r.json()["detail"] == main.GENERIC_AI_ERROR


def test_groq_json_validate_failed_is_retried_once_with_fewer_papers(monkeypatch):
    groq = FakeGroq([RuntimeError("Error code: 400 - json_validate_failed"), GOOD_JSON])
    use_fake_ai(monkeypatch, groq=groq)
    r = TestClient(main.app).post("/ask", json=ask_body("Groq retry path"))
    assert r.status_code == 200, r.text
    assert len(groq.calls) == 2
    assert "under 150 words" in groq.calls[1]["messages"][1]["content"]


def test_groq_both_attempts_fail_is_generic(monkeypatch):
    groq = FakeGroq([RuntimeError("json_validate_failed secret-internal-detail"), RuntimeError("again secret-internal-detail")])
    use_fake_ai(monkeypatch, groq=groq)
    r = TestClient(main.app).post("/ask", json=ask_body("Groq always fails"))
    assert r.status_code == 502 and "secret-internal-detail" not in r.text


def test_max_tokens_are_set_on_both_providers(monkeypatch):
    gemini = FakeGemini(text=GOOD_JSON)
    use_fake_ai(monkeypatch, gemini=gemini)
    TestClient(main.app).post("/ask", json=ask_body("Gemini tokens"))
    assert gemini.calls[0]["config"].max_output_tokens == main.AI_MAX_OUTPUT_TOKENS

    groq = FakeGroq([GOOD_JSON])
    use_fake_ai(monkeypatch, groq=groq)
    TestClient(main.app).post("/ask", json=ask_body("Groq tokens"))
    assert groq.calls[0]["max_completion_tokens"] == main.AI_MAX_OUTPUT_TOKENS


def test_deep_dive_works_and_does_not_leak(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"deep_dive": "## Summary\\nok"}'))
    c = TestClient(main.app)
    r = c.post("/deep-dive", json={"title": "Paper", "abstract": "Some abstract text."})
    assert r.status_code == 200 and r.json()["deep_dive"].startswith("## Summary")
    # no abstract: answered without calling the model
    r = c.post("/deep-dive", json={"title": "Paper", "abstract": "No abstract available."})
    assert r.status_code == 200 and "No abstract" in r.json()["deep_dive"]
    use_fake_ai(monkeypatch, gemini=FakeGemini(error=RuntimeError("boom /etc/secret")))
    r = c.post("/deep-dive", json={"title": "Paper", "abstract": "Some abstract text."})
    assert r.status_code == 502 and "/etc/secret" not in r.text


def test_unexpected_errors_do_not_leak(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("internal /srv/secret path")
    monkeypatch.setattr(main, "clean_academic_query", boom)
    r = TestClient(main.app, raise_server_exceptions=False).get("/search", params={"q": "sleep"})
    assert r.status_code == 500 and "/srv/secret" not in r.text


# ---------------------------------------------------------------- /health and docs exposure

def test_health_exposes_nothing(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "healthy"}


def test_health_is_503_when_database_is_down(client, monkeypatch):
    def down():
        raise RuntimeError("db down: postgresql://user:pw@host/db")
    monkeypatch.setattr(main.engine, "connect", down)
    r = client.get("/health")
    assert r.status_code == 503 and "postgresql" not in r.text


def test_interactive_docs_are_off_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers_present(client):
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_oversized_body_is_rejected(client):
    r = client.post("/ask", content=b"x" * (main.MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413


# ---------------------------------------------------------------- rate limiter

def test_rate_limiter_blocks_after_limit(client):
    limit = limiter().limits["/auth/login"][0]
    # a different address each time, so the per-account lockout does not trigger before the per-IP limit
    codes = [client.post("/auth/login", json={"email": f"x{i}@example.com", "password": "nope-nope"}).status_code for i in range(limit + 1)]
    assert codes[:limit] == [401] * limit
    assert codes[-1] == 429


def test_rate_limiter_cannot_be_dodged_with_fake_forwarded_for(client):
    assert main.TRUSTED_PROXY_COUNT == 0
    limit = limiter().limits["/auth/login"][0]
    codes = []
    for i in range(limit + 3):
        r = client.post("/auth/login", json={"email": f"x{i}@example.com", "password": "nope-nope"},
                        headers={"X-Forwarded-For": f"10.0.{i}.{i}"})
        codes.append(r.status_code)
    assert 429 in codes


def test_rate_limit_buckets_do_not_multiply_with_path_variants(client):
    limit = limiter().limits["default"][0]
    codes = [client.get(f"/history/random-{i}").status_code for i in range(limit + 5)]
    assert 429 in codes


def test_429_still_carries_cors_headers(client):
    limit = limiter().limits["/auth/login"][0]
    r = None
    for i in range(limit + 1):
        r = client.post("/auth/login", json={"email": f"x{i}@example.com", "password": "nope-nope"},
                        headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 429
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_rate_limiter_memory_is_bounded(monkeypatch):
    lim = limiter()
    monkeypatch.setattr(lim, "MAX_TRACKED_KEYS", 10)
    for i in range(50):
        lim.request_history[f"1.1.1.{i}:default"] = [0.0]  # long expired
    TestClient(main.app).get("/")
    assert len(lim.request_history) < 50


def _fake_request(peer, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return pytypes.SimpleNamespace(client=pytypes.SimpleNamespace(host=peer), headers=headers)


def test_get_client_ip_ignores_header_unless_proxy_trusted(monkeypatch):
    monkeypatch.setattr(main, "TRUSTED_PROXY_COUNT", 0)
    assert main.get_client_ip(_fake_request("9.9.9.9", "1.1.1.1")) == "9.9.9.9"
    monkeypatch.setattr(main, "TRUSTED_PROXY_COUNT", 1)
    # client forged "6.6.6.6"; our proxy appended the real address last
    assert main.get_client_ip(_fake_request("10.0.0.1", "6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    assert main.get_client_ip(_fake_request("10.0.0.1")) == "10.0.0.1"  # header missing: fall back to peer


# ---------------------------------------------------------------- sessions, passwords, accounts

def test_password_change_invalidates_old_sessions_but_keeps_this_one():
    c, _, email = make_user()
    other = TestClient(main.app)
    assert other.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    r = c.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "a-brand-new-pass"})
    assert r.status_code == 200
    assert c.get("/auth/me").status_code == 200       # this browser got a fresh cookie
    assert other.get("/auth/me").status_code == 401   # the other session is dead


def test_password_reset_invalidates_existing_sessions():
    c, uid, email = make_user()
    limiter().request_history.clear()
    TestClient(main.app).post("/auth/forgot-password", json={"email": email})
    token = emailed_token("reset", email)
    r = TestClient(main.app).post("/auth/reset-password", json={"token": token, "new_password": "reset-password-1"})
    assert r.status_code == 200
    assert c.get("/auth/me").status_code == 401


def test_logout_clears_cookie():
    c, _, _ = make_user()
    assert c.post("/auth/logout").status_code == 200
    assert c.get("/auth/me").status_code == 401


def test_delete_account_requires_password_and_kills_the_session():
    c, uid, email = make_user()
    assert c.request("DELETE", "/auth/delete-account").status_code == 422          # body/password mandatory
    assert c.request("DELETE", "/auth/delete-account", json={"password": "wrong-password"}).status_code == 400
    assert c.request("DELETE", "/auth/delete-account", json={"password": PASSWORD}).status_code == 200
    assert c.get("/auth/me").status_code == 401
    assert TestClient(main.app).post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401


def test_deleted_users_old_token_is_dead_everywhere(monkeypatch):
    c, uid, email = make_user()
    db = main.SessionLocal()
    try:
        pw_hash = db.query(main.User).filter(main.User.id == uid).first().hashed_password
    finally:
        db.close()
    stale = main.create_access_token(uid, email, pw_hash)  # valid until the account disappears
    c.request("DELETE", "/auth/delete-account", json={"password": PASSWORD})
    ghost = TestClient(main.app)
    ghost.cookies.set("access_token", stale)
    assert ghost.get("/bookmarks").status_code == 401


def test_auth_cookie_is_httponly_and_samesite():
    _, _, email = make_user()
    r = TestClient(main.app).post("/auth/login", json={"email": email, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_legacy_password_hashes_still_work_and_are_upgraded():
    import hashlib
    salt = "abcd" * 8
    legacy = f"{salt}:{hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), salt.encode(), 100000).hex()}"
    db = main.SessionLocal()
    try:
        db.add(main.User(email="legacy@example.com", hashed_password=legacy, is_verified=True))
        db.commit()
    finally:
        db.close()
    r = TestClient(main.app).post("/auth/login", json={"email": "legacy@example.com", "password": PASSWORD})
    assert r.status_code == 200
    db = main.SessionLocal()
    try:
        stored = db.query(main.User).filter(main.User.email == "legacy@example.com").first().hashed_password
    finally:
        db.close()
    assert stored.startswith("pbkdf2$600000$")


def test_login_and_register_validation():
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": "not-an-email", "password": PASSWORD}).status_code == 400
    assert c.post("/auth/register", json={"email": "short@example.com", "password": "short"}).status_code == 400
    assert c.post("/auth/register", json={"email": "long@example.com", "password": "x" * 129}).status_code == 422
    assert c.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}).status_code == 401


def test_register_never_returns_sensitive_fields_or_a_session():
    r = TestClient(main.app).post("/auth/register", json={"email": "fields@example.com", "password": PASSWORD})
    assert r.status_code == 200 and list(r.json()) == ["message"]
    assert "set-cookie" not in r.headers
    assert "hashed_password" not in r.text and "token" not in r.text


# ---------------------------------------------------------------- startup configuration (fresh interpreter)

def _import_main_with(env_overrides, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import main; print('COOKIE_SECURE=', main.COOKIE_SECURE)"],
        cwd=os.path.dirname(os.path.abspath(__file__)), env=env, capture_output=True, text=True,
    )


def test_cookie_secure_defaults_to_true():
    r = _import_main_with({}, drop=("COOKIE_SECURE", "DEV_MODE"))
    assert r.returncode == 0, r.stderr
    assert "COOKIE_SECURE= True" in r.stdout


def test_dev_mode_relaxes_cookie_secure_default():
    r = _import_main_with({"DEV_MODE": "true"}, drop=("COOKIE_SECURE",))
    assert "COOKIE_SECURE= False" in r.stdout


@pytest.mark.parametrize("missing", ["AUTH_SECRET", "CORS_ORIGINS", "DATABASE_URL", "APP_URL"])
def test_app_refuses_to_start_without_required_settings(missing):
    r = _import_main_with({}, drop=(missing,))
    assert r.returncode != 0 and "required" in r.stderr


def test_app_refuses_weak_auth_secret_in_production():
    r = _import_main_with({"AUTH_SECRET": "short"})
    assert r.returncode != 0 and "at least 32" in r.stderr


def test_samesite_none_requires_secure():
    r = _import_main_with({"COOKIE_SAMESITE": "none", "COOKIE_SECURE": "false"})
    assert r.returncode != 0


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://u:p@localhost:5432/db", "postgresql+psycopg2://u:p@localhost:5432/db"),
        ("postgres://u:p@host/db", "postgresql+psycopg2://u:p@host/db"),
        ("postgresql+psycopg2://u:p@host/db", "postgresql+psycopg2://u:p@host/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_database_url_is_pinned_to_the_installed_driver(url, expected):
    assert main.normalize_database_url(url) == expected
    from sqlalchemy import create_engine
    assert create_engine(main.normalize_database_url(url)).dialect.driver in ("psycopg2", "pysqlite")


# ---------------------------------------------------------------- no hardcoded domain

def _print_setting(env_overrides, expr, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_overrides)
    r = subprocess.run([sys.executable, "-c", f"import main; print('VALUE=' + str({expr}))"],
                       cwd=os.path.dirname(os.path.abspath(__file__)), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [l for l in r.stdout.splitlines() if l.startswith("VALUE=")][0][6:]


def test_mail_sender_is_derived_from_app_url_not_hardcoded():
    out = _print_setting({"APP_URL": "https://example-host.test", "SMTP_FROM_EMAIL": ""}, "main.SMTP_FROM_EMAIL")
    assert out == "noreply@example-host.test"


def test_explicit_sender_and_contact_email_win():
    env = {"APP_URL": "https://a.test", "SMTP_FROM_EMAIL": "hello@b.test", "CONTACT_EMAIL": "ops@c.test"}
    assert _print_setting(env, "main.SMTP_FROM_EMAIL") == "hello@b.test"
    assert _print_setting(env, "main.CONTACT_EMAIL") == "ops@c.test"
    env.pop("CONTACT_EMAIL")
    assert _print_setting({**env, "CONTACT_EMAIL": ""}, "main.CONTACT_EMAIL") == "hello@b.test"


def test_backend_source_contains_no_hardcoded_site_domains():
    source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8").read().lower()
    for banned in ("scholarpulse.org", "research.org", "example.com", "imosmart"):
        assert banned not in source, f"hardcoded domain found in main.py: {banned}"


# ---------------------------------------------------------------- API documentation (OpenAPI)

def _operations():
    schema = main.app.openapi()
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            yield method, path, op


def test_every_route_is_documented_with_summary_description_and_tag():
    ops = list(_operations())
    assert len(ops) >= 20
    for method, path, op in ops:
        assert op.get("summary"), f"{method} {path}: summary missing"
        assert len(op.get("description", "")) > 25, f"{method} {path}: description missing"
        assert op.get("tags"), f"{method} {path}: tag missing"
        assert "200" in op["responses"], f"{method} {path}: no documented 200 example"


def test_protected_routes_document_401_and_verified_routes_document_403():
    by_key = {(m, p): op for m, p, op in _operations()}
    for key in main.PROTECTED_OPERATIONS:
        op = by_key[key]
        assert "401" in op["responses"], key
        assert op.get("security") == [{"cookieAuth": []}], key
    for key in [("post", "/bookmarks"), ("get", "/bookmarks"), ("delete", "/bookmarks/{bookmark_id}"), ("post", "/history/sync")]:
        assert "403" in by_key[key]["responses"], key


def test_rate_limited_and_ai_routes_document_their_errors():
    by_key = {(m, p): op for m, p, op in _operations()}
    for key in [("post", "/ask"), ("post", "/deep-dive")]:
        assert {"429", "502", "503"} <= set(by_key[key]["responses"]), key
    assert "429" in by_key[("post", "/auth/login")]["responses"]


def test_documented_error_examples_never_leak_paths_or_secrets():
    text = str(main.app.openapi()).lower().replace(".env.example", "")  # naming the example file is fine
    for banned in (".env", "traceback", "gemini_api_key", "groq_api_key", "/home/", "uvicorn"):
        assert banned not in text, banned


def test_schema_declares_cookie_auth_and_tags():
    schema = main.app.openapi()
    assert schema["components"]["securitySchemes"]["cookieAuth"]["name"] == "access_token"
    assert {t["name"] for t in schema["tags"]} == {"System", "Auth", "Search", "AI", "History", "Library"}
    assert schema["info"]["title"] == "ScholarPulse API"


def test_root_path_is_configurable_for_reverse_proxies():
    out = _print_setting({"API_ROOT_PATH": "/api/"}, "main.app.openapi()['servers']")
    assert out == "[{'url': '/api'}]"
    assert _print_setting({"API_ROOT_PATH": ""}, "main.app.openapi().get('servers')") == "None"


def test_interactive_docs_are_served_when_enabled():
    out = _print_setting({"ENABLE_DOCS": "true"},
                         "(lambda c: (c.get('/docs').status_code, c.get('/redoc').status_code, c.get('/openapi.json').status_code))"
                         "(__import__('fastapi.testclient', fromlist=['x']).TestClient(main.app))")
    assert out == "(200, 200, 200)"


# ================================================================ closing the remaining gaps

# ---- 4. registration does not reveal whether an address is registered

def test_register_response_is_identical_for_new_and_existing_addresses():
    c = TestClient(main.app)
    new = c.post("/auth/register", json={"email": "enum-test@example.com", "password": PASSWORD})
    limiter().request_history.clear()
    again = c.post("/auth/register", json={"email": "enum-test@example.com", "password": "another-password"})
    assert (new.status_code, new.json(), "set-cookie" in new.headers) == (again.status_code, again.json(), "set-cookie" in again.headers)
    assert kinds("enum-test@example.com") == ["verify", "exists"]  # the owner is told; the caller learns nothing


def test_registering_an_existing_address_never_changes_the_account():
    c, uid, email = make_user()
    TestClient(main.app).post("/auth/register", json={"email": email, "password": "attacker-password-1"})
    assert TestClient(main.app).post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert TestClient(main.app).post("/auth/login", json={"email": email, "password": "attacker-password-1"}).status_code == 401


# ---- 7. verification and reset tokens are stored hashed, single-use and time-limited

def test_tokens_are_stored_hashed_not_raw():
    c = TestClient(main.app)
    c.post("/auth/register", json={"email": "hashed@example.com", "password": PASSWORD})
    raw = emailed_token("verify", "hashed@example.com")
    db = main.SessionLocal()
    try:
        user = db.query(main.User).filter(main.User.email == "hashed@example.com").first()
        assert user.verification_token == main.hash_token(raw) and raw not in user.verification_token
    finally:
        db.close()
    c.post("/auth/forgot-password", json={"email": "hashed@example.com"})
    raw_reset = emailed_token("reset", "hashed@example.com")
    db = main.SessionLocal()
    try:
        user = db.query(main.User).filter(main.User.email == "hashed@example.com").first()
        assert user.reset_token == main.hash_token(raw_reset) and user.reset_token != raw_reset
    finally:
        db.close()


def test_a_leaked_database_hash_cannot_be_used_as_a_token():
    c = TestClient(main.app)
    c.post("/auth/register", json={"email": "leak@example.com", "password": PASSWORD})
    db = main.SessionLocal()
    try:
        stored = db.query(main.User).filter(main.User.email == "leak@example.com").first().verification_token
    finally:
        db.close()
    assert c.post("/auth/verify-email", json={"token": stored}).status_code == 400


def test_verification_token_is_single_use_and_expires():
    c = TestClient(main.app)
    c.post("/auth/register", json={"email": "expiry@example.com", "password": PASSWORD})
    raw = emailed_token("verify", "expiry@example.com")
    db = main.SessionLocal()
    try:
        user = db.query(main.User).filter(main.User.email == "expiry@example.com").first()
        user.verification_token_expires_at = main._utcnow() - main.timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    assert c.post("/auth/verify-email", json={"token": raw}).status_code == 400  # expired
    c.post("/auth/resend-verification", json={"email": "expiry@example.com"})
    fresh = emailed_token("verify", "expiry@example.com")
    assert fresh != raw
    assert c.post("/auth/verify-email", json={"token": fresh}).status_code == 200
    assert c.post("/auth/verify-email", json={"token": fresh}).status_code == 400  # single use


def test_reset_token_is_single_use():
    c, uid, email = make_user()
    TestClient(main.app).post("/auth/forgot-password", json={"email": email})
    token = emailed_token("reset", email)
    anon = TestClient(main.app)
    assert anon.post("/auth/reset-password", json={"token": token, "new_password": "first-new-password"}).status_code == 200
    assert anon.post("/auth/reset-password", json={"token": token, "new_password": "second-new-password"}).status_code == 400


def test_email_cap_per_address_per_hour_with_identical_responses():
    c = TestClient(main.app)
    _, _, email = make_user()
    OUTBOX.clear()
    responses = []
    for _ in range(main.EMAIL_MAX_PER_HOUR + 2):
        limiter().request_history.clear()
        r = c.post("/auth/forgot-password", json={"email": email})
        responses.append((r.status_code, r.json()))
    assert len(set(map(str, responses))) == 1
    assert kinds(email).count("reset") == main.EMAIL_MAX_PER_HOUR


# ---- 2. per-account lockout

def _bad_login(email, password="wrong-password-1"):
    limiter().request_history.clear()
    return TestClient(main.app).post("/auth/login", json={"email": email, "password": password})


def test_account_locks_after_repeated_failures_even_for_the_right_password():
    _, _, email = make_user()
    for _ in range(main.LOGIN_MAX_FAILURES):
        assert _bad_login(email).status_code == 401
    locked = _bad_login(email, PASSWORD)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert "password" in locked.json()["detail"].lower() and email not in locked.text


def test_lockout_is_identical_for_unknown_addresses():
    for _ in range(main.LOGIN_MAX_FAILURES):
        _bad_login("ghost@example.com")
    assert _bad_login("ghost@example.com").status_code == 429


def test_lockout_is_per_account_and_cleared_by_success_or_reset():
    _, _, a = make_user()
    _, _, b = make_user()
    for _ in range(main.LOGIN_MAX_FAILURES - 1):
        _bad_login(a)
    assert _bad_login(b).status_code == 401                                   # another account is unaffected
    assert _bad_login(a, PASSWORD).status_code == 200                         # success resets the counter
    for _ in range(main.LOGIN_MAX_FAILURES):
        _bad_login(a)
    assert _bad_login(a, PASSWORD).status_code == 429
    TestClient(main.app).post("/auth/forgot-password", json={"email": a})
    r = TestClient(main.app).post("/auth/reset-password", json={"token": emailed_token("reset", a), "new_password": "brand-new-password-1"})
    assert r.status_code == 200
    assert _bad_login(a, "brand-new-password-1").status_code == 200           # the owner is back in


# ---- 3. server-side logout

def test_logout_revokes_a_copied_token():
    c, _, email = make_user()
    stolen = c.cookies.get("access_token")
    assert TestClient(main.app, cookies={"access_token": stolen}).get("/auth/me").status_code == 200
    assert c.post("/auth/logout").status_code == 200
    thief = TestClient(main.app)
    thief.cookies.set("access_token", stolen)
    assert thief.get("/auth/me").status_code == 401
    assert thief.get("/bookmarks").status_code == 401
    assert TestClient(main.app).post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_logout_revokes_the_bearer_token_too():
    c, _, _ = make_user()
    token = c.cookies.get("access_token")
    c2 = TestClient(main.app)
    assert c2.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    c2.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert c2.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_all_signs_out_every_device():
    c, _, email = make_user()
    other = TestClient(main.app)
    other.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert other.get("/auth/me").status_code == 200
    assert c.post("/auth/logout-all").status_code == 200
    assert c.get("/auth/me").status_code == 401 and other.get("/auth/me").status_code == 401
    fresh = TestClient(main.app)
    assert fresh.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert fresh.get("/auth/me").status_code == 200          # sessions created afterwards work
    assert TestClient(main.app).post("/auth/logout-all").status_code == 401


def test_session_lifetime_is_configurable_and_shorter_by_default():
    assert main.SESSION_DAYS == 14
    payload = main.verify_access_token(main.create_access_token(1, "a@b.c"))
    assert abs(payload["exp"] - (main.time.time() + 14 * 86400)) < 60 and payload["jti"] and payload["iat"] <= main.time.time()


def test_revoked_token_rows_are_purged_after_expiry():
    c, _, _ = make_user()
    db = main.SessionLocal()
    try:
        db.add(main.RevokedToken(jti="old" * 8, expires_at=main._utcnow() - main.timedelta(days=1)))
        db.commit()
    finally:
        db.close()
    c.post("/auth/logout")
    db = main.SessionLocal()
    try:
        assert db.query(main.RevokedToken).filter(main.RevokedToken.jti == "old" * 8).count() == 0
    finally:
        db.close()


# ---- 1. daily AI spend limits

def test_guest_ai_quota_per_ip(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 2)
    c = TestClient(main.app)
    codes = [c.post("/ask", json=ask_body(f"Quota question number {i}")).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    r = c.post("/ask", json=ask_body("Quota question number 99"))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "tomorrow" in r.json()["detail"].lower()
    assert c.post("/deep-dive", json={"title": "T", "abstract": "Some abstract."}).status_code == 429  # shared bucket


def test_cache_hits_do_not_use_quota(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 1)
    c = TestClient(main.app)
    body = ask_body("Same question again and again")
    assert [c.post("/ask", json=body).status_code for _ in range(4)] == [200, 200, 200, 200]


def test_signed_in_users_have_their_own_higher_quota(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 1)
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_USER", 3)
    user, _, _ = make_user()
    guest = TestClient(main.app)
    assert guest.post("/ask", json=ask_body("guest one question")).status_code == 200
    assert guest.post("/ask", json=ask_body("guest two question")).status_code == 429
    assert [user.post("/ask", json=ask_body(f"member question {i}")).status_code for i in range(4)] == [200, 200, 200, 429]


def test_global_ai_quota_protects_the_budget(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GLOBAL", 2)
    a, b = TestClient(main.app), TestClient(main.app)
    assert a.post("/ask", json=ask_body("global one")).status_code == 200
    assert b.post("/ask", json=ask_body("global two")).status_code == 200
    r = a.post("/ask", json=ask_body("global three"))
    assert r.status_code == 429 and "busy" in r.json()["detail"].lower()


def test_quota_zero_means_unlimited(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 0)
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GLOBAL", 0)
    c = TestClient(main.app)
    assert all(c.post("/ask", json=ask_body(f"unlimited {i}")).status_code == 200 for i in range(5))


# ---- 6. unguessable guest session ids

@pytest.mark.parametrize("bad", ["short", "a" * 15, "has spaces in it 123", "semi;colon-0123456789", "x" * 129, ""])
def test_weak_or_malformed_session_ids_are_rejected(monkeypatch, bad):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    r = TestClient(main.app).post("/ask", json={**ask_body("Session id check"), "session_id": bad})
    assert r.status_code == 422
    assert TestClient(main.app).get(f"/history/{bad or 'x'}").json() == {"history": []}


def test_uuid_and_hex_session_ids_are_accepted(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    import uuid
    for i, sid in enumerate([str(uuid.uuid4()), uuid.uuid4().hex, "A" * 16]):
        assert TestClient(main.app).post("/ask", json={**ask_body(f"Session ok {i}"), "session_id": sid}).status_code == 200


def test_guest_history_cannot_be_read_with_a_guessed_short_id(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD_JSON))
    TestClient(main.app).post("/ask", json=ask_body("Anonymous bucket check"))  # filed under the real id
    for guess in ("anonymous", "test", "123", "default", "admin"):
        assert TestClient(main.app).get(f"/history/{guess}").json() == {"history": []}


# ---- 5. headers on API responses

def test_api_responses_carry_a_locked_down_csp(client):
    r = client.get("/")
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert r.headers["cache-control"] == "no-store"
