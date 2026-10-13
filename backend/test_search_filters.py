"""Tests for the search filters (year, open access) and for how papers are normalised and de-duplicated."""
import asyncio
import types as pytypes

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def clean_cache():
    main.IN_MEMORY_CACHE.clear()
    yield


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload, self.text, self.status_code = payload, text, status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeClient:
    """Records the params each fetcher sends and replies with a canned response."""

    def __init__(self, response):
        self.response, self.calls = response, []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": headers})
        return self.response


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ filters are sent to every source

def test_openalex_filters_year_and_oa_and_flags_open_access():
    payload = {"results": [
        {"title": "OA paper", "publication_year": 2024, "doi": "https://doi.org/10.1/a", "authorships": [],
         "open_access": {"is_oa": True}, "abstract_inverted_index": {"Sleep": [0], "matters": [1]}},
        {"title": "Closed paper", "publication_year": 2024, "doi": "https://doi.org/10.1/b", "authorships": [],
         "open_access": {"is_oa": False}, "abstract_inverted_index": {"Closed": [0]}},
    ]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_openalex(client, "sleep", year_min=2023, year_max=2025, oa_only=True))
    assert client.calls[0]["params"]["filter"] == "publication_year:>2022,publication_year:<2026,is_oa:true"
    assert [r["is_open_access"] for r in out] == [True, False]


def test_openalex_without_filters_sends_no_filter():
    client = FakeClient(FakeResponse({"results": []}))
    run(main.search_openalex(client, "sleep"))
    assert "filter" not in client.calls[0]["params"]


def test_openalex_never_invents_an_abstract():
    payload = {"results": [{"title": "No abstract here", "publication_year": 2022, "authorships": [],
                            "open_access": {"is_oa": True}}]}
    out = run(main.search_openalex(FakeClient(FakeResponse(payload)), "x"))
    assert out[0]["abstract"] is None


def test_semantic_scholar_year_range_and_oa_flag():
    payload = {"data": [{"title": "T", "abstract": "A", "year": 2024, "externalIds": {"DOI": "10.1/x"},
                         "authors": [], "isOpenAccess": True, "openAccessPdf": {"url": "u"}},
                        {"title": "U", "abstract": "B", "year": 2024, "externalIds": {}, "authors": [],
                         "isOpenAccess": False, "openAccessPdf": None}]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_semantic_scholar(client, "sleep", year_min=2023, oa_only=True))
    assert client.calls[0]["params"]["year"] == "2023-"
    assert "openAccessPdf" in client.calls[0]["params"]
    assert [r["is_open_access"] for r in out] == [True, False]
    client = FakeClient(FakeResponse(payload))
    run(main.search_semantic_scholar(client, "sleep", year_min=2020, year_max=2022))
    assert client.calls[0]["params"]["year"] == "2020-2022"
    assert "openAccessPdf" not in client.calls[0]["params"]


