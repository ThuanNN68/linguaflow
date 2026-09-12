import type { ApiActionProposal } from './api/chat-api';
import type { Message } from './types';

/**
 * Return action proposals that belong with one assistant response.
 *
 * A proposal is extracted from the human message that prompted the assistant,
 * while the response itself is stored as a reply to that message.  Joining via
 * ``replyTo.id`` keeps approval controls scoped to the relevant answer and
 * avoids displaying another member's pending actions below an unrelated reply.
 */
export function proposalsForAssistantReply(
  message: Message,
  proposals: readonly ApiActionProposal[],
): ApiActionProposal[] {
  const sourceMessageId = message.isAssistant ? message.replyTo?.id : undefined;
  if (!sourceMessageId) return [];

  return proposals.filter((proposal) => proposal.source_message_id === sourceMessageId);
}

/**
 * A chat is a running conversation, not a backlog of approval controls.
 * Keep the inline calendar card on the newest Assistant turn only; older
 * proposals remain safely available in the Task inbox.
 */
export function latestAssistantReplyId(messages: readonly Message[]): string | undefined {
  return [...messages]
    .reverse()
    .find((message) => message.isAssistant && message.replyTo?.id)?.id;
}

/** Legacy replies can claim a proposal exists even when extraction failed.
 *
 * An in-progress reply ("đang kiểm tra") is deliberately not a claim: the
 * background extractor has not produced durable data yet, so showing a review
 * card at that point promises a calendar proposal that does not exist. */
export function hasUnresolvedAppointmentClaim(message: Message): boolean {
  return Boolean(message.isAssistant && message.replyTo?.id
    && /đã chuẩn bị[\s\S]*đề xuất lịch/iu.test(message.content));
}
