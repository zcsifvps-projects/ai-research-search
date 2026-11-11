"""Tests for Phase 3: research mode rules, medical handling, the evidence meter, deep research and its caps."""
import json

import pytest
from fastapi.testclient import TestClient

import main
from test_security import FakeGemini, FakeGroq, ask_body, clean_state, use_fake_ai  # noqa: F401 (autouse fixture)


def client():
    return TestClient(main.app)


class SequenceGemini(FakeGemini):
    """Replies with each canned text in turn (deep research makes a plan call, then a synthesis call)."""

    def __init__(self, *texts):
        super().__init__()
        self.texts = list(texts)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        item = self.texts.pop(0)
        if isinstance(item, Exception):
            raise item
        import types as pytypes
        return pytypes.SimpleNamespace(text=item)


# ------------------------------------------------------------ mode rules

@pytest.mark.parametrize("question,mode", [
    ("Is metformin safe in pregnancy?", "medical"),
    ("Does melatonin help insomnia in older adults?", "medical"),
    ("What is CRISPR?", "quick"),
    ("transformer attention mechanism", "quick"),
    ("How does remote work affect productivity across industries?", "deep"),
    ("Compare solar and wind energy costs", "deep"),
    ("State of research on quantum error correction", "deep"),
    ("Compare treatments for type 2 diabetes", "medical"),  # medical wins over deep
])
def test_detect_research_mode(question, mode):
    assert main.detect_research_mode(question) == mode


def test_long_multi_part_question_is_deep():
    q = ("How do school feeding programmes influence attendance and learning outcomes and what role do "
         "local food systems and government budgets play in rural Zambia")
    assert main.detect_research_mode(q) == "deep"


def test_mode_endpoint():
    assert client().get("/mode", params={"q": "Is aspirin good for stroke prevention?"}).json() == {"mode": "medical"}
    assert client().get("/mode", params={"q": "x" * 2001}).status_code == 422


# ------------------------------------------------------------ medical ranking

def test_rank_by_evidence_moves_strong_designs_up_within_relevance():
    papers = [
        {"title": "Cohort", "similarity_score": 80.0, "study_types": []},
        {"title": "Meta", "similarity_score": 70.0, "study_types": ["meta_analysis", "review"]},
        {"title": "Unrelated meta", "similarity_score": 40.0, "study_types": ["meta_analysis"]},
    ]
    assert [p["title"] for p in main.rank_by_evidence(papers)] == ["Meta", "Cohort", "Unrelated meta"]


def test_rank_by_evidence_without_scores_is_stable_by_level():
    papers = [{"title": "A", "study_types": []}, {"title": "B", "study_types": ["rct"]}, {"title": "C"}]
    assert [p["title"] for p in main.rank_by_evidence(papers)] == ["B", "A", "C"]


# ------------------------------------------------------------ evidence meter

def test_clean_meter_counts_valid_labels_only():
    raw = {"is_yes_no": True, "labels": [
        {"paper_id": 1, "label": "yes"}, {"paper_id": 2, "label": "YES"}, {"paper_id": 2, "label": "no"},
        {"paper_id": 3, "label": "possibly"}, {"paper_id": 9, "label": "no"}, {"paper_id": 4, "label": "maybe"}]}
    meter = main.clean_meter(raw, {1, 2, 3, 4})
    assert meter == {"yes": 2, "no": 0, "possibly": 1, "total": 3, "labels": [
        {"paper_id": 1, "label": "yes"}, {"paper_id": 2, "label": "yes"}, {"paper_id": 3, "label": "possibly"}]}


def test_clean_meter_hidden_for_non_yes_no_or_too_few():
    assert main.clean_meter({"is_yes_no": False, "labels": []}, {1}) is None
    assert main.clean_meter({"is_yes_no": True, "labels": [{"paper_id": 1, "label": "yes"}]}, {1}) is None
    assert main.clean_meter(None, {1}) is None


def test_ask_returns_meter(monkeypatch):
    many = [{"title": f"P{i}", "abstract": f"Abstract {i}."} for i in range(1, 5)]
    text = json.dumps({"answer": "Mostly yes [1].", "citations": [], "takeaways": [],
                       "meter": {"is_yes_no": True, "labels": [{"paper_id": i, "label": "yes" if i < 4 else "no"}
                                                               for i in range(1, 5)]}})
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=text))
    body = {"question": "Does it work?", "results": many, "session_id": "guest-session-0001-abcd"}
    meter = client().post("/ask", json=body).json()["meter"]
    assert meter["yes"] == 3 and meter["no"] == 1 and meter["total"] == 4


def test_ask_schema_has_meter_for_both_providers():
    assert "meter" in main.ASK_SCHEMA["required"]
    gem = main.gemini_safe_schema(main.ASK_SCHEMA)
    assert "additionalProperties" not in json.dumps(gem)
    assert gem["properties"]["meter"]["properties"]["labels"]["items"]["properties"]["label"]["enum"] == ["yes", "no", "possibly"]


