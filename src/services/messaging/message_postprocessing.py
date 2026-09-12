"""Schedule the existing text-dependent work for one canonical message.

This is orchestration only. Translation, context, glossary, customization,
commitment detection, profile inference, and embeddings keep their existing
implementations and persistence contracts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.database.models import Message
from src.services.assistant.assistant_indexing import schedule_chunk_index
from src.services.intelligence.commitment_detection import schedule_commitment_detection
from src.services.intelligence.profile_inference import schedule_profile_inference
from src.services.language.translation import schedule_translations
from src.services.messaging.message_memory import schedule_message_embedding


def schedule_text_dependent_work(
    *,
    message: Message,
    publisher: Any,
    translate: bool = True,
    translation_scheduler: Callable[..., None] | None = None,
    commitment_scheduler: Callable[..., None] | None = None,
    profile_scheduler: Callable[..., None] | None = None,
    embedding_scheduler: Callable[..., None] | None = None,
    chunk_scheduler: Callable[..., None] | None = None,
) -> None:
    """Hand a text-ready message to the same detached services exactly once.

    Callers own the exactly-once decision. The normal text caller uses its
    durable ``created`` flag; voice uses ownership of the guarded
    pending-to-completed transition. The guards here are defense in depth so a
    pending or failed voice row can never snapshot empty text even if a future
    caller invokes this helper incorrectly.

    Structured assistant mentions deliberately remain outside this helper.
    They require explicit persisted mention metadata and synchronous reply
    creation in the WebSocket flow; speech is not parsed into mentions.
    """
    text = message.original_text
    if not isinstance(text, str) or not text.strip():
        return
    if message.message_type == "voice" and message.transcription_status != "completed":
        return

    if translate:
        (translation_scheduler or schedule_translations)(
            message=message,
            publisher=publisher,
        )
    (commitment_scheduler or schedule_commitment_detection)(
        message_id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        publisher=publisher,
    )
    (profile_scheduler or schedule_profile_inference)(
        conversation_id=message.conversation_id,
    )
    (embedding_scheduler or schedule_message_embedding)(
        message_id=message.id,
        conversation_id=message.conversation_id,
        text=text,
        # Whose `store_memory` consent decides whether this is remembered. Not
        # optional: without a sender the scheduler falls back to the RAG flag
        # alone, so an account that granted memory would be embedded never and
        # nothing would say so (ADR-30).
        sender_id=message.sender_id,
    )
    # The Assistant Agent reads `assistant_chunks`, which is a different index
    # from `message_embeddings` above and not derivable from it: a chunk may
    # gather six messages or split one (ADR-37). Without this the assistant's
    # semantic recall is empty for every conversation nobody has backfilled —
    # which looks exactly like a conversation in which nothing was said.
    #
    # Self-gating on cadence: the scheduler indexes the first message and then
    # every REINDEX_EVERY, so this call is cheap on all but one message in
    # twenty.
    (chunk_scheduler or schedule_chunk_index)(
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
    )
