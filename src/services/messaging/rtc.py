"""RTC provider adapter and direct-call state machine.

Daily is intentionally isolated here: routes only authorize, transition durable
call state and publish safe notifications.  Provider API keys and meeting tokens
are never persisted or broadcast.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database.models import CallSession, Conversation, ConversationMember
from src.services.messaging.blocking import DirectMessagingBlockedError, is_blocked_between
from src.services.messaging.chat import ConversationMembershipError, ConversationNotFoundError

TERMINAL_CALL_STATUSES = frozenset({"rejected", "ended", "missed", "failed"})


class RTCProviderUnavailableError(Exception):
    """No configured provider can safely issue a room or participant token."""


class CallStateError(Exception):
    """A caller attempted an invalid state transition."""


class CallNotFoundError(Exception):
    """No authorized call exists for the requested identifier."""


class RTCProvider(Protocol):
    name: str

    async def create_room(self, room_name: str) -> str: ...

    async def create_join_token(self, room_name: str, user_id: str, *, owner: bool) -> str: ...

    async def close_room(self, room_name: str) -> None: ...

    def room_url(self, room_name: str) -> str: ...


class DailyProvider:
    name = "daily"

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.daily_api_key.strip()
        self._api_base = settings.daily_api_base.rstrip("/")
        self._ttl_seconds = settings.call_token_ttl_seconds

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RTCProviderUnavailableError("RTC provider is not configured")
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def create_room(self, room_name: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self._api_base}/rooms",
                    headers=self._headers(),
                    json={
                        "name": room_name,
                        "privacy": "private",
                        # If neither participant explicitly ends the call,
                        # Daily still tears down the unused room.
                        "properties": {"exp": int(time.time()) + self._ttl_seconds},
                    },
                )
                response.raise_for_status()
                room_url = str(response.json().get("url") or "")
        except RTCProviderUnavailableError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise RTCProviderUnavailableError("RTC provider could not create a room") from exc
        if not room_url:
            raise RTCProviderUnavailableError("RTC provider returned no room URL")
        return room_url

    async def create_join_token(self, room_name: str, user_id: str, *, owner: bool) -> str:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self._api_base}/meeting-tokens",
                    headers=self._headers(),
                    json={
                        "properties": {
                            "room_name": room_name,
                            "user_name": user_id,
                            "is_owner": owner,
                            "exp": int(time.time()) + self._ttl_seconds,
                        }
                    },
                )
                response.raise_for_status()
                token = str(response.json().get("token") or "")
        except RTCProviderUnavailableError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise RTCProviderUnavailableError("RTC provider could not issue a join token") from exc
        if not token:
            raise RTCProviderUnavailableError("RTC provider returned no join token")
        return token

    async def close_room(self, room_name: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.delete(f"{self._api_base}/rooms/{room_name}", headers=self._headers())
                # Deleting a room that already expired is equivalently closed.
                if response.status_code != 404:
                    response.raise_for_status()
        except RTCProviderUnavailableError:
            raise
        except httpx.HTTPError as exc:
            raise RTCProviderUnavailableError("RTC provider could not close room") from exc

    def room_url(self, room_name: str) -> str:
        # Daily's default room URL. Custom domains remain supported for calls
        # started in this process because their URL is returned by create_room;
        # reconnecting deployments should set a custom adapter if needed.
        return f"https://{room_name}.daily.co"


def get_rtc_provider(settings: Settings | None = None) -> RTCProvider:
    resolved = settings or get_settings()
    if resolved.rtc_provider != "daily":
        raise RTCProviderUnavailableError("RTC calling is disabled")
    return DailyProvider(resolved)


@dataclass(frozen=True, slots=True)
class CallJoin:
    session: CallSession
    room_url: str
    join_token: str | None


class CallService:
    """Authorization and state transitions for one-to-one call sessions."""

    def __init__(
        self,
        db: AsyncSession,
        provider: RTCProvider | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._provider = provider
        self._settings = settings or get_settings()
        self._ring_timeout_seconds = self._settings.call_ring_timeout_seconds

    async def _expire_stale_ringing_calls(self, *user_ids: str) -> None:
        """Lazily transition stale ringing calls to missed status."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ring_timeout_seconds)
        stmt = select(CallSession).where(
            CallSession.status == "ringing",
            CallSession.created_at < cutoff,
        )
        if user_ids:
            stmt = stmt.where(
                or_(
                    CallSession.caller_id.in_(user_ids),
                    CallSession.callee_id.in_(user_ids),
                )
            )
        stale_calls = list((await self._db.scalars(stmt)).all())
        for stale in stale_calls:
            stale.status = "missed"
            stale.ended_at = stale.created_at + timedelta(seconds=self._ring_timeout_seconds)
            if self._provider is not None:
                try:
                    await self._provider.close_room(stale.provider_room_name)
                except RTCProviderUnavailableError:
                    pass
        if stale_calls:
            await self._db.commit()

    async def start_call(self, *, caller_id: str, conversation_id: str, call_type: str) -> CallJoin:
        if self._provider is None:
            raise RTCProviderUnavailableError("RTC calling is disabled")
        conversation, members = await self._require_direct_members(conversation_id, caller_id)
        callee_id = next(member_id for member_id in members if member_id != caller_id)
        if await is_blocked_between(self._db, caller_id, callee_id):
            raise DirectMessagingBlockedError("Direct messaging is unavailable")

        # Expire any stale ringing calls for participants before checking busy state
        await self._expire_stale_ringing_calls(caller_id, callee_id)

        # 1-to-1 policy: a user must not participate in more than one active/ringing call across all conversations
        caller_busy = await self._db.scalar(
            select(CallSession.id).where(
                or_(CallSession.caller_id == caller_id, CallSession.callee_id == caller_id),
                CallSession.status.in_(("ringing", "accepted")),
            ).limit(1)
        )
        if caller_busy is not None:
            raise CallStateError("You are already in an active call")

        callee_busy = await self._db.scalar(
            select(CallSession.id).where(
                or_(CallSession.caller_id == callee_id, CallSession.callee_id == callee_id),
                CallSession.status.in_(("ringing", "accepted")),
            ).limit(1)
        )
        if callee_busy is not None:
            raise CallStateError("The other user is currently in another call")

        room_name = f"linguaflow-{uuid.uuid4().hex}"
        room_url = await self._provider.create_room(room_name)
        try:
            call = CallSession(
                conversation_id=conversation.id,
                caller_id=caller_id,
                callee_id=callee_id,
                call_type=call_type,
                status="ringing",
                provider=self._provider.name,
                provider_room_name=room_name,
                provider_room_url=room_url,
            )
            self._db.add(call)
            await self._db.commit()
            await self._db.refresh(call)
        except Exception:
            await self._db.rollback()
            try:
                await self._provider.close_room(room_name)
            except RTCProviderUnavailableError:
                pass
            raise
        # The caller waits on the ringing screen. A short-lived join token is
        # minted for each participant only after the callee accepts.
        return CallJoin(session=call, room_url=room_url, join_token=None)

    async def accept_call(self, *, call_id: str, user_id: str) -> CallJoin:
        call = await self._get_participant_call(call_id, user_id)
        if call.callee_id != user_id or call.status != "ringing":
            raise CallStateError("Only the callee can accept a ringing call")

        # Expire if ringing timeout has elapsed
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ring_timeout_seconds)
        if call.created_at < cutoff:
            call.status = "missed"
            call.ended_at = call.created_at + timedelta(seconds=self._ring_timeout_seconds)
            await self._db.commit()
            if self._provider is not None:
                try:
                    await self._provider.close_room(call.provider_room_name)
                except RTCProviderUnavailableError:
                    pass
            raise CallStateError("Call has expired")

        if self._provider is None:
            raise RTCProviderUnavailableError("RTC calling is disabled")

        call.status = "accepted"
        call.answered_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(call)
        try:
            token = await self._provider.create_join_token(call.provider_room_name, user_id, owner=False)
        except RTCProviderUnavailableError:
            call.status = "failed"
            call.ended_at = datetime.now(UTC)
            await self._db.commit()
            try:
                await self._provider.close_room(call.provider_room_name)
            except RTCProviderUnavailableError:
                pass
            raise
        return CallJoin(session=call, room_url=call.provider_room_url, join_token=token)

    async def join_call(self, *, call_id: str, user_id: str) -> CallJoin:
        """Issue a participant-specific Daily token for an accepted call."""
        if self._provider is None:
            raise RTCProviderUnavailableError("RTC calling is disabled")
        call = await self._get_participant_call(call_id, user_id)
        if call.status != "accepted":
            raise CallStateError("The call has not been accepted")
        token = await self._provider.create_join_token(
            call.provider_room_name,
            user_id,
            owner=user_id == call.caller_id,
        )
        return CallJoin(session=call, room_url=call.provider_room_url, join_token=token)

    async def reject_call(self, *, call_id: str, user_id: str) -> CallSession:
        call = await self._get_participant_call(call_id, user_id)
        if call.callee_id != user_id or call.status != "ringing":
            raise CallStateError("Only the callee can reject a ringing call")
        call.status = "rejected"
        call.ended_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(call)
        if self._provider is not None:
            try:
                await self._provider.close_room(call.provider_room_name)
            except RTCProviderUnavailableError:
                # The durable application state is authoritative; Daily rooms also
                # expire from their provider-side TTL.
                pass
        return call

    async def end_call(self, *, call_id: str, user_id: str) -> CallSession:
        call = await self._get_participant_call(call_id, user_id)
        if call.status not in TERMINAL_CALL_STATUSES:
            call.status = "ended"
            call.ended_at = datetime.now(UTC)
            await self._db.commit()
            await self._db.refresh(call)
            if self._provider is not None:
                try:
                    await self._provider.close_room(call.provider_room_name)
                except RTCProviderUnavailableError:
                    # State is authoritative and already persisted. Room expiration
                    # is safer than rolling the state back because a call ended.
                    pass
        return call

    async def get_call(self, *, call_id: str, user_id: str) -> CallSession:
        return await self._get_participant_call(call_id, user_id)

    async def _get_participant_call(self, call_id: str, user_id: str) -> CallSession:
        call = await self._db.get(CallSession, call_id)
        if call is None or user_id not in {call.caller_id, call.callee_id}:
            raise CallNotFoundError("Call was not found")
        return call

    async def _require_direct_members(self, conversation_id: str, user_id: str) -> tuple[Conversation, tuple[str, ...]]:
        conversation = await self._db.get(Conversation, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise ConversationNotFoundError(conversation_id)
        rows = await self._db.scalars(
            select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id)
        )
        members = tuple(rows)
        if user_id not in members:
            raise ConversationMembershipError(conversation_id, user_id)
        if conversation.type != "direct" or len(members) != 2:
            raise CallStateError("Calls are only available for direct conversations")
        return conversation, members
