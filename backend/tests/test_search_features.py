"""Tests for the search features: new result fields, study type / citation / preprint / field filters,
sorting, DOI and author search, and the PubMed and Europe PMC sources."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from test_search_filters import FakeClient, FakeResponse, fake_sources, paper, titles  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def clean_cache():
    main.IN_MEMORY_CACHE.clear()
    yield


def run(coro):
    return asyncio.run(coro)


class SequenceClient:
    """Replies with each canned response in turn and records the requests."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        return self.responses.pop(0)


def rich(title, year=2024, cited=None, types=None, preprint=None, retracted=False, field=None, database="OpenAlex",
         doi=None, oa=True):
    p = paper(title, year, oa, doi)
    p.update({"cited_by_count": cited, "study_types": types or [], "is_preprint": preprint,
              "is_retracted": retracted, "field": field, "database": database})
    return p


# ------------------------------------------------------------ study types

def test_title_detection_adds_study_types_and_implied_ones():
    r = main.normalize_result("A randomized controlled trial of melatonin")
    assert r["study_types"] == ["rct", "clinical_trial"]
    r = main.normalize_result("Sleep and memory: a systematic review and meta-analysis")
    assert r["study_types"] == ["meta_analysis", "systematic_review", "review"]
    assert main.normalize_result("Plain observational paper")["study_types"] == []


def test_publication_type_mapping():
    assert main.map_publication_types(["Randomized Controlled Trial", "Clinical Trial, Phase III", "Journal Article"]) \
        == ["rct", "clinical_trial"]


def test_normalize_rejects_bad_values():
    r = main.normalize_result("T", cited_by_count=-3, field="Astrology")
    assert r["cited_by_count"] is None and r["field"] is None and r["is_retracted"] is False


# ------------------------------------------------------------ OpenAlex

def test_openalex_sends_new_filters_and_reads_new_fields():
    payload = {"results": [{
        "title": "A review of sleep", "publication_year": 2023, "doi": "https://doi.org/10.1/r", "authorships": [],
        "open_access": {"is_oa": True}, "abstract_inverted_index": {"Sleep": [0]}, "type": "review",
        "cited_by_count": 120, "is_retracted": True,
        "primary_topic": {"field": {"id": "https://openalex.org/fields/32"}},
    }]}
    client = FakeClient(FakeResponse(payload))
    filters = {"min_citations": 10, "exclude_preprints": True, "study_type": "meta_analysis", "field": "Psychology"}
    out = run(main.search_openalex(client, "sleep", year_min=2020, filters=filters))
    assert client.calls[0]["params"]["filter"] == (
        "publication_year:>2019,cited_by_count:>9,type:!preprint,type:review,primary_topic.field.id:32")
    r = out[0]
    assert r["cited_by_count"] == 120 and r["is_retracted"] is True and r["is_preprint"] is False
    assert r["field"] == "Psychology" and "review" in r["study_types"] and r["database"] == "OpenAlex"


def test_openalex_title_mode_uses_title_filter():
    client = FakeClient(FakeResponse({"results": []}))
    run(main.search_openalex(client, "Attention is all, you need", mode="title"))
    params = client.calls[0]["params"]
    assert "search" not in params and params["filter"] == "title.search:Attention is all  you need"


def test_openalex_author_search_finds_author_then_works():
    client = SequenceClient(
        FakeResponse({"results": [{"id": "https://openalex.org/A123"}]}),
        FakeResponse({"results": [{"title": "Work", "publication_year": 2020, "authorships": [],
                                   "open_access": {"is_oa": False}, "cited_by_count": 5}]}),
    )
    out = run(main.search_openalex_by_author(client, "Jane Doe", year_min=2018))
    assert client.calls[1]["params"]["filter"] == "author.id:A123,publication_year:>2017"
    assert client.calls[1]["params"]["sort"] == "cited_by_count:desc"
    assert out[0]["title"] == "Work"


def test_openalex_author_search_rejects_odd_ids():
    client = SequenceClient(FakeResponse({"results": [{"id": "https://openalex.org/../x"}]}))
    assert run(main.search_openalex_by_author(client, "x")) == []
    assert len(client.calls) == 1


# ------------------------------------------------------------ Semantic Scholar, arXiv, Crossref

