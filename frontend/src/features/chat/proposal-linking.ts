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

/** Legacy replies can claim a proposal exists even when extraction failed.
 * This only offers recovery; it must never invent appointment data. */
export function hasUnresolvedAppointmentClaim(message: Message): boolean {
  return Boolean(message.isAssistant && message.replyTo?.id
    && /(?:đã chuẩn bị|đang kiểm tra)[\s\S]*đề xuất lịch/iu.test(message.content));
}
