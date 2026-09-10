"""Shared service utilities for Conversation Intelligence operations (B-09)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from src.agents.conversation_intelligence.observability import build_runnable_config
from src.agents.conversation_intelligence.parsing import invoke_with_repair
from src.config import Settings, get_settings
from src.schemas.intelligence import ActionProposalResponse
from src.services.assistant.agent_consent import require_consent
from src.services.shared.llm import get_llm

T = TypeVar("T", bound=BaseModel)


class ConversationIntelligenceService:
    """Base service for invoking intelligence operations safely with timeouts and structured output."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def get_model(self, provider: str | None = None) -> Any:
        """Get configured ChatModel instance."""
        return get_llm(settings=self.settings, provider=provider)

    async def invoke_structured(
        self,
        messages: list[BaseMessage],
        schema: type[T],
        operation: str = "intelligence_operation",
        timeout: float | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        provider: str | None = None,
        **metadata: Any,
    ) -> T:
        """Safely invoke LLM for structured output with bounded timeout and repair retry."""
        llm = self.get_model(provider=provider)
        eff_timeout = timeout if timeout is not None else float(self.settings.llm_timeout_seconds)
        eff_provider = provider or self.settings.llm_provider
        eff_model = self.settings.llm_model

        runnable_config = build_runnable_config(
            operation=operation,
            conversation_id=conversation_id,
            message_id=message_id,
            **metadata,
        )

        return await invoke_with_repair(
            llm=llm,
            messages=messages,
            schema=schema,
            operation=operation,
            timeout=eff_timeout,
            provider=eff_provider,
            model=eff_model,
            conversation_id=conversation_id,
            message_id=message_id,
            runnable_config=runnable_config,
        )

    async def summarize_conversation(
        self,
        conversation_id: str,
        user_id: str,
        db: Any,
        message_limit: int = 200,
        target_language: str | None = None,
    ) -> Any:
        """Fetch authorized messages and generate grounded conversation summary (B-03)."""
        from sqlalchemy import select

        from src.agents.conversation_intelligence.summary import (
            format_transcript_line,
            generate_long_conversation_summary,
        )
        from src.database.models import User
        from src.schemas.intelligence import ConversationSummaryResponse
        from src.services.messaging.chat import ChatService

        # Before membership, not after: this asks about the caller's own account
        # and involves no conversation data, so checking it first costs a single
        # indexed lookup and avoids reading messages the user never agreed to
        # let a model see.
        await require_consent(db, user_id, "read_conversations")

        chat_service = ChatService(db)
        # 1. Membership & existence authorization + message history retrieval
        messages = await chat_service.get_message_history(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=message_limit,
        )

        # 2. Determine target language
        user = await db.get(User, user_id)
        eff_target_lang = target_language or (user.preferred_language if user else "vi")

        # 3. Filter out deleted messages (soft-deleted rows keep row but have deleted_at set / blank text)
        usable_messages = [m for m in messages if m.deleted_at is None and m.original_text and m.original_text.strip()]

        if not usable_messages:
            return ConversationSummaryResponse(
                summary="",
                key_points=[],
                decisions=[],
                open_items=[],
                message_count=0,
                target_language=eff_target_lang,
                window_start_at=None,
                window_end_at=None,
            )

        # 4. Resolve sender display names
        sender_ids = list({m.sender_id for m in usable_messages})
        users_res = await db.execute(select(User).where(User.id.in_(sender_ids)))
        user_map = {u.id: (u.display_name or u.username or u.email.split("@")[0]) for u in users_res.scalars().all()}

        # 5. Format transcript (chronological order)
        transcript_lines = [
            format_transcript_line(
                created_at_str=m.created_at.isoformat() if m.created_at else "",
                speaker_name=user_map.get(m.sender_id, "User"),
                text=m.original_text,
            )
            for m in usable_messages
        ]
        # 6. Generate summary
        #
        # Lines rather than a joined transcript: above LONG_CONVERSATION_THRESHOLD
        # this splits into batches, and the batch boundaries have to fall between
        # messages. Cutting a joined string by length would hand one batch a
        # fragment whose speaker and timestamp are in the previous batch. Below
        # the threshold it joins them again and takes the single-pass path, so
        # there is one entry point rather than a choice the caller has to make.
        return await generate_long_conversation_summary(
            transcript_lines=transcript_lines,
            target_language=eff_target_lang,
            message_count=len(usable_messages),
            window_start_at=usable_messages[0].created_at,
            window_end_at=usable_messages[-1].created_at,
            conversation_id=conversation_id,
            settings=self.settings,
        )

    async def extract_actions_from_message(
        self,
        conversation_id: str,
        message_id: str,
        user_id: str,
        db: Any,
    ) -> list[Any]:
        """Extract candidate actions from a specific message and persist as ActionProposals (B-04)."""
        from sqlalchemy import select

        from src.agents.conversation_intelligence.action_graph import extract_action_candidates
        from src.database.models import Conversation, ConversationMember, Message, User
        from src.schemas.intelligence import ActionProposalResponse
        from src.services.intelligence.action_proposals import ActionProposalService
        from src.services.messaging.chat import (
            ConversationMembershipError,
            ConversationNotFoundError,
            MessageNotFoundError,
        )

        await require_consent(db, user_id, "read_conversations")

        # 1. Verify conversation existence and caller membership
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            raise ConversationNotFoundError(conversation_id)

        member_stmt = select(ConversationMember).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id,
        )
        if (await db.execute(member_stmt)).scalar_one_or_none() is None:
            raise ConversationMembershipError(conversation_id, user_id)

        # 2. Fetch message
        msg = await db.get(Message, message_id)
        if msg is None or msg.conversation_id != conversation_id:
            raise MessageNotFoundError(message_id)

        if msg.visibility == "private" and msg.visible_to_user_id != user_id:
            raise MessageNotFoundError(message_id)

        # If message was withdrawn/deleted or has blank text, return empty
        if msg.deleted_at is not None or not msg.original_text or not msg.original_text.strip():
            return []

        # 3. Retrieve conversation members for attribution context
        members_stmt = (
            select(User)
            .join(ConversationMember, ConversationMember.user_id == User.id)
            .where(ConversationMember.conversation_id == conversation_id)
        )
        members_res = await db.execute(members_stmt)
        members_list = [
            {
                "id": u.id,
                "name": u.display_name or u.username or u.email.split("@")[0],
                "username": u.username or "",
            }
            for u in members_res.scalars().all()
        ]

        sender_user = await db.get(User, msg.sender_id)
        sender_name = (
            sender_user.display_name or sender_user.username or sender_user.email.split("@")[0]
            if sender_user
            else "User"
        )

        # 4. Extract action candidates via LLM
        candidates = await extract_action_candidates(
            message_text=msg.original_text,
            sender_id=msg.sender_id,
            sender_name=sender_name,
            members=members_list,
            reference_timestamp=msg.created_at,
            conversation_id=conversation_id,
            message_id=message_id,
            settings=self.settings,
        )

        if not candidates:
            return []

        requester = next((member for member in members_list if member["id"] == user_id), None)
        if requester is None:
            return []
        eligible = [
            candidate
            for candidate in candidates
            if self._candidate_belongs_to_requester(
                message_text=msg.original_text,
                sender_id=msg.sender_id,
                requester_id=user_id,
                requester=requester,
                candidate=candidate,
            )
        ]
        if not eligible:
            return []

        # 5. Persist proposals idempotently
        prop_service = ActionProposalService(db)
        proposals = await prop_service.create_proposals_from_candidates(
            conversation_id=conversation_id,
            source_message_id=message_id,
            candidates=eligible,
            owner_user_id=user_id,
            source_mode="on_demand",
            created_by_user_id=user_id,
        )

        return [ActionProposalResponse.model_validate(p) for p in proposals]

    async def extract_actions_from_recent_messages(
        self,
        conversation_id: str,
        user_id: str,
        days: int,
        db: Any,
        hours: int | None = None,
    ) -> list[Any]:
        """Scan the caller-visible, human messages in one conversation.

        This is deliberately an explicit on-demand operation: it never scans a
        conversation merely because it is opened.  Reusing the single-message
        extraction path preserves ownership checks and idempotency for every
        proposal, while the date filter gives the caller a clear privacy scope.
        ``hours`` is used for a short window and is limited at the API boundary.
        """
        from sqlalchemy import or_, select

        from src.database.models import Message
        from src.services.messaging.chat import ConversationMembershipError, ConversationNotFoundError

        await require_consent(db, user_id, "read_conversations")

        # Validate scope once before running any LLM work.  The per-message
        # method repeats this defensively, so no later refactor can bypass it.
        from src.database.models import Conversation, ConversationMember

        if await db.get(Conversation, conversation_id) is None:
            raise ConversationNotFoundError(conversation_id)
        member = await db.scalar(
            select(ConversationMember.user_id).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id,
            )
        )
        if member is None:
            raise ConversationMembershipError(conversation_id, user_id)

        since = datetime.now(UTC) - timedelta(hours=hours if hours is not None else days * 24)
        newest_messages = (
            select(Message.id, Message.created_at)
            .where(
                Message.conversation_id == conversation_id,
                Message.created_at >= since,
                Message.deleted_at.is_(None),
                Message.assistant_generated.is_(False),
                Message.original_text != "",
                or_(Message.visibility == "public", Message.visible_to_user_id == user_id),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(100)
            .subquery()
        )
        # Select the newest bounded window above, then restore chronological
        # processing. Reversing the result in Python made the contract depend
        # on the database/mock return order and produced message-2 before
        # message-1 in the reliability test.
        statement = select(newest_messages.c.id).order_by(
            newest_messages.c.created_at.asc(),
            newest_messages.c.id.asc(),
        )
        message_ids = list((await db.scalars(statement)).all())
        proposals: list[Any] = []
        for message_id in message_ids:
            proposals.extend(
                await self.extract_actions_from_message(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_id=user_id,
                    db=db,
                )
            )
        return proposals

    @staticmethod
    def _candidate_belongs_to_requester(
        *,
        message_text: str,
        sender_id: str,
        requester_id: str,
        requester: dict[str, str],
        candidate: Any,
    ) -> bool:
        """Conservative server-side B-04 eligibility, independent of model IDs."""
        import re

        text = message_text.casefold()
        sender_self_commitment = bool(
            re.search(r"\b(?:i(?:'ll| will| am going to)|i commit to)\b", text)
            or re.search(r"(?:tôi|mình|em|anh)\s+sẽ", text)
        )
        if sender_id != requester_id and sender_self_commitment:
            return False

        aliases = {requester.get("username", "").casefold(), requester.get("name", "").casefold()}
        aliases.update(alias.split()[0] for alias in list(aliases) if alias)
        requester_named = any(alias and re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)
        assignment = bool(re.search(r"\b(?:please|can you|could you|need you to|nhờ)\b", text))
        relationship = getattr(candidate, "relationship", "other_or_unknown")
        if sender_id == requester_id:
            return sender_self_commitment or relationship in {
                "requester_self_commitment",
                "requester_appointment",
            }
        if requester_named and assignment:
            return True
        return bool(
            candidate.action_type == "appointment" and requester_named and relationship == "requester_appointment"
        )

    async def detect_self_commitments_from_message(
        self,
        conversation_id: str,
        message_id: str,
        user_id: str,
        db: Any,
    ) -> list[ActionProposalResponse]:
        """Detect proactive self-commitments from message and create ActionProposals (B-10)."""
        from sqlalchemy import select

        from src.agents.conversation_intelligence.self_commitment import detect_self_commitments
        from src.database.models import Conversation, ConversationMember, Message, User
        from src.services.intelligence.action_proposals import ActionProposalService
        from src.services.messaging.chat import (
            ConversationMembershipError,
            ConversationNotFoundError,
            MessageNotFoundError,
        )

        # Only `read_conversations` here. This method serves both the manual
        # endpoint and the background scanner, and a request the user typed is
        # not proactive — the extra `proactive_scan` check belongs to the
        # scanner, in `commitment_detection.py`.
        await require_consent(db, user_id, "read_conversations")

        # 1. Verify conversation and caller membership
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            raise ConversationNotFoundError(conversation_id)

        member_stmt = select(ConversationMember).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id,
        )
        if (await db.execute(member_stmt)).scalar_one_or_none() is None:
            raise ConversationMembershipError(conversation_id, user_id)

        # 2. Fetch message
        msg = await db.get(Message, message_id)
        if msg is None or msg.conversation_id != conversation_id:
            raise MessageNotFoundError(message_id)
        if msg.sender_id != user_id:
            raise ConversationMembershipError(conversation_id, user_id)

        if msg.deleted_at is not None or not msg.original_text or not msg.original_text.strip():
            return []

        # 3. Resolve sender display name
        sender_user = await db.get(User, msg.sender_id)
        sender_name = (
            sender_user.display_name or sender_user.username or sender_user.email.split("@")[0]
            if sender_user
            else "User"
        )

        # 4. Detect self-commitments via LLM
        candidates = await detect_self_commitments(
            message_text=msg.original_text,
            sender_id=msg.sender_id,
            sender_name=sender_name,
            reference_timestamp=msg.created_at,
            conversation_id=conversation_id,
            message_id=message_id,
            settings=self.settings,
        )

        if not candidates:
            return []

        # 5. Persist proposals idempotently
        prop_service = ActionProposalService(db)
        proposals = await prop_service.create_proposals_from_candidates(
            conversation_id=conversation_id,
            source_message_id=message_id,
            candidates=candidates,
            owner_user_id=msg.sender_id,
            source_mode="proactive",
            created_by_user_id=None,
        )

        return [ActionProposalResponse.model_validate(p) for p in proposals]
