"""Tests for the "At a glance" fields on /deep-dive and deep-dive caching."""
import json

from fastapi.testclient import TestClient

import main
from test_security import FakeGemini, FakeGroq, clean_state, use_fake_ai  # noqa: F401 (autouse fixture)


def client():
    return TestClient(main.app)


GOOD = json.dumps({
    "deep_dive": "## Summary\nok",
    "glance": {"study_type": " Randomised controlled trial ", "population": "Adults over 60",
               "sample_size": "200", "main_finding": "Sleep onset was 15 minutes faster."},
})


def body(abstract="We randomised 200 adults over 60 to melatonin or placebo."):
    return {"title": "Melatonin in older adults", "abstract": abstract}


def test_deep_dive_returns_glance(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD))
    data = client().post("/deep-dive", json=body()).json()
    assert data["deep_dive"] == "## Summary\nok"
    assert data["glance"] == {"study_type": "Randomised controlled trial", "population": "Adults over 60",
                              "sample_size": "200", "main_finding": "Sleep onset was 15 minutes faster."}


def test_missing_or_empty_glance_fields_become_not_stated(monkeypatch):
    text = json.dumps({"deep_dive": "ok", "glance": {"study_type": "", "population": 5, "sample_size": "  "}})
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=text))
    glance = client().post("/deep-dive", json=body("Another abstract.")).json()["glance"]
    assert set(glance) == set(main.GLANCE_FIELDS) and all(v == "Not stated" for v in glance.values())


def test_reply_without_glance_still_works(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"deep_dive": "## Summary\\nold style"}'))
    data = client().post("/deep-dive", json=body("Old style reply abstract.")).json()
    assert data["deep_dive"].endswith("old style") and data["glance"]["main_finding"] == "Not stated"


def test_glance_values_are_trimmed_to_limit(monkeypatch):
    text = json.dumps({"deep_dive": "ok", "glance": {f: "x" * 900 for f in main.GLANCE_FIELDS}})
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=text))
    glance = client().post("/deep-dive", json=body("Long values abstract.")).json()["glance"]
    assert all(len(v) == main.GLANCE_MAX_CHARS for v in glance.values())


def test_deep_dive_is_cached(monkeypatch):
    gemini = FakeGemini(text=GOOD)
    use_fake_ai(monkeypatch, gemini=gemini)
    first = client().post("/deep-dive", json=body()).json()
    second = client().post("/deep-dive", json=body()).json()
    assert first == second and len(gemini.calls) == 1


def test_cached_deep_dive_uses_no_quota(monkeypatch):
    use_fake_ai(monkeypatch, gemini=FakeGemini(text=GOOD))
    monkeypatch.setattr(main, "AI_DAILY_LIMIT_GUEST", 1)
    assert client().post("/deep-dive", json=body()).status_code == 200
    assert client().post("/deep-dive", json=body()).status_code == 200  # cache hit, no quota used
    assert client().post("/deep-dive", json=body("A new abstract.")).status_code == 429


def test_no_abstract_has_no_glance_and_no_ai_call(monkeypatch):
    gemini = FakeGemini(text=GOOD)
    use_fake_ai(monkeypatch, gemini=gemini)
    data = client().post("/deep-dive", json=body("No abstract available.")).json()
    assert data["glance"] is None and gemini.calls == []


def test_glance_schema_works_for_both_providers(monkeypatch):
    assert "glance" in main.DEEP_DIVE_SCHEMA["required"]
    assert "additionalProperties" not in json.dumps(main.gemini_safe_schema(main.DEEP_DIVE_SCHEMA))
    groq = FakeGroq([GOOD])
    use_fake_ai(monkeypatch, gemini=None, groq=groq)
    assert client().post("/deep-dive", json=body("Groq abstract.")).json()["glance"]["sample_size"] == "200"
    schema = groq.calls[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["glance"]["additionalProperties"] is False
