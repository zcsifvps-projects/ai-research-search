"""Tests for takeaways from /ask, POST /takeaways and POST /compare."""
import json

from fastapi.testclient import TestClient

import main
from test_security import FakeGemini, FakeGroq, ask_body, clean_state, use_fake_ai  # noqa: F401 (autouse fixture)


def paper(pid, title=None, abstract="An abstract with a finding.", **extra):
    return {"paper_id": pid, "title": title or f"Paper {pid}", "abstract": abstract, **extra}


def client():
    return TestClient(main.app)


# ------------------------------------------------------------ /ask takeaways

def test_ask_returns_takeaways_and_drops_invented_ids(monkeypatch):
    text = json.dumps({"answer": "Sleep matters [1].", "citations": [{"paper_id": 1, "title": "x"}],
                       "takeaways": [{"paper_id": 1, "takeaway": " Sleep loss hurts memory. "},
                                     {"paper_id": 1, "takeaway": "duplicate"},
                                     {"paper_id": 42, "takeaway": "invented"},
                                     {"paper_id": 2, "takeaway": "x" * 500}]})
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=text))
    r = client().post("/ask", json=ask_body("Takeaway check"))
    assert r.status_code == 200
    tk = r.json()["takeaways"]
    assert tk[0] == {"paper_id": 1, "takeaway": "Sleep loss hurts memory."}
    assert [t["paper_id"] for t in tk] == [1, 2] and len(tk[1]["takeaway"]) == main.MAX_TAKEAWAY_CHARS


def test_ask_without_takeaways_in_reply_still_works(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"answer": "A [1].", "citations": []}'))
    r = client().post("/ask", json=ask_body("Old style reply"))
    assert r.status_code == 200 and r.json()["takeaways"] == []


def test_ask_schema_requires_takeaways_for_groq_strict_mode():
    assert "takeaways" in main.ASK_SCHEMA["required"]
    assert "additionalProperties" not in json.dumps(main.gemini_safe_schema(main.ASK_SCHEMA))


def test_ask_takeaways_are_reused_by_takeaways_endpoint(monkeypatch):
    text = json.dumps({"answer": "A [1].", "citations": [],
                       "takeaways": [{"paper_id": 1, "takeaway": "From ask."}]})
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=text))
    client().post("/ask", json=ask_body("Reuse check"))
    gemini = FakeGemini(text='{"takeaways": [{"paper_id": 1, "takeaway": "should not be called"}]}')
    use_fake_ai(monkeypatch, gemini=gemini)
    r = client().post("/takeaways", json={"papers": [
        {"paper_id": 1, "title": "Paper One", "abstract": "Sleep deprivation impairs memory consolidation."}]})
    assert r.json()["takeaways"] == [{"paper_id": 1, "takeaway": "From ask."}]
    assert gemini.calls == []


# ------------------------------------------------------------ /takeaways

def test_takeaways_skips_missing_abstract_and_retracted_without_ai(monkeypatch):
    gemini = FakeGemini(text='{"takeaways": [{"paper_id": 1, "takeaway": "x"}]}')
    use_fake_ai(monkeypatch, gemini=gemini)
    r = client().post("/takeaways", json={"papers": [paper(1, abstract=None), paper(2, is_retracted=True),
                                                     paper(3, abstract="No abstract available.")]})
    assert r.status_code == 200 and r.json() == {"takeaways": []}
    assert gemini.calls == []


def test_takeaways_calls_ai_once_then_uses_cache(monkeypatch):
    gemini = FakeGemini(text='{"takeaways": [{"paper_id": 1, "takeaway": "One."}, {"paper_id": 2, "takeaway": "Two."},'
                             ' {"paper_id": 9, "takeaway": "invented"}]}')
    use_fake_ai(monkeypatch, gemini=gemini)
    body = {"papers": [paper(1), paper(2, abstract="Other abstract.")]}
    r = client().post("/takeaways", json=body)
    assert r.json()["takeaways"] == [{"paper_id": 1, "takeaway": "One."}, {"paper_id": 2, "takeaway": "Two."}]
    assert len(gemini.calls) == 1
    client().post("/takeaways", json=body)
    assert len(gemini.calls) == 1  # cache hit, no new AI call


