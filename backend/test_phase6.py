"""Tests for Phase 6: folders (collections), saved conversations (threads) and share links."""
from fastapi.testclient import TestClient

import main
from test_security import clean_state, make_user  # noqa: F401 (autouse fixture)


def save_paper(c, title="Paper A", doi=None):
    r = c.post("/bookmarks", json={"title": title, "authors": ["A"], "doi": doi})
    assert r.status_code == 200, r.text
    return r.json()["id"]


TURN = {"question": "Does sleep help memory?", "answer": "Yes [1].",
        "citations": [{"paper_id": 1, "title": "Paper A"}, {"paper_id": "x"}, {"paper_id": True}], "mode": "quick"}
PAPER = {"title": "Paper A", "authors": ["A"], "abstract": "Abs", "doi": "10.1/a", "description": "J",
         "publication_year": 2024, "cited_by_count": 5, "study_types": ["rct"], "secret_field": "drop me"}


# ------------------------------------------------------------ folders

def test_folders_need_a_verified_account():
    assert TestClient(main.app).get("/collections").status_code == 401
    c, _, _ = make_user(verified=False)
    assert c.get("/collections").status_code == 403
    assert c.post("/threads", json={"turns": [TURN]}).status_code == 403


def test_folder_lifecycle_and_counts():
    c, _, _ = make_user()
    a, b = save_paper(c, "Paper A"), save_paper(c, "Paper B")
    fid = c.post("/collections", json={"name": "  Thesis   chapter  "}).json()["id"]
    assert c.post(f"/collections/{fid}/items", json={"bookmark_id": a}).status_code == 200
    assert c.post(f"/collections/{fid}/items", json={"bookmark_id": a}).status_code == 200  # twice is harmless
    folders = c.get("/collections").json()["collections"]
    assert folders == [{"id": fid, "name": "Thesis chapter", "count": 1, "created_at": folders[0]["created_at"]}]
    listed = c.get("/bookmarks", params={"collection_id": fid}).json()["bookmarks"]
    assert [x["id"] for x in listed] == [a] and listed[0]["collection_ids"] == [fid]
    everything = {x["id"]: x["collection_ids"] for x in c.get("/bookmarks").json()["bookmarks"]}
    assert everything == {a: [fid], b: []}
    assert c.post(f"/collections/{fid}/rename", json={"name": "Chapter 2"}).json()["name"] == "Chapter 2"
    assert c.delete(f"/collections/{fid}/items/{a}").status_code == 200
    assert c.get("/collections").json()["collections"][0]["count"] == 0
    assert c.delete(f"/collections/{fid}").status_code == 200
    assert {x["id"] for x in c.get("/bookmarks").json()["bookmarks"]} == {a, b}  # papers stay in the library


def test_deleting_a_saved_paper_removes_it_from_folders():
    c, _, _ = make_user()
    a = save_paper(c)
    fid = c.post("/collections", json={"name": "F"}).json()["id"]
    c.post(f"/collections/{fid}/items", json={"bookmark_id": a})
    c.delete(f"/bookmarks/{a}")
    assert c.get("/collections").json()["collections"][0]["count"] == 0


def test_folders_are_private():
    owner, _, _ = make_user()
    other, _, _ = make_user()
    a = save_paper(owner)
    fid = owner.post("/collections", json={"name": "Mine"}).json()["id"]
    other_paper = save_paper(other, "Other paper")
    assert other.get("/collections").json()["collections"] == []
    assert other.post(f"/collections/{fid}/rename", json={"name": "x"}).status_code == 404
    assert other.delete(f"/collections/{fid}").status_code == 404
    assert other.post(f"/collections/{fid}/items", json={"bookmark_id": other_paper}).status_code == 404
    assert other.get("/bookmarks", params={"collection_id": fid}).status_code == 404
    # cannot put someone else's paper in your own folder
    ofid = other.post("/collections", json={"name": "Theirs"}).json()["id"]
    assert other.post(f"/collections/{ofid}/items", json={"bookmark_id": a}).status_code == 404


def test_folder_name_rules_and_limit(monkeypatch):
    c, _, _ = make_user()
    assert c.post("/collections", json={"name": "   "}).status_code == 400
    assert c.post("/collections", json={"name": "x" * 101}).status_code == 422
    monkeypatch.setattr(main, "MAX_COLLECTIONS_PER_USER", 2)
    c.post("/collections", json={"name": "1"})
    c.post("/collections", json={"name": "2"})
    assert c.post("/collections", json={"name": "3"}).status_code == 400


# ------------------------------------------------------------ saved conversations

