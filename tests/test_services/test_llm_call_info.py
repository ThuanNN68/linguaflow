"""Tests for reading provider accounting metadata off a response.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from src.services.shared.llm import extract_call_info


def test_reads_model_tokens_and_finish_reason():
    """The shape LangChain gives us for a normal Groq or OpenAI response."""
    response = AIMessage(
        content="Bonjour",
        response_metadata={
            "model_name": "llama-3.3-70b-versatile",
            "finish_reason": "stop",
        },
        usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
        id="chatcmpl-abc123",
    )

    info = extract_call_info(response)

    assert info.model_served == "llama-3.3-70b-versatile"
    assert info.input_tokens == 120
    assert info.output_tokens == 8
    assert info.finish_reason == "stop"
    assert info.request_id == "chatcmpl-abc123"


def test_reads_the_model_key_when_the_provider_uses_it():
    """Not every provider reports the model under the same key."""
    response = AIMessage(content="Hola", response_metadata={"model": "gpt-4o-mini"})

    assert extract_call_info(response).model_served == "gpt-4o-mini"


def test_finish_reason_length_is_preserved():
    """A truncated response is why some translations look like rambling.

    Without this the case is indistinguishable from a model that simply
    produced too much text, and LLM_MAX_TOKENS never comes up as the cause.
    """
    response = AIMessage(content="Der Bericht", response_metadata={"finish_reason": "length"})

    assert extract_call_info(response).finish_reason == "length"


def test_a_response_without_metadata_yields_empty_values():
    """Providers vary in what they report; a missing field is not an error."""
    info = extract_call_info(AIMessage(content="Ciao"))

    assert info.model_served == ""
    assert info.input_tokens == 0
    assert info.finish_reason == ""


def test_a_bare_test_double_does_not_raise():
    """The agent tests mock responses as an object with only `content`.

    Telemetry runs on the same path as the translation, so anything it cannot
    read has to come back empty rather than break the call (NFR-02).
    """
    double = type("Msg", (), {"content": "Xin chào"})()

    assert extract_call_info(double) == extract_call_info(None)


def test_unusable_metadata_is_swallowed():
    """A provider returning something unexpected must not fail a translation."""
    broken = type("Msg", (), {"content": "x", "usage_metadata": "not a mapping"})()

    assert extract_call_info(broken).input_tokens == 0