def test_semantic_scholar_new_filters_and_fields():
    payload = {"data": [{"title": "T", "abstract": "A", "year": 2024, "externalIds": {}, "authors": [],
                         "isOpenAccess": False, "venue": "medRxiv", "citationCount": 7,
                         "publicationTypes": ["MetaAnalysis"], "s2FieldsOfStudy": [{"category": "Medicine"}]}]}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_semantic_scholar(client, "x", filters={"min_citations": 5, "study_type": "rct",
                                                                   "field": "Social Sciences"}))
    params = client.calls[0]["params"]
    assert params["minCitationCount"] == "5" and params["publicationTypes"] == "ClinicalTrial"
    assert params["fieldsOfStudy"] == "Sociology,Political Science,Education,Law,Linguistics"
    r = out[0]
    assert r["cited_by_count"] == 7 and r["is_preprint"] is True and r["field"] == "Medicine"
    assert r["study_types"] == ["meta_analysis", "review"]


def test_arxiv_is_skipped_when_preprints_are_excluded():
    client = FakeClient(FakeResponse(text=""))
    assert run(main.search_arxiv(client, "x", filters={"exclude_preprints": True})) == []
    assert client.calls == []


def test_crossref_reads_type_and_citations():
    payload = {"message": {"items": [{"title": ["T"], "abstract": "<p>A</p>", "DOI": "10.1/c", "type": "posted-content",
                                      "is-referenced-by-count": 3, "posted": {"date-parts": [[2025]]}}]}}
    out = run(main.search_crossref(FakeClient(FakeResponse(payload)), "x"))
    assert out[0]["is_preprint"] is True and out[0]["cited_by_count"] == 3 and out[0]["publication_year"] == 2025


# ------------------------------------------------------------ PubMed

PUBMED_XML = """<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
<Journal><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue><Title>The Lancet</Title></Journal>
<ArticleTitle>Melatonin for <i>sleep</i></ArticleTitle>
<Abstract><AbstractText Label="METHODS">We randomised 200 adults.</AbstractText>
<AbstractText Label="RESULTS">Sleep improved.</AbstractText></Abstract>
<AuthorList><Author><LastName>Smith</LastName><ForeName>Ann</ForeName></Author></AuthorList>
<PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType>
<PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
</Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/pm</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
<PubmedArticle><MedlineCitation><Article><ArticleTitle>No abstract</ArticleTitle></Article></MedlineCitation>
</PubmedArticle></PubmedArticleSet>"""


def test_pubmed_two_step_search_and_parsing(monkeypatch):
    monkeypatch.setattr(main, "NCBI_API_KEY", "k")
    monkeypatch.setattr(main, "NCBI_EMAIL", "dev@example.org")
    client = SequenceClient(FakeResponse({"esearchresult": {"idlist": ["111", "222"]}}),
                            FakeResponse(text=PUBMED_XML))
    out = run(main.search_pubmed(client, "melatonin", year_min=2020,
                                 filters={"study_type": "rct", "exclude_preprints": True}))
    first = client.calls[0]["params"]
    assert first["term"] == "(melatonin) AND randomized controlled trial[pt] NOT preprint[pt]"
    assert first["mindate"] == 2020 and first["maxdate"] == 3000 and first["datetype"] == "pdat"
    assert first["api_key"] == "k" and first["email"] == "dev@example.org"
    assert client.calls[1]["params"]["id"] == "111,222"
    assert len(out) == 1  # the record without an abstract is skipped
    r = out[0]
    assert r["title"] == "Melatonin for sleep" and r["authors"] == ["Ann Smith"] and r["publication_year"] == 2022
    assert r["abstract"] == "METHODS: We randomised 200 adults.\nRESULTS: Sleep improved."
    assert r["doi"] == "10.1/pm" and r["description"] == "The Lancet" and r["database"] == "PubMed"
    assert r["is_retracted"] is True and r["is_preprint"] is False and "rct" in r["study_types"]


def test_pubmed_without_key_sends_no_key_and_stops_on_no_ids(monkeypatch):
    monkeypatch.setattr(main, "NCBI_API_KEY", "")
    client = SequenceClient(FakeResponse({"esearchresult": {"idlist": []}}))
    assert run(main.search_pubmed(client, "x")) == []
    assert "api_key" not in client.calls[0]["params"] and len(client.calls) == 1


# ------------------------------------------------------------ Europe PMC