def test_save_open_update_rename_delete_thread():
    c, _, _ = make_user()
    r = c.post("/threads", json={"turns": [TURN], "papers": [PAPER]})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert r.json()["title"] == "Does sleep help memory?" and r.json()["turn_count"] == 1
    full = c.get(f"/threads/{tid}").json()
    assert full["turns"][0]["citations"] == [{"paper_id": 1, "title": "Paper A"}]  # bad citations dropped
    assert full["papers"][0]["cited_by_count"] == 5 and "secret_field" not in full["papers"][0]
    second = {**TURN, "question": "What about naps?", "mode": "not-a-mode"}
    r = c.post(f"/threads/{tid}", json={"turns": [TURN, second], "papers": [PAPER]})
    assert r.json()["turn_count"] == 2
    assert c.get(f"/threads/{tid}").json()["turns"][1]["mode"] is None
    assert c.post(f"/threads/{tid}/rename", json={"title": "Sleep research"}).json()["title"] == "Sleep research"
    assert [t["title"] for t in c.get("/threads").json()["threads"]] == ["Sleep research"]
    assert c.delete(f"/threads/{tid}").status_code == 200
    assert c.get(f"/threads/{tid}").status_code == 404


def test_threads_are_private():
    owner, _, _ = make_user()
    other, _, _ = make_user()
    tid = owner.post("/threads", json={"turns": [TURN]}).json()["id"]
    for method, path, body in [("get", f"/threads/{tid}", None), ("delete", f"/threads/{tid}", None),
                               ("post", f"/threads/{tid}", {"turns": [TURN]}),
                               ("post", f"/threads/{tid}/rename", {"title": "x"}),
                               ("post", f"/threads/{tid}/share", None)]:
        r = getattr(other, method)(path, json=body) if body else getattr(other, method)(path)
        assert r.status_code == 404, (method, path)
    assert other.get("/threads").json()["threads"] == []


def test_thread_limits(monkeypatch):
    c, _, _ = make_user()
    assert c.post("/threads", json={"turns": []}).status_code == 422
    assert c.post("/threads", json={"turns": [TURN] * 51}).status_code == 422
    assert c.post("/threads", json={"turns": [{**TURN, "answer": "x" * 20001}]}).status_code == 422
    assert c.post("/threads", json={"turns": [TURN], "papers": [PAPER] * 51}).status_code == 422
    monkeypatch.setattr(main, "MAX_THREADS_PER_USER", 1)
    c.post("/threads", json={"turns": [TURN]})
    assert c.post("/threads", json={"turns": [TURN]}).status_code == 400


# ------------------------------------------------------------ share links

def test_share_link_is_public_read_only_and_anonymous():
    c, uid, email = make_user()
    tid = c.post("/threads", json={"turns": [TURN], "papers": [PAPER]}).json()["id"]
    shared = c.post(f"/threads/{tid}/share").json()
    token = shared["share_token"]
    assert shared["shared"] is True and len(token) >= 40
    assert c.post(f"/threads/{tid}/share").json()["share_token"] == token  # same link when shared again
    r = TestClient(main.app).get(f"/share/{token}")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex, nofollow"
    data = r.json()
    assert data["title"] == "Does sleep help memory?" and data["turns"][0]["answer"] == "Yes [1]."
    assert email not in r.text and "user_id" not in r.text
    assert set(data) == {"title", "turns", "papers", "shared_at", "updated_at"}  # nothing about the owner


def test_unshare_kills_the_link_and_resharing_makes_a_new_one():
    c, _, _ = make_user()
    tid = c.post("/threads", json={"turns": [TURN]}).json()["id"]
    old = c.post(f"/threads/{tid}/share").json()["share_token"]
    assert c.post(f"/threads/{tid}/unshare").json()["shared"] is False
    assert TestClient(main.app).get(f"/share/{old}").status_code == 404
    new = c.post(f"/threads/{tid}/share").json()["share_token"]
    assert new != old and TestClient(main.app).get(f"/share/{new}").status_code == 200


def test_deleted_thread_link_stops_working_and_bad_tokens_404():
    c, _, _ = make_user()
    tid = c.post("/threads", json={"turns": [TURN]}).json()["id"]
    token = c.post(f"/threads/{tid}/share").json()["share_token"]
    c.delete(f"/threads/{tid}")
    guest = TestClient(main.app)
    assert guest.get(f"/share/{token}").status_code == 404
    for bad in ("short", "x" * 65, "has.dots.in.it.and.more.chars", "%27%20OR%201%3D1%20--aaaaaaaaaaa"):
        assert guest.get(f"/share/{bad}").status_code == 404


def test_shared_link_shows_updates():
    c, _, _ = make_user()
    tid = c.post("/threads", json={"turns": [TURN]}).json()["id"]
    token = c.post(f"/threads/{tid}/share").json()["share_token"]
    c.post(f"/threads/{tid}", json={"turns": [TURN, {**TURN, "question": "Follow-up?"}]})
    assert len(TestClient(main.app).get(f"/share/{token}").json()["turns"]) == 2
