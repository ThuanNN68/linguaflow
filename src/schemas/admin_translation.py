"""Wire contract for the administrator's live translation test bench."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.database.models import GLOSSARY_AUDIENCES, GLOSSARY_DOMAINS, TRANSLATION_TONES
from src.schemas.auth import normalize_language


class AdminTranslationRequest(BaseModel):
    """One synchronous translation run through the production agent graph."""

    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(min_length=1, max_length=10_000)
    source_language: str
    target_language: str
    domain: str = ""
    audience: str = ""
    translation_tone: str = "natural"

    @field_validator("original_text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Text must not be blank")
        return cleaned

    @field_validator("source_language", "target_language")
    @classmethod
    def language_must_be_supported(cls, value: str) -> str:
        return normalize_language(value)

    @field_validator("domain")
    @classmethod
    def domain_must_be_canonical(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned and cleaned not in GLOSSARY_DOMAINS:
            raise ValueError("Unsupported domain")
        return cleaned

    @field_validator("audience")
    @classmethod
    def audience_must_be_canonical(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned and cleaned not in GLOSSARY_AUDIENCES:
            raise ValueError("Unsupported audience")
        return cleaned

    @field_validator("translation_tone")
    @classmethod
    def tone_must_be_supported(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned not in TRANSLATION_TONES:
            raise ValueError("Unsupported translation tone")
        return cleaned


class AdminTranslationResponse(BaseModel):
    """Actual output and provider telemetry returned by the agent."""

    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    is_fallback: bool
    fallback_reason: str = ""
    matched_glossary_terms: list[str] = Field(default_factory=list)
