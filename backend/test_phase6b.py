"""Tests for Phase 6b: new sources (DOAJ, ERIC, OpenAIRE, AJOL), Unpaywall free PDF links, source switches and health."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import main
import make_admin
from test_search_filters import FakeClient, FakeResponse, fake_sources, paper  # noqa: F401 (fixture)
from test_security import clean_state  # noqa: F401 (autouse fixture)
from test_phase5 import code_for, make_admin_client  # noqa: F401


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_sources(monkeypatch):
    monkeypatch.setattr(main, "SETTINGS_ENCRYPTION_KEY", make_admin.new_encryption_key())
    db = main.SessionLocal()
    db.query(main.SourceSetting).delete()
    db.commit()
    db.close()
    main.reset_source_switch_cache()
    main._SOURCE_HEALTH.clear()
    main.IN_MEMORY_CACHE.clear()
    yield
    db = main.SessionLocal()
    db.query(main.SourceSetting).delete()
    db.commit()
    db.close()
    main.reset_source_switch_cache()


# ------------------------------------------------------------ query safety

def test_plain_query_strips_search_syntax():
    assert main.plain_query('malaria AND (zambia) OR title:"x" [1 TO 9]') == "malaria zambia title x 1 9"
    assert main.plain_query("::") == ""


# ------------------------------------------------------------ DOAJ

DOAJ = {"results": [
    {"bibjson": {"title": "Malaria in Zambia", "abstract": "<p>We studied malaria.</p>", "year": "2021",
                 "author": [{"name": "A. Banda"}], "journal": {"title": "Medical Journal of Zambia"},
                 "identifier": [{"type": "doi", "id": "10.1/doaj"}, {"type": "eissn", "id": "1234"}]}},
    {"bibjson": {"title": "No abstract", "year": "2020"}},
]}


def test_doaj_parses_and_filters_by_year():
    client = FakeClient(FakeResponse(DOAJ))
    out = run(main.search_doaj(client, "malaria (zambia)", year_min=2020, year_max=2024))
    assert "malaria%20zambia%29%20AND%20bibjson.year%3A%5B2020%20TO%202024%5D" in client.calls[0]["url"]
    assert len(out) == 1
    r = out[0]
    assert r["abstract"] == "We studied malaria." and r["doi"] == "10.1/doaj" and r["publication_year"] == 2021
    assert r["is_open_access"] is True and r["database"] == "DOAJ" and r["description"] == "Medical Journal of Zambia"


# ------------------------------------------------------------ ERIC

def test_eric_parses_and_sends_year_range():
    payload = {"response": {"docs": [
        {"title": "Early literacy", "author": ["Phiri, J."], "description": "A study.", "publicationdateyear": 2022,
         "source": "Reading Journal", "e_fulltextauth": 1},
        {"title": "Closed", "description": "Other.", "publicationdateyear": 2019, "e_fulltextauth": 0},
    ]}}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_eric(client, "early literacy", year_min=2018))
    assert client.calls[0]["params"]["search"] == "(early literacy) AND publicationdateyear:[2018 TO 3000]"
    assert out[0]["is_open_access"] is True and out[1]["is_open_access"] is None
    assert out[0]["database"] == "ERIC" and out[0]["publication_year"] == 2022


# ------------------------------------------------------------ OpenAIRE

def test_openaire_parses_access_and_doi():
    payload = {"results": [
        {"mainTitle": "Water in Lusaka", "descriptions": ["<p>Abstract.</p>"], "publicationDate": "2019-05-01",
         "authors": [{"fullName": "Chanda, P."}], "pids": [{"scheme": "doi", "value": "10.1/oa"}],
         "bestAccessRight": {"label": "OPEN"}, "container": {"name": "Water Journal"}},
        {"mainTitle": "Closed one", "descriptions": ["Text."], "bestAccessRight": {"label": "CLOSED"}},
        {"mainTitle": "No abstract"},
    ]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_openaire(client, "water lusaka", year_min=2015, year_max=2020))
    assert client.calls[0]["params"]["fromPublicationDate"] == "2015-01-01"
    assert client.calls[0]["params"]["toPublicationDate"] == "2020-12-31"
    assert [r["is_open_access"] for r in out] == [True, False]
    assert out[0]["doi"] == "10.1/oa" and out[0]["description"] == "Water Journal" and out[0]["publication_year"] == 2019


# ------------------------------------------------------------ AJOL (OpenAlex filtered to AJOL journals)

def test_ajol_uses_openalex_with_ajol_filter_and_label():
    payload = {"results": [{"title": "Malaria", "publication_year": 2020, "authorships": [],
                            "open_access": {"is_oa": True}, "abstract_inverted_index": {"Malaria": [0]}}]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_ajol(client, "malaria zambia", year_min=2015))
    assert "primary_location.source.host_organization_lineage:P4310321518" in client.calls[0]["params"]["filter"]
    assert "publication_year:>2014" in client.calls[0]["params"]["filter"]
    assert out[0]["database"] == "AJOL"


# ------------------------------------------------------------ failures are recorded

def test_source_failures_feed_health():
    class Boom:
        async def get(self, *a, **k):
            raise RuntimeError("down")

    assert run(main.search_doaj(Boom(), "x y")) == []
    assert run(main.search_crossref(Boom(), "x y")) == []
    assert main._SOURCE_HEALTH["doaj"]["failures"] == 1 and main._SOURCE_HEALTH["doaj"]["last_error"] == "RuntimeError"
    assert main._SOURCE_HEALTH["crossref"]["failures"] == 1


def test_tracked_counts_calls_results_and_time():
    async def two():
        return [1, 2]

    assert run(main.tracked("eric", two())) == [1, 2]
    h = main._SOURCE_HEALTH["eric"]
    assert h["calls"] == 1 and h["results"] == 2 and h["last_ok_at"]


# ------------------------------------------------------------ switches change what /search calls

def test_new_sources_are_searched_and_switched_off_ones_are_not(fake_sources):
    TestClient(main.app).get("/search", params={"q": "sleep"})
    called = {c[0] for c in fake_sources["calls"]}
    assert {"doaj", "eric", "openaire", "ajol"} <= called
    db = main.SessionLocal()
    db.add(main.SourceSetting(name="ajol", enabled=False))
    db.commit()
    db.close()
    main.reset_source_switch_cache()
    main.IN_MEMORY_CACHE.clear()
    fake_sources["calls"].clear()
    TestClient(main.app).get("/search", params={"q": "sleep again"})
    assert "ajol" not in {c[0] for c in fake_sources["calls"]}
    assert main._SOURCE_HEALTH["openalex"]["calls"] >= 1  # searches are timed per source


def test_eric_is_skipped_for_open_access_only(fake_sources):
    TestClient(main.app).get("/search", params={"q": "sleep", "open_access_only": "true"})
    called = {c[0] for c in fake_sources["calls"]}
    assert "eric" not in called and "doaj" in called


# ------------------------------------------------------------ admin Sources tab

def test_admin_sources_list_and_switch():
    c, uid, email, secret = make_admin_client()
    assert c.post("/admin/unlock", json={"code": code_for(secret)}).status_code == 200
    sources = {s["name"]: s for s in c.get("/admin/sources").json()["sources"]}
    assert set(sources) == set(main.SOURCE_REGISTRY)
    assert sources["core"]["key_required"] is True and sources["doaj"]["key_name"] is None
    assert all(s["enabled"] for s in sources.values())
    r = c.post("/admin/sources/doaj", json={"enabled": False})
    assert r.status_code == 200 and main.source_enabled("doaj") is False
    main.reset_source_switch_cache()  # survives a restart (read back from the database)
    assert main.source_enabled("doaj") is False
    assert c.post("/admin/sources/doaj", json={"enabled": True}).status_code == 200
    assert c.post("/admin/sources/nope", json={"enabled": False}).status_code == 404
    entries = c.get("/admin/audit").json()["entries"]
    assert ("source_off", "doaj") in [(e["action"], e["target"]) for e in entries]


def test_last_search_source_cannot_be_switched_off():
    c, uid, email, secret = make_admin_client()
    c.post("/admin/unlock", json={"code": code_for(secret)})
    for name in main.SEARCH_SOURCES[:-1]:
        assert c.post(f"/admin/sources/{name}", json={"enabled": False}).status_code == 200
    assert c.post(f"/admin/sources/{main.SEARCH_SOURCES[-1]}", json={"enabled": False}).status_code == 400
    assert c.post("/admin/sources/unpaywall", json={"enabled": False}).status_code == 200  # not a search source


def test_sources_tab_needs_admin():
    assert TestClient(main.app).get("/admin/sources").status_code == 401


# ------------------------------------------------------------ Unpaywall free PDF links

class UnpaywallClient:
    def __init__(self, *a, **k):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, timeout=None):
        UnpaywallClient.seen.append(url)
        if url.endswith("/10.1/none"):
            return FakeResponse(status_code=404)
        return FakeResponse({"is_oa": True, "best_oa_location": {"url_for_pdf": "https://repo.example/paper.pdf"}})


@pytest.fixture()
def unpaywall(monkeypatch):
    UnpaywallClient.seen = []
    monkeypatch.setattr(main, "UNPAYWALL_ENABLED", True)
    monkeypatch.setattr(main.httpx, "AsyncClient", UnpaywallClient)
    return UnpaywallClient


def test_free_pdf_links_are_added_and_cached(unpaywall):
    papers = [paper("A", 2020, None, "10.1/a"), paper("B", 2020, False, "10.1/none"), paper("C", 2020, None)]
    run(main.add_free_pdf_links(papers))
    assert papers[0]["pdf_url"] == "https://repo.example/paper.pdf" and papers[0]["is_open_access"] is True
    assert "pdf_url" not in papers[1] and "pdf_url" not in papers[2]  # not found; no DOI
    assert len(unpaywall.seen) == 2
    again = [paper("A", 2020, None, "10.1/a")]
    run(main.add_free_pdf_links(again))
    assert again[0]["pdf_url"] and len(unpaywall.seen) == 2  # served from cache


def test_free_pdf_links_respect_switch_and_email(unpaywall, monkeypatch):
    papers = [paper("A", 2020, None, "10.1/a")]
    db = main.SessionLocal()
    db.add(main.SourceSetting(name="unpaywall", enabled=False))
    db.commit()
    db.close()
    main.reset_source_switch_cache()
    run(main.add_free_pdf_links(papers))
    assert unpaywall.seen == []
    main.reset_source_switch_cache()
    db = main.SessionLocal()
    db.query(main.SourceSetting).delete()
    db.commit()
    db.close()
    main.reset_source_switch_cache()
    monkeypatch.setattr(main, "NCBI_EMAIL", "")
    monkeypatch.setattr(main, "CONTACT_EMAIL", "")
    run(main.add_free_pdf_links(papers))
    assert unpaywall.seen == []


def test_only_https_pdf_links_are_kept(unpaywall, monkeypatch):
    async def get(self, url, params=None, timeout=None):
        return FakeResponse({"is_oa": True, "best_oa_location": {"url_for_pdf": "javascript:alert(1)"}})

    monkeypatch.setattr(UnpaywallClient, "get", get)
    papers = [paper("A", 2020, None, "10.1/js")]
    run(main.add_free_pdf_links(papers))
    assert "pdf_url" not in papers[0]
    stored = main.sanitize_paper_for_storage({"title": "T", "pdf_url": "http://insecure.example/x.pdf"})
    assert stored["pdf_url"] is None
