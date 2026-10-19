"""Tests for what is actually SENT to the AI providers.

The other AI tests replace the whole client with a fake, so they cannot notice when the real SDK builds a
request the provider rejects. These tests use the real google-genai client, intercept the HTTP request body it
builds (nothing is sent over the network) and check it, which is exactly the class of bug that returned
"400 INVALID_ARGUMENT ... Unknown name additional_properties" from Gemini in production.
"""
import copy
import json
import types as pytypes

import pytest
from google import genai

import main
from test_security import GOOD_JSON, FakeGemini, FakeGroq


class Captured(Exception):
    pass


def real_gemini_client_capturing(captured: dict):
    """A real google-genai client whose HTTP layer records the request body instead of sending it."""
    client = genai.Client(api_key="not-a-real-key")

    def fake_request(http_method, path, request_dict, http_options=None):
        captured["body"] = request_dict
        raise Captured("request intercepted")

    client._api_client.request = fake_request
    return client


@pytest.mark.parametrize("schema_name", ["ASK_SCHEMA", "DEEP_DIVE_SCHEMA"])
def test_gemini_request_has_no_additional_properties(schema_name):
    captured = {}
    client = real_gemini_client_capturing(captured)
    schema = getattr(main, schema_name)
    key = "answer" if schema_name == "ASK_SCHEMA" else "deep_dive"
    with pytest.raises(Captured):  # generate_structured_ai retries once, then re-raises the last error
        main.generate_structured_ai(client, None, "system", "user", "retry", schema, key)
    sent = json.dumps(captured["body"])
    assert "responseSchema" in sent  # we really did inspect the structured-output part of the request
    assert "additional_properties" not in sent
    assert "additionalProperties" not in sent


def test_shared_schemas_are_not_mutated_and_groq_keeps_strict_mode():
    before = copy.deepcopy(main.ASK_SCHEMA)
    assert main.gemini_safe_schema(main.ASK_SCHEMA) != main.ASK_SCHEMA
    assert main.ASK_SCHEMA == before  # the copy for Gemini must not change the shared schema
    groq = FakeGroq([GOOD_JSON])
    main.generate_structured_ai(None, groq, "system", "user", "retry", main.ASK_SCHEMA, "answer")
    sent_schema = groq.calls[0]["response_format"]["json_schema"]["schema"]
    assert sent_schema["additionalProperties"] is False  # Groq strict mode requires it
    assert sent_schema["properties"]["citations"]["items"]["additionalProperties"] is False


def test_falls_back_to_groq_when_gemini_fails():
    gemini = FakeGemini(error=RuntimeError("400 INVALID_ARGUMENT"))
    groq = FakeGroq([GOOD_JSON])
    result = main.generate_structured_ai(gemini, groq, "system", "user", "retry", main.ASK_SCHEMA, "answer")
    assert result["answer"].startswith("Sleep matters")
    assert len(gemini.calls) == 2  # Gemini gets its normal retry first
    assert len(groq.calls) == 1


def test_gemini_failure_without_groq_still_raises():
    gemini = FakeGemini(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        main.generate_structured_ai(gemini, None, "system", "user", "retry", main.ASK_SCHEMA, "answer")


def test_groq_is_not_called_when_gemini_succeeds():
    gemini = FakeGemini(text=GOOD_JSON)
    groq = FakeGroq([])
    main.generate_structured_ai(gemini, groq, "system", "user", "retry", main.ASK_SCHEMA, "answer")
    assert groq.calls == []
