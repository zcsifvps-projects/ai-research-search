"""Tests for complete account deletion, the daily housekeeping job, and safe defaults."""
from datetime import timedelta

from fastapi.testclient import TestClient

import main
import make_admin
from test_documents_graph import PAGE_TEXT, make_pdf, upload
from test_security import PASSWORD, clean_state, make_user  # noqa: F401 (autouse fixture)

TURN = {"question": "Does sleep help memory?", "answer": "Yes [1].", "citations": [], "mode": "quick"}


def count(model, **where):
    db = main.SessionLocal()
    try:
        query = db.query(model)
        for column, value in where.items():
            query = query.filter(getattr(model, column) == value)
        return query.count()
    finally:
        db.close()


# ------------------------------------------------------------ account deletion removes everything

def test_deleting_an_account_removes_all_its_data():
    c, uid, email = make_user()
    bookmark = c.post("/bookmarks", json={"title": "Paper", "authors": []}).json()["id"]
    folder = c.post("/collections", json={"name": "Thesis"}).json()["id"]
    c.post(f"/collections/{folder}/items", json={"bookmark_id": bookmark})
    thread = c.post("/threads", json={"turns": [TURN]}).json()["id"]
    token = c.post(f"/threads/{thread}/share").json()["share_token"]
    document = upload(c, make_pdf([PAGE_TEXT])).json()["id"]
    assert TestClient(main.app).get(f"/share/{token}").status_code == 200

    r = c.request("DELETE", "/auth/delete-account", json={"password": PASSWORD})
    assert r.status_code == 200, r.text

    assert count(main.User, id=uid) == 0
    assert count(main.Bookmark, user_id=uid) == 0
    assert count(main.Collection, user_id=uid) == 0 and count(main.CollectionItem, collection_id=folder) == 0
    assert count(main.Thread, user_id=uid) == 0
    assert count(main.UploadedDocument, user_id=uid) == 0 and count(main.DocumentChunk, document_id=document) == 0
    assert TestClient(main.app).get(f"/share/{token}").status_code == 404  # the share link is dead


def test_deleting_one_account_leaves_others_alone():
    keep, keep_uid, _ = make_user()
    keep.post("/threads", json={"turns": [TURN]})
    keep.post("/collections", json={"name": "Mine"})
    gone, _, _ = make_user()
    gone.request("DELETE", "/auth/delete-account", json={"password": PASSWORD})
    assert count(main.Thread, user_id=keep_uid) == 1 and count(main.Collection, user_id=keep_uid) == 1


# ------------------------------------------------------------ housekeeping

def _add_history(user_id, days_old, session="guest-session-house-0001"):
    db = main.SessionLocal()
    try:
        row = main.SearchHistory(session_id=session, user_id=user_id, question="q", answer="a",
                                 created_at=main._utcnow().replace(tzinfo=None) - timedelta(days=days_old))
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_housekeeping_removes_old_guest_history_only():
    _, uid, _ = make_user()
    old_guest = _add_history(None, main.GUEST_HISTORY_DAYS + 5)
    new_guest = _add_history(None, 1)
    old_user = _add_history(uid, main.GUEST_HISTORY_DAYS + 5)
    counts = main.run_housekeeping()
    assert counts["guest_history"] >= 1
    assert count(main.SearchHistory, id=old_guest) == 0
    assert count(main.SearchHistory, id=new_guest) == 1
    assert count(main.SearchHistory, id=old_user) == 1  # account history is kept until the user deletes it
    assert main.LAST_HOUSEKEEPING["at"]


def test_housekeeping_clears_expired_tokens_and_old_audit_entries():
    now = main._utcnow().replace(tzinfo=None)
    db = main.SessionLocal()
    try:
        db.add(main.RevokedToken(jti="expired-jti-0001", expires_at=now - timedelta(days=1)))
        db.add(main.RevokedToken(jti="live-jti-0001", expires_at=now + timedelta(days=1)))
        db.add(main.AdminAuditLog(action="old", created_at=now - timedelta(days=main.AUDIT_LOG_DAYS + 1)))
        db.add(main.AdminAuditLog(action="recent", created_at=now))
        user = main.User(email="expired-token@example.com", hashed_password="x", is_verified=False,
                         verification_token="hash", verification_token_expires_at=now - timedelta(hours=1),
                         reset_token="hash2", reset_token_expires_at=now - timedelta(hours=1))
        db.add(user)
        db.commit()
        uid = user.id
    finally:
        db.close()
    main.run_housekeeping()
    assert count(main.RevokedToken, jti="expired-jti-0001") == 0 and count(main.RevokedToken, jti="live-jti-0001") == 1
    assert count(main.AdminAuditLog, action="old") == 0 and count(main.AdminAuditLog, action="recent") == 1
    db = main.SessionLocal()
    try:
        user = db.query(main.User).filter(main.User.id == uid).first()
        assert user.verification_token is None and user.reset_token is None
    finally:
        db.close()


def test_housekeeping_command(capsys):
    assert make_admin.cli(["make_admin.py", "housekeeping"]) == 0
    assert "guest_history" in capsys.readouterr().out


def test_housekeeping_thread_is_off_in_tests():
    import threading

    assert main.HOUSEKEEPING_ENABLED is False
    assert not [t for t in threading.enumerate() if t.name == "housekeeping"]


# ------------------------------------------------------------ safe defaults

def test_missing_gemini_model_falls_back_to_a_current_alias():
    out = __import__("test_security")._print_setting({"GEMINI_MODEL": ""}, "main.GEMINI_MODEL")
    assert "gemini-flash-latest" in out


# ------------------------------------------------------------ backup status (read-only)

def test_backup_status_with_no_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "BACKUP_DIR", str(tmp_path / "missing"))
    status = main.backup_status()
    assert status == {"configured": False, "count": 0, "latest_at": None, "latest_mb": None, "total_mb": 0, "stale": True}


def test_backup_status_counts_dumps_and_flags_stale(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(main, "BACKUP_DIR", str(tmp_path))
    old = tmp_path / "imosmart-2026-11-10_0230.dump"
    new = tmp_path / "imosmart-2026-11-11_0230.dump"
    old.write_bytes(b"x" * 1_000_000)
    new.write_bytes(b"x" * 2_000_000)
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "imosmart-2026-11-12_0230.dump.part").write_bytes(b"x")  # unfinished dumps are ignored
    now = time.time()
    os.utime(old, (now - 50 * 3600, now - 50 * 3600))
    os.utime(new, (now - 3600, now - 3600))
    status = main.backup_status()
    assert status["count"] == 2 and status["latest_mb"] == 2.0 and status["total_mb"] == 3.0
    assert status["stale"] is False and status["latest_at"]
    os.utime(new, (now - 30 * 3600, now - 30 * 3600))
    assert main.backup_status()["stale"] is True


def test_overview_includes_backups(monkeypatch, tmp_path):
    from test_admin import code_for, make_admin_client

    monkeypatch.setattr(main, "SETTINGS_ENCRYPTION_KEY", make_admin.new_encryption_key())
    monkeypatch.setattr(main, "BACKUP_DIR", str(tmp_path))
    c, uid, email, secret = make_admin_client()
    c.post("/admin/unlock", json={"code": code_for(secret)})
    data = c.get("/admin/overview").json()
    assert data["backups"]["count"] == 0 and data["backups"]["configured"] is True
    assert str(tmp_path) not in str(data)  # the folder path is never shown
