"""Server-side speech synthesis used when browser-native TTS is unavailable."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from src.config import Settings, get_settings


class TTSConfigurationError(RuntimeError):
    """Speech synthesis is disabled or missing required credentials."""


class TTSSynthesisError(RuntimeError):
    """The configured speech provider could not synthesize audio."""


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    data: bytes
    content_type: str = "audio/mpeg"


class TTSService:
    """Small provider boundary for authenticated message speech."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._transport = transport

    async def synthesize(self, text: str, language: str) -> SynthesizedSpeech:
        if self._settings.tts_provider == "disabled":
            raise TTSConfigurationError("Server-side text-to-speech is disabled")
        provider = self._settings.tts_provider
        if provider == "openai":
            return await self._synthesize_openai(text)
        if provider == "elevenlabs":
            return await self._synthesize_elevenlabs(text)
        if provider == "google":
            return await self._synthesize_google(text, language)
        raise TTSConfigurationError("Unsupported server-side text-to-speech provider")

    async def _synthesize_openai(self, text: str) -> SynthesizedSpeech:
        if not self._settings.openai_api_key:
            raise TTSConfigurationError("OPENAI_API_KEY is required for OpenAI text-to-speech")
        try:
            async with httpx.AsyncClient(
                base_url="https://api.openai.com/v1",
                timeout=self._settings.tts_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    "/audio/speech",
                    headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                    json={
                        "model": self._settings.tts_model,
                        "voice": self._settings.tts_voice,
                        "input": text,
                        "response_format": "mp3",
                    },
                )
            response.raise_for_status()
        except httpx.HTTPError:
            raise TTSSynthesisError("Unable to synthesize speech") from None
        if not response.content:
            raise TTSSynthesisError("Speech provider returned empty audio")
        return SynthesizedSpeech(data=response.content)

    async def _synthesize_elevenlabs(self, text: str) -> SynthesizedSpeech:
        if not self._settings.elevenlabs_api_key:
            raise TTSConfigurationError("ELEVENLABS_API_KEY is required for ElevenLabs text-to-speech")
        try:
            async with httpx.AsyncClient(
                base_url=self._settings.elevenlabs_api_base.rstrip("/"),
                timeout=self._settings.tts_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"/text-to-speech/{self._settings.tts_voice}",
                    params={"output_format": "mp3_44100_128"},
                    headers={"xi-api-key": self._settings.elevenlabs_api_key, "Accept": "audio/mpeg"},
                    json={"text": text, "model_id": self._settings.tts_model},
                )
            response.raise_for_status()
        except httpx.HTTPError:
            raise TTSSynthesisError("Unable to synthesize speech") from None
        if not response.content:
            raise TTSSynthesisError("Speech provider returned empty audio")
        return SynthesizedSpeech(data=response.content)

    async def _synthesize_google(self, text: str, language: str) -> SynthesizedSpeech:
        if not self._settings.google_tts_api_key:
            raise TTSConfigurationError("GOOGLE_TTS_API_KEY is required for Google text-to-speech")
        voice: dict[str, str] = {"languageCode": language}
        if self._settings.tts_voice:
            voice["name"] = self._settings.tts_voice
        try:
            async with httpx.AsyncClient(
                base_url="https://texttospeech.googleapis.com/v1",
                timeout=self._settings.tts_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    "/text:synthesize",
                    params={"key": self._settings.google_tts_api_key},
                    json={
                        "input": {"text": text},
                        "voice": voice,
                        "audioConfig": {"audioEncoding": "MP3"},
                    },
                )
            response.raise_for_status()
            encoded = response.json().get("audioContent", "")
            audio = base64.b64decode(encoded, validate=True)
        except (httpx.HTTPError, ValueError, TypeError):
            raise TTSSynthesisError("Unable to synthesize speech") from None
        if not audio:
            raise TTSSynthesisError("Speech provider returned empty audio")
        return SynthesizedSpeech(data=audio)