def test_takeaways_counts_against_ai_quota(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"takeaways": [{"paper_id": 1, "takeaway": "One."}]}'))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 1)
    assert client().post("/takeaways", json={"papers": [paper(1, abstract="A.")]}).status_code == 200
    assert client().post("/takeaways", json={"papers": [paper(1, abstract="B.")]}).status_code == 429


def test_takeaways_limits():
    c = client()
    assert c.post("/takeaways", json={"papers": []}).status_code == 422
    assert c.post("/takeaways", json={"papers": [paper(i) for i in range(1, 12)]}).status_code == 422
    assert c.post("/takeaways", json={"papers": [paper(1), paper(1)]}).status_code == 400
    assert c.post("/takeaways", json={"papers": [paper(0)]}).status_code == 422
    assert c.post("/takeaways", json={"papers": [paper(1, abstract="x" * 10001)]}).status_code == 422


def test_takeaways_ai_failure_is_generic(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(error=RuntimeError("boom /etc/secret")))
    r = client().post("/takeaways", json={"papers": [paper(1)]})
    assert r.status_code == 502 and "secret" not in r.text


# ------------------------------------------------------------ /compare

COMPARE_JSON = json.dumps({
    "summary": "Both found effects [1][2].",
    "papers": [
        {"paper_id": 1, "study_type": "RCT", "population": "Adults", "sample_size": "200", "methods": "Trial",
         "main_finding": "Better sleep", "limitations": "Short"},
        {"paper_id": 2, "study_type": "", "population": "Mice", "sample_size": "Not stated", "methods": "Lab",
         "main_finding": "Effect", "limitations": "Animal model"},
        {"paper_id": 7, "study_type": "invented", "population": "", "sample_size": "", "methods": "",
         "main_finding": "", "limitations": ""},
    ],
})


def test_compare_builds_rows_in_order_and_fills_gaps(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=COMPARE_JSON))
    r = client().post("/compare", json={"papers": [paper(1, publication_year=2020), paper(2), paper(3)]})
    assert r.status_code == 200
    data = r.json()
    assert data["summary"] == "Both found effects [1][2]."
    assert [row["paper_id"] for row in data["rows"]] == [1, 2, 3]
    assert data["rows"][0]["study_type"] == "RCT" and data["rows"][0]["publication_year"] == 2020
    assert data["rows"][1]["study_type"] == "Not stated"  # empty value from the model
    assert all(data["rows"][2][f] == "Not stated" for f in main.COMPARE_FIELDS)  # paper the model skipped


def test_compare_needs_two_usable_papers(monkeypatch):
    gemini = FakeGemini(text=COMPARE_JSON)
    use_fake_ai(monkeypatch, gemini=gemini)
    r = client().post("/compare", json={"papers": [paper(1), paper(2, is_retracted=True)]})
    assert r.status_code == 400 and gemini.calls == []
    assert client().post("/compare", json={"papers": [paper(1)]}).status_code == 422
    assert client().post("/compare", json={"papers": [paper(i) for i in range(1, 7)]}).status_code == 422


def test_compare_is_cached(monkeypatch):
    gemini = FakeGemini(text=COMPARE_JSON)
    use_fake_ai(monkeypatch, gemini=gemini)
    body = {"papers": [paper(1), paper(2)]}
    first = client().post("/compare", json=body).json()
    assert client().post("/compare", json=body).json() == first
    assert len(gemini.calls) == 1


def test_compare_falls_back_to_groq(monkeypatch):
    groq = FakeGroq([COMPARE_JSON])
    use_fake_ai(monkeypatch, gemini=FakeGemini(error=RuntimeError("503 UNAVAILABLE")), groq=groq)
    r = client().post("/compare", json={"papers": [paper(1), paper(2)]})
    assert r.status_code == 200 and len(groq.calls) == 1
    schema = groq.calls[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["papers"]["items"]["additionalProperties"] is False


def test_new_routes_are_rate_limited():
    limits = main.RateLimiterMiddleware(main.app).limits
    assert limits["/takeaways"] == (20, 60) and limits["/compare"] == (20, 60)


def test_parse_ai_json_accepts_lists_but_not_empty():
    assert main.parse_ai_json('{"takeaways": [{"paper_id": 1, "takeaway": "x"}]}', "takeaways")
    for bad in ('{"takeaways": []}', '{"takeaways": ""}', '[]'):
        try:
            main.parse_ai_json(bad, "takeaways")
        except ValueError:
            continue
        raise AssertionError(bad)