# ------------------------------------------------------------ medical /ask

def test_medical_ask_adds_guidance_and_disclaimer(monkeypatch):
    gemini = FakeGemini(text='{"answer": "Evidence is mixed [1].", "citations": []}')
    use_fake_ai(monkeypatch, gemini=gemini)
    r = client().post("/ask", json=ask_body("Is drug X safe?", mode="medical"))
    assert r.status_code == 200
    assert r.json()["answer"].startswith("Evidence is mixed [1].") and "Not medical advice" in r.json()["answer"]
    prompt = gemini.calls[0]["contents"][0].parts[0].text
    assert "meta-analyses" in prompt and "Never give personal medical advice" in prompt


def test_quick_ask_has_no_disclaimer_and_bad_mode_rejected(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"answer": "A [1].", "citations": []}'))
    assert "Not medical advice" not in client().post("/ask", json=ask_body("Plain question")).json()["answer"]
    assert client().post("/ask", json=ask_body("Bad mode", mode="deep")).status_code == 422


def test_medical_and_quick_answers_are_cached_separately(monkeypatch):
    gemini = FakeGemini(text='{"answer": "A [1].", "citations": []}')
    use_fake_ai(monkeypatch, gemini=gemini)
    client().post("/ask", json=ask_body("Same question"))
    client().post("/ask", json=ask_body("Same question", mode="medical"))
    assert len(gemini.calls) == 2


# ------------------------------------------------------------ /search medical flag

def test_search_medical_flag_ranks_evidence(monkeypatch):
    async def fake(client_, q, year_min=None, year_max=None, oa_only=False, filters=None, **kwargs):
        return [
            {**main.normalize_result("Plain cohort study", abstract="a", doi="10.1/a"), "database": "OpenAlex"},
            {**main.normalize_result("A meta-analysis of drug X", abstract="b", doi="10.1/b"), "database": "OpenAlex"},
        ]

    async def empty(*a, **k):
        return []

    for name in ("search_semantic_scholar", "search_arxiv", "search_core", "search_crossref", "search_pubmed",
                 "search_europepmc"):
        monkeypatch.setattr(main, name, empty)
    monkeypatch.setattr(main, "search_openalex", fake)
    plain = client().get("/search", params={"q": "drug x"}).json()["results"]
    med = client().get("/search", params={"q": "drug x", "medical": "true"}).json()["results"]
    assert plain[0]["title"] == "Plain cohort study"
    assert med[0]["title"] == "A meta-analysis of drug X"


# ------------------------------------------------------------ /deep-research

PLAN = json.dumps({"queries": ["remote work productivity", "remote work productivity", "telework output", "  "]})


def deep_answer(ids=(1, 2)):
    return json.dumps({
        "answer": "## Overview\nIt depends [1][2].",
        "citations": [{"paper_id": i, "title": "x"} for i in ids] + [{"paper_id": 99, "title": "invented"}],
        "takeaways": [{"paper_id": 1, "takeaway": "Output rose."}],
        "meter": {"is_yes_no": False, "labels": []},
    })


@pytest.fixture()
def fake_search(monkeypatch):
    calls = []

    async def fake(q, year_min=None, year_max=None, open_access_only=False, min_citations=None,
                   exclude_preprints=False, study_type=None, field=None, sort="relevance", search_by="topic",
                   medical=False):
        calls.append({"q": q, "year_min": year_min, "study_type": study_type, "medical": medical})
        return {"results": [
            {**main.normalize_result(f"Paper about {q}", abstract=f"Abstract {q}", doi=f"10.1/{q[:5]}"),
             "database": "OpenAlex"},
            {**main.normalize_result("Shared paper", abstract="Shared abstract", doi="10.1/shared"),
             "database": "PubMed"},
            {**main.normalize_result("Retracted paper", abstract="Bad", doi="10.1/bad", is_retracted=True),
             "database": "OpenAlex"},
            {**main.normalize_result("No abstract paper", abstract=None, doi="10.1/none"), "database": "Crossref"},
        ]}

    monkeypatch.setattr(main, "search", fake)
    return calls


def deep_body(question="How does remote work affect productivity across industries?", **extra):
    return {"question": question, "session_id": "guest-session-0001-abcd", **extra}


def test_deep_research_full_flow(monkeypatch, fake_search):
    gemini = SequenceGemini(PLAN, deep_answer())
    use_fake_ai(monkeypatch, gemini=gemini)
    r = client().post("/deep-research", json=deep_body(year_min=2020))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mode"] == "deep" and data["medical"] is False
    assert data["sub_queries"] == ["remote work productivity", "telework output"]  # duplicates and blanks removed
    assert [c["q"] for c in fake_search] == data["sub_queries"] and all(c["year_min"] == 2020 for c in fake_search)
    titles = [p["title"] for p in data["results"]]
    assert titles.count("Shared paper") == 1  # merged across sub-searches
    assert data["papers_used"] == 3  # retracted and abstract-less papers are not evidence
    assert [c["paper_id"] for c in data["citations"]] == [1, 2]  # invented id dropped
    assert data["meter"] is None and data["source_counts"]
    assert len(gemini.calls) == 2
    synthesis_prompt = gemini.calls[1]["contents"][0].parts[0].text
    assert "Retracted paper" not in synthesis_prompt and "## Evidence table" in synthesis_prompt


