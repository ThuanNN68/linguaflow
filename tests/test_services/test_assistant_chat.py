"""Behavioural contract tests for replies created by an ``@assistant`` mention.

These tests intentionally use a scripted LLM.  They make the data passed to a
provider inspectable and deterministic in CI, while the live end-to-end check
continues to exercise the configured model separately.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.messaging import chat as chat_module
from src.services.messaging.chat import ChatService


class _RecentMessagesDb:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self._messages = messages

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self._messages)


def _message(text: str, *, assistant_generated: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        original_text=text,
        assistant_generated=assistant_generated,
        conversation_id="conversation-1",
        sender_id="user-1",
        deleted_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "history", "user_request", "model_answer"),
    [
        (
            "summarizes a deadline and its owner",
            ["Linh sẽ hoàn thiện bản thảo song ngữ trước sáng thứ Hai."],
            "@assistant Tóm tắt việc cần làm và thời hạn.",
            "Linh cần hoàn thiện bản thảo song ngữ trước sáng thứ Hai.",
        ),
        (
            "resolves a pronoun using recent context",
            ["Minh gửi hợp đồng cho Lan.", "Cô ấy sẽ phản hồi vào chiều nay."],
            "@assistant Ai sẽ phản hồi và khi nào?",
            "Lan sẽ phản hồi vào chiều nay.",
        ),
        (
            "preserves an English request and answer",
            ["The customer requested the revised proposal by Friday."],
            "@assistant What is the customer's deadline?",
            "The revised proposal is due by Friday.",
        ),
        (
            "keeps an explicit constraint",
            ["Chỉ gửi bản thảo mới cho Linh, chưa gửi khách hàng."],
            "@assistant Bản thảo mới được gửi cho ai?",
            "Chỉ gửi cho Linh; chưa gửi khách hàng.",
        ),
        (
            "answers after an earlier assistant turn",
            [
                "Cuộc họp chuyển sang 9 giờ sáng mai.",
                "Mình đã tóm tắt: cuộc họp diễn ra lúc 9 giờ sáng mai.",
            ],
            "@assistant Cuộc họp diễn ra lúc mấy giờ?",
            "Cuộc họp diễn ra lúc 9 giờ sáng mai.",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_assistant_reply_supplies_relevant_context_and_returns_model_answer(
    monkeypatch, case, history, user_request, model_answer
):
    messages = [
        _message(text, assistant_generated=index == len(history) - 1 and "Mình" in text)
        for index, text in enumerate(history)
    ]
    trigger = _message(user_request)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=model_answer)))
    monkeypatch.setattr(chat_module, "get_assistant_llm", lambda: llm)

    result = await chat_module.ChatService(_RecentMessagesDb(messages))._assistant_reply_text(trigger)

    assert result == model_answer, case
    prompt = llm.ainvoke.await_args.args[0]
    assert user_request.removeprefix("@assistant ") in prompt[1].content
    for message in history:
        assert message in prompt[1].content
    assert "không tin cậy" in prompt[0].content
    assert "Không nhắc lại tag @assistant" in prompt[0].content


@pytest.mark.asyncio
async def test_assistant_reply_strips_tag_case_insensitively(monkeypatch):
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="Đã hiểu.")))
    monkeypatch.setattr(chat_module, "get_assistant_llm", lambda: llm)
    trigger = _message("  @ASSISTANT   Hãy hỗ trợ tôi  ")

    result = await chat_module.ChatService(_RecentMessagesDb([trigger]))._assistant_reply_text(trigger)

    assert result == "Đã hiểu."
    assert "Yêu cầu cần trả lời:\nHãy hỗ trợ tôi" in llm.ainvoke.await_args.args[0][1].content


@pytest.mark.asyncio
async def test_assistant_reply_without_request_does_not_call_model(monkeypatch):
    llm = SimpleNamespace(ainvoke=AsyncMock())
    monkeypatch.setattr(chat_module, "get_assistant_llm", lambda: llm)

    result = await chat_module.ChatService(_RecentMessagesDb([]))._assistant_reply_text(_message("@assistant"))

    assert result == "Bạn muốn mình hỗ trợ điều gì trong cuộc trò chuyện này?"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_assistant_reply_uses_safe_fallback_when_provider_fails(monkeypatch):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider unavailable")))
    monkeypatch.setattr(chat_module, "get_assistant_llm", lambda: llm)

    result = await chat_module.ChatService(_RecentMessagesDb([]))._assistant_reply_text(
        _message("@assistant Tóm tắt giúp tôi")
    )

    assert result.startswith("Mình chưa thể tạo câu trả lời đầy đủ")


@pytest.mark.asyncio
async def test_tagging_the_assistant_hides_the_request_from_everyone_else(
    conversation_factory,
    monkeypatch,
    test_db,
    test_user,
    test_user_two,
):
    """Both halves of an exchange with the assistant are private, not just the reply.

    This reverses an earlier rule under which the request stayed public. Hiding
    only the answer showed the rest of the conversation one half of an exchange
    it was not part of: everyone could read what somebody had asked the
    assistant, and nothing of what came back. The people around you should not
    learn that you consulted it at all.
    """
    conversation = await conversation_factory(test_user, [test_user_two])
    service = ChatService(test_db)
    result = await service.send_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="private-assistant-1",
        text="@assistant Tóm tắt cuộc trao đổi này",
        mentions=[{"type": "assistant"}],
    )

    assert result.for_assistant is True
    assert result.recipient_ids == ()
    assert result.message.visibility == "private"
    assert result.message.visible_to_user_id == test_user.id
    assert [
        message.id
        for message in await service.get_message_history(user_id=test_user.id, conversation_id=conversation.id)
    ] == [result.message.id]
    assert await service.get_message_history(user_id=test_user_two.id, conversation_id=conversation.id) == []

    monkeypatch.setattr(
        service,
        "_assistant_reply_text",
        AsyncMock(return_value="Tóm tắt riêng tư."),
    )
    reply = await service.create_assistant_reply(trigger_message=result.message)

    assert reply.recipient_ids == (test_user.id,)
    assert reply.message.visible_to_user_id == test_user.id
    assert reply.message.assistant_generated is True
    # Requester sees both halves; the other member sees neither.
    assert len(await service.get_message_history(user_id=test_user.id, conversation_id=conversation.id)) == 2
    assert await service.get_message_history(user_id=test_user_two.id, conversation_id=conversation.id) == []


@pytest.mark.asyncio
async def test_replying_to_an_assistant_message_is_also_addressed_to_it(
    conversation_factory,
    monkeypatch,
    test_db,
    test_user,
    test_user_two,
):
    """A follow-up needs no second tag, and is hidden the same way.

    Once the assistant has answered, hitting reply on that answer is the natural
    next turn; requiring `@assistant` again on every turn makes talking to it
    feel like operating a machine.
    """
    conversation = await conversation_factory(test_user, [test_user_two])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="Tóm tắt riêng tư.")))
    monkeypatch.setattr(chat_module, "get_assistant_llm", lambda: llm)
    service = ChatService(test_db)
    tagged = await service.send_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="assistant-thread-1",
        text="@assistant Tóm tắt cuộc trao đổi này",
        mentions=[{"type": "assistant"}],
    )
    reply = await service.create_assistant_reply(trigger_message=tagged.message)

    follow_up = await service.send_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="assistant-thread-2",
        text="Chi tiết hơn phần deadline giúp mình",
        reply_to_message_id=reply.message.id,
    )

    assert follow_up.for_assistant is True
    assert follow_up.recipient_ids == ()
    assert follow_up.message.visible_to_user_id == test_user.id


@pytest.mark.asyncio
async def test_an_ordinary_group_message_is_not_treated_as_talking_to_the_assistant(
    conversation_factory,
    test_db,
    test_user,
    test_user_two,
):
    """The guard that keeps the previous two rules from swallowing normal chat.

    A message that neither tags the assistant nor answers it belongs to the
    people in the conversation. Getting this wrong would hide someone's message
    from their colleagues and answer it as though it had been a question.
    """
    conversation = await conversation_factory(test_user, [test_user_two])
    service = ChatService(test_db)
    plain = await service.send_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="ordinary-1",
        text="Chiều nay mình họp lúc 3 giờ nhé",
    )

    assert plain.for_assistant is False
    assert plain.recipient_ids == (test_user_two.id,)
    assert plain.message.visibility == "public"
    assert [
        message.id
        for message in await service.get_message_history(user_id=test_user_two.id, conversation_id=conversation.id)
    ] == [plain.message.id]
