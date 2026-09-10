"""The admin test bench must execute server agent output, never browser mocks."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_member_cannot_run_admin_translation(client, test_user_headers):
    response = await client.post(
        "/api/v1/admin/translate",
        headers=test_user_headers,
        json={
            "original_text": "Deploy the service",
            "source_language": "en",
            "target_language": "vi",
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_translation_returns_real_agent_state(
    client, test_admin_headers, monkeypatch
):
    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "translated_text": "Triển khai dịch vụ",
        "source_language": "en",
        "target_language": "vi",
        "model": "served-model",
        "latency_ms": 321,
        "is_fallback": False,
        "glossary_terms": [],
        "telemetry": {
            "input_tokens": 17,
            "output_tokens": 8,
            "model_served": "served-model",
            "outcome": "llm",
        },
    }
    monkeypatch.setattr(
        "src.api.admin.build_translation_graph",
        lambda **_kwargs: graph,
    )

    response = await client.post(
        "/api/v1/admin/translate",
        headers=test_admin_headers,
        json={
            "original_text": "Deploy the service",
            "source_language": "EN",
            "target_language": "VI",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["translated_text"] == "Triển khai dịch vụ"
    assert body["model"] == "served-model"
    assert body["input_tokens"] == 17
    assert body["output_tokens"] == 8
    assert body["is_fallback"] is False
    state = graph.ainvoke.await_args.args[0]
    assert state["original_text"] == "Deploy the service"
    assert state["source_language"] == "en"
    assert state["target_language"] == "vi"


@pytest.mark.asyncio
async def test_admin_translation_rejects_unsupported_language(
    client, test_admin_headers
):
    response = await client.post(
        "/api/v1/admin/translate",
        headers=test_admin_headers,
        json={
            "original_text": "Hello",
            "source_language": "xx",
            "target_language": "vi",
        },
    )

    assert response.status_code == 422