def test_europepmc_query_and_parsing():
    payload = {"resultList": {"result": [
        {"title": "A <i>trial</i>", "abstractText": "<h4>Aim</h4>Test it.", "authorString": "Lee K, Park J.",
         "pubYear": "2021", "journalInfo": {"journal": {"title": "BMJ"}}, "doi": "10.1/e", "isOpenAccess": "Y",
         "citedByCount": 12, "pubTypeList": {"pubType": ["Meta-Analysis", "Retracted Publication"]}, "source": "MED"},
        {"title": "Preprint", "abstractText": "X", "source": "PPR", "pubYear": "2024"},
        {"title": "No abstract", "pubYear": "2020"},
    ]}}
    client = FakeClient(FakeResponse(payload))
    out = run(main.search_europepmc(client, "x", year_min=2020, year_max=2024, oa_only=True,
                                    filters={"study_type": "meta_analysis", "exclude_preprints": True}))
    assert client.calls[0]["params"]["query"] == (
        '(x) AND PUB_YEAR:[2020 TO 2024] AND OPEN_ACCESS:y AND PUB_TYPE:"Meta-Analysis" NOT SRC:PPR')
    assert len(out) == 2
    r = out[0]
    assert r["title"] == "A trial" and r["abstract"] == "Aim Test it." and r["authors"] == ["Lee K", "Park J"]
    assert r["is_open_access"] is True and r["cited_by_count"] == 12 and r["is_retracted"] is True
    assert r["study_types"] == ["meta_analysis", "review"] and r["database"] == "Europe PMC"
    assert out[1]["is_preprint"] is True


# ------------------------------------------------------------ DOI detection and lookup

@pytest.mark.parametrize("query,expected", [
    ("10.1038/nature12373", "10.1038/nature12373"),
    ("https://doi.org/10.1038/NATURE12373", "10.1038/nature12373"),
    ("doi:10.1000/xyz123.", "10.1000/xyz123"),
    ("sleep and memory", None),
    ("effects of 10.5 mg dose", None),
    ("see 10.1038/nature12373 for details", None),
])
def test_extract_doi_query(query, expected):
    assert main.extract_doi_query(query) == expected


def test_lookup_doi_falls_back_to_crossref():
    client = SequenceClient(
        FakeResponse(status_code=404),
        FakeResponse({"message": {"title": ["Found"], "DOI": "10.1/x", "published": {"date-parts": [[2019]]}}}),
    )
    out = run(main.lookup_doi(client, "10.1/x"))
    assert "openalex.org/works/doi:10.1/x" in client.calls[0]["url"]
    assert out[0]["title"] == "Found" and out[0]["abstract"] is None


# ------------------------------------------------------------ /search endpoint

