"""Tests for admin rights, authenticator unlock, audit log, user tools, and encrypted API keys."""
import time

import pytest
from fastapi.testclient import TestClient

import main
import make_admin
from test_security import clean_state, limiter, make_user  # noqa: F401 (autouse fixture)

ENC_KEY = make_admin.new_encryption_key()


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setattr(main, "SETTINGS_ENCRYPTION_KEY", ENC_KEY)
    main.reset_panel_key_cache()
    db = main.SessionLocal()
    db.query(main.AppSetting).delete()
    db.commit()
    db.close()
    yield
    db = main.SessionLocal()
    db.query(main.AppSetting).delete()
    db.commit()
    db.close()
    main.reset_panel_key_cache()


def code_for(secret: str, offset: int = 0) -> str:
    return main.totp_code(secret, int(time.time() // main.TOTP_PERIOD) + offset)


def make_admin_client():
    c, uid, email = make_user()
    secret = make_admin.grant(email)
    return c, uid, email, secret


def unlocked_admin():
    c, uid, email, secret = make_admin_client()
    r = c.post("/admin/unlock", json={"code": code_for(secret)})
    assert r.status_code == 200, r.text
    return c, uid, email, secret


# ------------------------------------------------------------ TOTP

def test_totp_matches_rfc6238_test_vector():
    # RFC 6238 appendix B, SHA-1, T = 59 s -> 94287082 (8 digits); the 6-digit code is its last 6 digits.
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert main.totp_code(secret, 59 // 30) == "287082"


def test_totp_allows_one_step_drift_and_refuses_reuse():
    secret = main.new_totp_secret()
    now = time.time()
    step = int(now // 30)
    assert main.match_totp(secret, main.totp_code(secret, step - 1), None, at=now) == step - 1
    assert main.match_totp(secret, main.totp_code(secret, step + 2), None, at=now) is None
    assert main.match_totp(secret, main.totp_code(secret, step), step, at=now) is None  # already used
    assert main.match_totp(secret, "12345", None) is None and main.match_totp(secret, "abcdef", None) is None


# ------------------------------------------------------------ encryption

def test_settings_encryption_round_trip_and_binding():
    sealed = main.encrypt_setting("GROQ_API_KEY", "gsk_secret_value_123")
    assert "gsk_secret" not in sealed
    assert main.decrypt_setting("GROQ_API_KEY", sealed) == "gsk_secret_value_123"
    assert main.decrypt_setting("CORE_API_KEY", sealed) is None  # bound to its setting name


def test_wrong_encryption_key_cannot_decrypt(monkeypatch):
    sealed = main.encrypt_setting("GROQ_API_KEY", "value-1234")
    monkeypatch.setattr(main, "SETTINGS_ENCRYPTION_KEY", make_admin.new_encryption_key())
    assert main.decrypt_setting("GROQ_API_KEY", sealed) is None


# ------------------------------------------------------------ access control

ADMIN_ROUTES = [("get", "/admin/overview"), ("get", "/admin/users"), ("get", "/admin/audit"), ("get", "/admin/keys")]


def test_admin_routes_need_sign_in():
    c = TestClient(main.app)
    for method, path in ADMIN_ROUTES:
        assert getattr(c, method)(path).status_code == 401


def test_normal_user_is_refused_even_with_a_forged_cookie():
    c, uid, _ = make_user()
    c.cookies.set("admin_token", "00.ff")
    for method, path in ADMIN_ROUTES:
        assert getattr(c, method)(path).status_code == 403
    assert c.post("/admin/unlock", json={"code": "123456"}).status_code == 403
    assert c.get("/admin/status").json()["is_admin"] is False


def test_admin_must_unlock_with_a_code():
    c, uid, email, secret = make_admin_client()
    assert c.get("/admin/overview").status_code == 403
    status = c.get("/admin/status").json()
    assert status["is_admin"] is True and status["unlocked"] is False
    assert c.post("/admin/unlock", json={"code": "000000"}).status_code in (401, 200)  # 1 in a million it is right
    assert c.post("/admin/unlock", json={"code": code_for(secret)}).status_code == 200
    assert c.get("/admin/overview").status_code == 200
    assert c.get("/admin/status").json()["unlocked"] is True


def test_code_cannot_be_reused():
    c, uid, email, secret = make_admin_client()
    code = code_for(secret)
    assert c.post("/admin/unlock", json={"code": code}).status_code == 200
    assert c.post("/admin/unlock", json={"code": code}).status_code == 401


def test_wrong_codes_lock_the_admin_out():
    c, uid, email, secret = make_admin_client()
    for _ in range(main.ADMIN_CODE_MAX_FAILURES):
        c.post("/admin/unlock", json={"code": "999999" if code_for(secret) != "999999" else "888888"})
        limiter().request_history.clear()
    r = c.post("/admin/unlock", json={"code": code_for(secret)})
    assert r.status_code == 429


def test_admin_cookie_is_tied_to_the_sign_in_session():
    c, uid, email, secret = unlocked_admin()
    stolen = c.cookies.get("admin_token")
    other = TestClient(main.app)
    other.cookies.set("admin_token", stolen)
    assert other.get("/admin/overview").status_code == 401  # no sign-in at all
    c.post("/auth/logout")
    c.cookies.set("admin_token", stolen)
    assert c.get("/admin/overview").status_code == 401


def test_admin_cookie_is_strict_httponly_and_short():
    c, uid, email, secret = make_admin_client()
    r = c.post("/admin/unlock", json={"code": code_for(secret)})
    cookie = r.headers["set-cookie"].lower()
    assert "admin_token=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert f"max-age={main.ADMIN_SESSION_MINUTES * 60}" in cookie


def test_revoked_admin_loses_access():
    c, uid, email, secret = unlocked_admin()
    make_admin.revoke(email)
    assert c.get("/admin/overview").status_code == 403


def test_lock_ends_admin_access():
    c, uid, email, secret = unlocked_admin()
    c.post("/admin/lock")
    assert c.get("/admin/overview").status_code == 403


def test_admin_routes_are_not_in_public_docs():
    assert not [p for p in main.app.openapi()["paths"] if p.startswith("/admin")]


def test_grant_needs_verified_account_and_encryption(monkeypatch):
    c, uid, email = make_user(verified=False)
    with pytest.raises(SystemExit):
        make_admin.grant(email)
    _, _, email2 = make_user()
    monkeypatch.setattr(main, "SETTINGS_ENCRYPTION_KEY", "")
    with pytest.raises(SystemExit):
        make_admin.grant(email2)


def test_totp_secret_is_stored_encrypted():
    c, uid, email, secret = make_admin_client()
    db = main.SessionLocal()
    try:
        stored = db.query(main.User).filter(main.User.id == uid).first().totp_secret
    finally:
        db.close()
    assert stored.startswith("v1:") and secret not in stored


# ------------------------------------------------------------ dashboard and users

def test_overview_numbers():
    c, uid, email, secret = unlocked_admin()
    data = c.get("/admin/overview").json()
    assert data["users"]["total"] >= 1 and data["users"]["admins"] >= 1
    assert {"calls_today", "daily_cap"} <= set(data["ai"]) and {"runs_today", "daily_cap"} <= set(data["deep"])


def test_user_list_search_and_paging():
    c, uid, email, secret = unlocked_admin()
    data = c.get("/admin/users", params={"q": email}).json()
    assert data["total"] == 1 and data["users"][0]["email"] == email and data["users"][0]["is_admin"] is True
    assert c.get("/admin/users", params={"q": "%"}).json()["total"] == 0  # wildcards are literal
    assert c.get("/admin/users", params={"page_size": 500}).status_code == 422
    assert "hashed_password" not in str(c.get("/admin/users").json())


def test_disable_blocks_sign_in_and_ends_sessions_then_enable():
    admin, *_ = unlocked_admin()
    victim, vid, vemail = make_user()
    assert admin.post(f"/admin/users/{vid}/disable").status_code == 200
    assert victim.get("/auth/me").status_code == 401  # session ended
    limiter().request_history.clear()
    r = TestClient(main.app).post("/auth/login", json={"email": vemail, "password": main_password()})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    assert admin.post(f"/admin/users/{vid}/enable").status_code == 200
    limiter().request_history.clear()
    assert TestClient(main.app).post("/auth/login", json={"email": vemail, "password": main_password()}).status_code == 200


def main_password():
    from test_security import PASSWORD
    return PASSWORD


def test_admins_cannot_be_disabled_from_the_panel():
    admin, uid, email, secret = unlocked_admin()
    assert admin.post(f"/admin/users/{uid}/disable").status_code == 400
    other_admin, ouid, oemail, osecret = make_admin_client()
    assert admin.post(f"/admin/users/{ouid}/disable").status_code == 400


def test_logout_all_and_resend_verification():
    admin, *_ = unlocked_admin()
    victim, vid, _ = make_user()
    assert admin.post(f"/admin/users/{vid}/logout-all").status_code == 200
    assert victim.get("/auth/me").status_code == 401
    assert admin.post(f"/admin/users/{vid}/resend-verification").status_code == 400  # already verified
    unverified, uvid, uvemail = make_user(verified=False)
    assert admin.post(f"/admin/users/{uvid}/resend-verification").status_code == 200
    assert admin.post("/admin/users/999999/disable").status_code == 404


def test_actions_are_audited():
    admin, uid, email, secret = unlocked_admin()
    victim, vid, vemail = make_user()
    admin.post(f"/admin/users/{vid}/disable")
    entries = admin.get("/admin/audit").json()["entries"]
    actions = [(e["action"], e["target"]) for e in entries]
    assert ("user_disable", vemail) in actions and ("unlock", None) in actions
    assert ("admin_grant", email) in actions


# ------------------------------------------------------------ API keys

@pytest.fixture()
def fake_testers(monkeypatch):
    seen = []

    def ok(key):
        seen.append(key)

    def bad(key):
        raise ValueError(f"rejected {key}")

    monkeypatch.setitem(main.MANAGED_KEYS["GROQ_API_KEY"], "test", ok)
    monkeypatch.setitem(main.MANAGED_KEYS["CORE_API_KEY"], "test", bad)
    return seen


def test_key_status_never_returns_values(monkeypatch):
    monkeypatch.setattr(main, "GEMINI_API_KEY", "AIzaEnvValue98765")
    c, *_ = unlocked_admin()
    data = c.get("/admin/keys").json()
    gem = next(k for k in data["keys"] if k["name"] == "GEMINI_API_KEY")
    assert gem["source"] == "env" and gem["last4"] == "8765" and data["editable"] is True
    assert "AIzaEnvValue" not in str(data)


def test_set_key_tests_saves_encrypted_and_applies_without_restart(fake_testers, monkeypatch):
    monkeypatch.setattr(main, "GROQ_API_KEY", "")
    c, uid, email, secret = unlocked_admin()
    r = c.post("/admin/keys/GROQ_API_KEY", json={"value": "gsk_new_value_abcd", "code": code_for(secret, 1)})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "GROQ_API_KEY", "source": "panel", "last4": "abcd"}
    assert fake_testers == ["gsk_new_value_abcd"]
    assert main.effective_key("GROQ_API_KEY") == "gsk_new_value_abcd"
    db = main.SessionLocal()
    try:
        stored = db.query(main.AppSetting).filter(main.AppSetting.name == "GROQ_API_KEY").first().value_encrypted
    finally:
        db.close()
    assert "gsk_new_value" not in stored
    main.reset_panel_key_cache()  # as after a restart: read back from the database
    assert main.effective_key("GROQ_API_KEY") == "gsk_new_value_abcd"
    entries = c.get("/admin/audit").json()["entries"]
    assert any(e["action"] == "key_set" and e["target"] == "GROQ_API_KEY" for e in entries)
    assert "gsk_new_value" not in str(entries)


def test_rejected_key_is_never_saved(fake_testers, monkeypatch):
    monkeypatch.setattr(main, "CORE_API_KEY", "env-core-key-1234")
    c, uid, email, secret = unlocked_admin()
    r = c.post("/admin/keys/CORE_API_KEY", json={"value": "bad-core-key-0000", "code": code_for(secret, 1)})
    assert r.status_code == 400 and "bad-core-key" not in r.text
    assert main.effective_key("CORE_API_KEY") == "env-core-key-1234"


def test_key_change_needs_a_fresh_code(fake_testers):
    c, uid, email, secret = make_admin_client()
    code = code_for(secret)
    c.post("/admin/unlock", json={"code": code})
    r = c.post("/admin/keys/GROQ_API_KEY", json={"value": "gsk_value_12345678", "code": code})
    assert r.status_code == 401 and fake_testers == []  # same code again is refused


def test_key_validation():
    c, uid, email, secret = unlocked_admin()
    assert c.post("/admin/keys/NOT_A_KEY", json={"value": "x" * 10, "code": code_for(secret, 1)}).status_code == 404
    assert c.post("/admin/keys/GROQ_API_KEY", json={"value": "has space 123", "code": code_for(secret, 1)}).status_code == 400
    assert c.post("/admin/keys/GROQ_API_KEY", json={"value": "short", "code": "123456"}).status_code == 422


def test_clear_key_falls_back_to_env(fake_testers, monkeypatch):
    monkeypatch.setattr(main, "GROQ_API_KEY", "env-groq-key-5555")
    c, uid, email, secret = unlocked_admin()
    c.post("/admin/keys/GROQ_API_KEY", json={"value": "gsk_panel_value_99", "code": code_for(secret, 1)})
    assert main.effective_key("GROQ_API_KEY") == "gsk_panel_value_99"
    db = main.SessionLocal()
    try:  # let the next code be accepted
        user = db.query(main.User).filter(main.User.id == uid).first()
        user.totp_last_step = int(time.time() // 30) - 5
        db.commit()
    finally:
        db.close()
    r = c.post("/admin/keys/GROQ_API_KEY/clear", json={"code": code_for(secret)})
    assert r.status_code == 200 and r.json()["source"] == "env"
    assert main.effective_key("GROQ_API_KEY") == "env-groq-key-5555"


def test_ai_clients_use_the_effective_key(monkeypatch):
    monkeypatch.setattr(main, "GROQ_API_KEY", "")
    assert main.get_groq_client() is None
    db = main.SessionLocal()
    db.add(main.AppSetting(name="GROQ_API_KEY", value_encrypted=main.encrypt_setting("GROQ_API_KEY", "gsk_from_panel_1"),
                           last4="el_1"))
    db.commit()
    db.close()
    main.reset_panel_key_cache()
    assert main.get_groq_client() is not None


def test_new_encryption_key_format():
    import base64

    key = make_admin.new_encryption_key()
    assert len(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))) == 32
