"""Tests for the optional server-side TTS provider boundary."""

import base64

import httpx
import pytest

from src.config import Settings
from src.services.voice.tts import TTSConfigurationError, TTSService


def _settings(**overrides) -> Settings:
    values = {"jwt_secret": "x" * 48, "tts_provider": "openai", "openai_api_key": "test-key"}
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_openai_tts_returns_mp3_without_leaking_credentials():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"mp3-audio")

    speech = await TTSService(
        _settings(),
        transport=httpx.MockTransport(handler),
    ).synthesize("Xin chao", "vi")

    assert speech.data == b"mp3-audio"
    assert speech.content_type == "audio/mpeg"
    assert requests[0].url.path == "/v1/audio/speech"
    assert requests[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_disabled_server_tts_fails_without_calling_a_provider():
    with pytest.raises(TTSConfigurationError, match="disabled"):
        await TTSService(_settings(tts_provider="disabled")).synthesize("Hello", "en")


@pytest.mark.asyncio
async def test_elevenlabs_tts_uses_configured_voice_and_key():
    requests: list[httpx.Request] = []
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"eleven-audio")
    settings = _settings(tts_provider="elevenlabs", elevenlabs_api_key="eleven-key", tts_voice="voice-1", tts_model="eleven_multilingual_v2")
    speech = await TTSService(settings, transport=httpx.MockTransport(handler)).synthesize("Xin chào", "vi")
    assert speech.data == b"eleven-audio"
    assert requests[0].url.path == "/v1/text-to-speech/voice-1"
    assert requests[0].headers["xi-api-key"] == "eleven-key"


@pytest.mark.asyncio
async def test_google_tts_decodes_audio_and_passes_language():
    requests: list[httpx.Request] = []
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"google-audio").decode()})
    settings = _settings(tts_provider="google", google_tts_api_key="google-key", tts_voice="vi-VN-Neural2-A")
    speech = await TTSService(settings, transport=httpx.MockTransport(handler)).synthesize("Xin chào", "vi-VN")
    assert speech.data == b"google-audio"
    assert requests[0].url.path == "/v1/text:synthesize"
    assert requests[0].url.params["key"] == "google-key"
    assert b'"languageCode":"vi-VN"' in await requests[0].aread()