@pytest.fixture()
def rich_sources(monkeypatch, fake_sources):
    async def openalex(client, q, year_min=None, year_max=None, oa_only=False, filters=None, **kwargs):
        return [
            rich("Cited review", 2019, cited=500, types=["review"], field="Medicine"),
            rich("Fresh trial", 2025, cited=3, types=["rct", "clinical_trial"], field="Medicine"),
            rich("Unknown citations", 2022, cited=None, field="Psychology"),
            rich("A preprint", 2024, cited=50, preprint=True, field="Medicine"),
        ]

    async def pubmed(client, q, year_min=None, year_max=None, oa_only=False, filters=None, **kwargs):
        return [rich("Retracted trial", 2021, retracted=True, types=["rct", "clinical_trial"], database="PubMed",
                     oa=None)]

    monkeypatch.setattr(main, "search_openalex", openalex)
    monkeypatch.setattr(main, "search_pubmed", pubmed)
    monkeypatch.setattr(main, "search_arxiv", lambda *a, **k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(main, "search_crossref", lambda *a, **k: asyncio.sleep(0, result=[]))
    return fake_sources


def get(params):
    return TestClient(main.app).get("/search", params=params)


def test_min_citations_drops_low_and_unknown(rich_sources):
    assert titles(get({"q": "sleep", "min_citations": 10})) == ["A preprint", "Cited review"]


def test_exclude_preprints(rich_sources):
    assert "A preprint" not in titles(get({"q": "sleep", "exclude_preprints": "true"}))


def test_study_type_filter(rich_sources):
    assert titles(get({"q": "sleep", "study_type": "rct"})) == ["Fresh trial", "Retracted trial"]


def test_field_filter(rich_sources):
    assert titles(get({"q": "sleep", "field": "Psychology"})) == ["Unknown citations"]


def test_new_filters_reach_the_sources(rich_sources):
    get({"q": "sleep", "min_citations": 5, "study_type": "review", "field": "Medicine", "exclude_preprints": "true"})
    sent = dict(rich_sources["filters"])
    assert sent["europepmc"] == {"min_citations": 5, "exclude_preprints": True, "study_type": "review",
                                 "field": "Medicine"}


def test_unknown_filter_values_are_rejected(rich_sources):
    assert get({"q": "sleep", "study_type": "astrology"}).status_code == 400
    assert get({"q": "sleep", "field": "Astrology"}).status_code == 400
    assert get({"q": "sleep", "sort": "random"}).status_code == 422


def test_sorting(rich_sources):
    newest = [r["title"] for r in get({"q": "sleep", "sort": "newest"}).json()["results"]]
    assert newest[:2] == ["Fresh trial", "A preprint"]
    cited = [r["title"] for r in get({"q": "sleep", "sort": "most_cited"}).json()["results"]]
    assert cited[0] == "Cited review" and cited[-1] in ("Unknown citations", "Retracted trial")


def test_retracted_papers_are_kept_and_flagged(rich_sources):
    results = get({"q": "sleep"}).json()["results"]
    assert next(r for r in results if r["title"] == "Retracted trial")["is_retracted"] is True


def test_source_counts_are_per_database(rich_sources):
    data = get({"q": "sleep"}).json()
    assert data["source_counts"] == {"OpenAlex": 4, "PubMed": 1}
    assert data["search_mode"] == "topic"


def test_open_access_search_skips_pubmed_and_crossref(fake_sources):
    get({"q": "sleep", "open_access_only": "true"})
    called = {c[0] for c in fake_sources["calls"]}
    assert "pubmed" not in called and "crossref" not in called and "europepmc" in called


def test_doi_query_is_looked_up_directly(fake_sources):
    data = get({"q": "https://doi.org/10.1234/ABC"}).json()
    assert data["search_mode"] == "doi" and titles_of(data) == ["Found by DOI"]
    assert fake_sources["calls"] == [("doi", "10.1234/abc")]


def test_bad_doi_prefix_is_rejected(fake_sources):
    assert get({"q": "doi: not a doi"}).status_code == 400


def test_author_prefix_and_parameter(fake_sources):
    data = get({"q": "author: Jane Doe"}).json()
    assert data["search_mode"] == "author" and titles_of(data) == ["By author"]
    assert ("author", "Jane Doe") in fake_sources["calls"]
    data = get({"q": "Jane Doe", "search_by": "author"}).json()
    assert data["search_mode"] == "author"


def test_title_mode_queries_openalex_by_title(fake_sources):
    data = get({"q": "title: Deep residual learning"}).json()
    assert data["search_mode"] == "title"
    assert ("openalex", "Deep residual learning", "title") in fake_sources["queries"]
    assert {q[0] for q in fake_sources["queries"]} == {"openalex", "s2"}


def test_new_filters_are_part_of_the_cache_key(rich_sources):
    a = get({"q": "cache me"}).json()["count"]
    b = get({"q": "cache me", "study_type": "rct"}).json()["count"]
    c = get({"q": "cache me", "sort": "newest"}).json()["results"][0]["title"]
    assert a != b and c == "Fresh trial"


def titles_of(data):
    return sorted(r["title"] for r in data["results"])


# ------------------------------------------------------------ merging and storage

def test_dedupe_merges_citations_retraction_types_and_field():
    a = rich("Same", cited=5, types=["review"], doi="10.9/s", field=None)
    b = rich("Same", cited=80, types=["meta_analysis"], retracted=True, doi="10.9/S", field="Medicine",
             preprint=False)
    a["is_preprint"] = None
    out = main.deduplicate_results([a, b])
    assert len(out) == 1
    m = out[0]
    assert m["cited_by_count"] == 80 and m["is_retracted"] is True and m["field"] == "Medicine"
    assert m["study_types"] == ["meta_analysis", "review"] and m["is_preprint"] is False


def test_sanitize_keeps_new_fields_and_drops_bad_values():
    clean = main.sanitize_paper_for_storage({
        "title": "T", "cited_by_count": 9, "study_types": ["rct", "bogus"], "is_retracted": True,
        "is_preprint": "yes", "field": "Medicine", "database": "PubMed",
    })
    assert clean["cited_by_count"] == 9 and clean["study_types"] == ["rct"] and clean["is_retracted"] is True
    assert clean["is_preprint"] is None and clean["field"] == "Medicine" and clean["database"] == "PubMed"
    assert main.sanitize_paper_for_storage({"title": "T", "cited_by_count": True})["cited_by_count"] is None
