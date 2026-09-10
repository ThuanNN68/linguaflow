import { describe, expect, it } from 'vitest';

import type { ApiActionProposal } from './api/chat-api';
import { hasUnresolvedAppointmentClaim, proposalsForAssistantReply } from './proposal-linking';
import type { Message } from './types';

const proposal = (sourceMessageId: string): ApiActionProposal => ({
  id: `proposal-${sourceMessageId}`,
  conversation_id: 'conversation-1',
  source_message_id: sourceMessageId,
  owner_user_id: 'user-1',
  action_type: 'task',
  status: 'pending_confirmation',
  title: 'Follow up',
  details: null,
  location: null,
  scheduled_start_at: null,
  scheduled_end_at: null,
  due_at: null,
  clarification_prompt: null,
  clarification_question: null,
  confidence_score: 0.9,
  source_mode: 'on_demand',
  created_at: '2026-09-04T00:00:00Z',
  updated_at: '2026-09-04T00:00:00Z',
  confirmed_at: null,
  rejected_at: null,
  stale_at: null,
  missing_fields: '[]',
});

const assistantReply = (replyToId?: string): Message => ({
  id: 'assistant-1',
  senderId: 'user-1',
  conversationId: 'conversation-1',
  content: 'I found an action item.',
  messageType: 'text',
  transcriptionStatus: null,
  timestamp: '2026-09-04T00:00:00Z',
  status: 'sent',
  isAssistant: true,
  replyTo: replyToId ? { id: replyToId, senderName: 'User', content: 'Please help.' } : undefined,
});

describe('proposalsForAssistantReply', () => {
  it('offers recovery only for threaded assistant appointment claims', () => {
    const message = { ...assistantReply('source-1'), content: 'Đã chuẩn bị đề xuất lịch họp vào lúc 6h. Vui lòng duyệt.' };
    expect(hasUnresolvedAppointmentClaim(message)).toBe(true);
    expect(hasUnresolvedAppointmentClaim({ ...message, isAssistant: false })).toBe(false);
    expect(hasUnresolvedAppointmentClaim({ ...message, replyTo: undefined })).toBe(false);
    expect(hasUnresolvedAppointmentClaim({ ...message, content: 'Xin chào!' })).toBe(false);
  });
  it('links only proposals extracted from the message an assistant answer replies to', () => {
    expect(proposalsForAssistantReply(assistantReply('source-1'), [proposal('source-1'), proposal('source-2')]))
      .toEqual([proposal('source-1')]);
  });

  it('does not show a proposal below a non-assistant or unthreaded message', () => {
    expect(proposalsForAssistantReply({ ...assistantReply('source-1'), isAssistant: false }, [proposal('source-1')]))
      .toEqual([]);
    expect(proposalsForAssistantReply(assistantReply(), [proposal('source-1')])).toEqual([]);
  });

  it('keeps an appointment preview attached to its assistant reply', () => {
    const appointment = {
      ...proposal('calendar-request'),
      action_type: 'appointment' as const,
      title: 'Họp kế hoạch sprint',
      scheduled_start_at: '2026-09-07T02:00:00Z',
      location: 'Phòng D',
    };

    expect(proposalsForAssistantReply(assistantReply('calendar-request'), [appointment]))
      .toEqual([appointment]);
  });
});