def test_deep_research_planning_failure_falls_back_to_question(monkeypatch, fake_search):
    use_fake_ai(monkeypatch, gemini=SequenceGemini(RuntimeError("plan broke"), RuntimeError("plan broke again"),
                                                     deep_answer()))
    r = client().post("/deep-research", json=deep_body("solar panel efficiency trends"))
    assert r.status_code == 200
    assert len(r.json()["sub_queries"]) == 1


def test_deep_research_medical_detected_adds_disclaimer(monkeypatch, fake_search):
    use_fake_ai(monkeypatch, gemini=SequenceGemini(PLAN, deep_answer()))
    data = client().post("/deep-research", json=deep_body("Compare treatments for type 2 diabetes")).json()
    assert data["medical"] is True and "Not medical advice" in data["answer"]
    assert all(c["medical"] is True for c in fake_search)


def test_deep_research_is_cached_and_saved_to_history(monkeypatch, fake_search):
    gemini = SequenceGemini(PLAN, deep_answer())
    use_fake_ai(monkeypatch, gemini=gemini)
    body = {**deep_body(), "session_id": "deep-history-session-0001"}
    first = client().post("/deep-research", json=body).json()
    second = client().post("/deep-research", json=body).json()
    assert first == second and len(gemini.calls) == 2
    history = client().get("/history/deep-history-session-0001").json()["history"]
    assert len(history) == 2 and history[0]["papers"]


def test_deep_research_cap(monkeypatch, fake_search):
    monkeypatch.setattr(main, "DEEP_DAILY_LIMIT_GUEST", 1)
    use_fake_ai(monkeypatch, gemini=SequenceGemini(PLAN, deep_answer(), PLAN, deep_answer()))
    assert client().post("/deep-research", json=deep_body("first question about trends")).status_code == 200
    r = client().post("/deep-research", json=deep_body("second question about trends"))
    assert r.status_code == 429 and "quick answer" in r.json()["detail"]


def test_deep_research_counts_two_ai_calls(monkeypatch, fake_search):
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 1)
    use_fake_ai(monkeypatch, gemini=SequenceGemini(PLAN, deep_answer()))
    assert client().post("/deep-research", json=deep_body()).status_code == 429


def test_deep_research_validation(monkeypatch, fake_search):
    use_fake_ai(monkeypatch, gemini=SequenceGemini())
    c = client()
    assert c.post("/deep-research", json=deep_body("   ")).status_code == 400
    assert c.post("/deep-research", json=deep_body(study_type="astrology")).status_code == 400
    assert c.post("/deep-research", json=deep_body(field="Astrology")).status_code == 400
    assert c.post("/deep-research", json=deep_body("x" * 2001)).status_code == 422
    assert c.post("/deep-research", json={**deep_body(), "session_id": "short"}).status_code == 422


def test_deep_research_no_evidence(monkeypatch):
    async def nothing(**kwargs):
        return {"results": [{**main.normalize_result("No abstract", abstract=None), "database": "OpenAlex"}]}

    monkeypatch.setattr(main, "search", nothing)
    use_fake_ai(monkeypatch, gemini=SequenceGemini(PLAN))
    r = client().post("/deep-research", json=deep_body())
    assert r.status_code == 400 and "No papers with an abstract" in r.json()["detail"]


def test_deep_research_synthesis_failure_is_generic(monkeypatch, fake_search):
    use_fake_ai(monkeypatch, gemini=SequenceGemini(PLAN, RuntimeError("boom /etc/secret"),
                                                     RuntimeError("boom /etc/secret")))
    r = client().post("/deep-research", json=deep_body())
    assert r.status_code == 502 and "secret" not in r.text


def test_deep_research_falls_back_to_groq(monkeypatch, fake_search):
    groq = FakeGroq([PLAN, deep_answer()])
    use_fake_ai(monkeypatch, gemini=FakeGemini(error=RuntimeError("503 UNAVAILABLE")), groq=groq)
    assert client().post("/deep-research", json=deep_body()).status_code == 200
    assert len(groq.calls) == 2


def test_new_routes_are_rate_limited():
    limits = main.RateLimiterMiddleware(main.app).limits
    assert limits["/deep-research"] == (5, 60) and limits["/mode"] == (60, 60)


def test_clean_search_queries():
    assert main.clean_search_queries(['  "a b c?" ', "A B C", "x", 5, "d e f", "g h i", "j k l", "m n o", "p q r"]) \
        == ["a b c", "d e f", "g h i", "j k l", "m n o"]