ARXIV_XML = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Pre</title><summary>An abstract.</summary>
<published>2024-05-01T00:00:00Z</published><author><name>A B</name></author></entry></feed>"""


def test_arxiv_date_range_and_always_open_access():
    client = FakeClient(FakeResponse(text=ARXIV_XML))
    out = run(main.search_arxiv(client, "sleep", year_min=2023, year_max=2025))
    assert "submittedDate:[202301010000 TO 202512312359]" in client.calls[0]["params"]["search_query"]
    assert out[0]["is_open_access"] is True and out[0]["publication_year"] == 2024
    client = FakeClient(FakeResponse(text=ARXIV_XML))
    run(main.search_arxiv(client, "sleep"))
    assert "submittedDate" not in client.calls[0]["params"]["search_query"]


def test_crossref_date_filter_and_unknown_open_access():
    payload = {"message": {"items": [{"title": ["T"], "abstract": "<p>Abs</p>", "DOI": "10.1/c",
                                      "published-print": {"date-parts": [[2024]]}}]}}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_crossref(client, "sleep", year_min=2023, year_max=2025))
    assert client.calls[0]["params"]["filter"] == "from-pub-date:2023-01-01,until-pub-date:2025-12-31"
    assert out[0]["is_open_access"] is None and out[0]["abstract"] == "Abs"


def test_core_year_query_and_always_open_access(monkeypatch):
    monkeypatch.setattr(main, "CORE_API_KEY", "key")
    payload = {"results": [{"title": "T", "abstract": "A", "yearPublished": 2024, "authors": [], "doi": None}]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_core(client, "sleep", year_min=2023, year_max=2025))
    q = client.calls[0]["params"]["q"]
    assert "yearPublished>=2023" in q and "yearPublished<=2025" in q
    assert out[0]["is_open_access"] is True


# ------------------------------------------------------------ the /search endpoint

def paper(title, year, oa, doi=None, abstract="An abstract."):
    return {"title": title, "authors": [], "abstract": abstract, "publication_year": year,
            "description": "Test", "doi": doi, "is_open_access": oa}


@pytest.fixture()
def fake_sources(monkeypatch):
    seen = {"calls": []}

    def make(name, papers):
        async def fake(client, q, year_min=None, year_max=None, oa_only=False):
            seen["calls"].append((name, year_min, year_max, oa_only))
            return [dict(p) for p in papers]
        return fake

    monkeypatch.setattr(main, "search_openalex", make("openalex", [
        paper("Old open", 2015, True, "10.1/old"), paper("New open", 2025, True, "10.1/new"),
        paper("New closed", 2025, False, "10.1/closed"), paper("No year", None, True, "10.1/noyear")]))
    monkeypatch.setattr(main, "search_semantic_scholar", make("s2", []))
    monkeypatch.setattr(main, "search_arxiv", make("arxiv", [paper("Preprint", 2025, True)]))
    monkeypatch.setattr(main, "search_core", make("core", []))
    monkeypatch.setattr(main, "search_crossref", make("crossref", [paper("Crossref only", 2025, None, "10.1/cr")]))
    return seen


def titles(resp):
    return sorted(r["title"] for r in resp.json()["results"])


def test_year_filter_keeps_only_matching_years_and_drops_unknown_years(fake_sources):
    r = TestClient(main.app).get("/search", params={"q": "sleep", "year_min": 2020})
    assert r.status_code == 200
    assert "Old open" not in titles(r) and "No year" not in titles(r)
    assert {"New open", "New closed", "Preprint", "Crossref only"} <= set(titles(r))
    r = TestClient(main.app).get("/search", params={"q": "sleep", "year_min": 2010, "year_max": 2016})
    assert titles(r) == ["Old open"]


def test_filters_are_passed_to_every_source(fake_sources):
    TestClient(main.app).get("/search", params={"q": "sleep", "year_min": 2023, "year_max": 2026})
    assert fake_sources["calls"], "no source was called"
    assert all(c[1:] == (2023, 2026, False) for c in fake_sources["calls"])


def test_open_access_filter_only_returns_confirmed_open_access(fake_sources):
    r = TestClient(main.app).get("/search", params={"q": "sleep", "open_access_only": "true"})
    assert r.status_code == 200
    assert set(titles(r)) == {"Old open", "New open", "No year", "Preprint"}
    # unknown (Crossref) and closed papers are excluded; a DOI alone does not make a paper open access
    assert not any(c[0] == "crossref" for c in fake_sources["calls"])


def test_no_filters_returns_everything(fake_sources):
    r = TestClient(main.app).get("/search", params={"q": "sleep"})
    assert len(r.json()["results"]) == 6


def test_combined_filters(fake_sources):
    r = TestClient(main.app).get("/search", params={"q": "sleep", "year_min": 2020, "open_access_only": "true"})
    assert set(titles(r)) == {"New open", "Preprint"}


def test_filters_are_part_of_the_cache_key(fake_sources):
    c = TestClient(main.app)
    a = c.get("/search", params={"q": "cache me"}).json()["count"]
    b = c.get("/search", params={"q": "cache me", "open_access_only": "true"}).json()["count"]
    assert a != b


# ------------------------------------------------------------ de-duplication

def test_dedupe_merges_open_access_abstract_and_year():
    results = [
        paper("Same paper", None, None, "https://doi.org/10.5/z", abstract=None),
        paper("Same paper", 2024, True, "10.5/Z", abstract="Real abstract"),
        paper("Other", 2020, False, "10.5/o"),
    ]
    out = main.deduplicate_results(results)
    assert len(out) == 2
    merged = out[0]
    assert merged["is_open_access"] is True and merged["abstract"] == "Real abstract" and merged["publication_year"] == 2024


def test_dedupe_by_title_when_no_doi():
    out = main.deduplicate_results([paper("Dup", 2020, None), paper("dup", 2020, True)])
    assert len(out) == 1 and out[0]["is_open_access"] is True
