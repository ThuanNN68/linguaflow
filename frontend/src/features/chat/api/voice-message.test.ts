import { describe, expect, it, vi } from 'vitest';
import {
  toConversation,
  toApiMessageFromRealtime,
  toMessage,
  toMessageAttachment,
  formatRelativeMessageTime,
  type ApiAttachment,
  type ApiMessage,
} from './chat-api';
import { uploadAndSendVoiceMessage } from './voice-message';
import { VoiceRecorderError } from '../voice-recorder';
import type { Message, User } from '../types';

const sender: Pick<User, 'id' | 'name' | 'avatar'> = {
  id: 'sender-1',
  name: 'Sender',
  avatar: '/sender.png',
};

const uploadedAudio: ApiAttachment = {
  id: 'attachment-1',
  conversation_id: 'conversation-1',
  filename: 'voice-1.ogg',
  content_type: 'audio/ogg;codecs=vorbis',
  size: 2048,
  created_at: '2026-08-27T10:00:00Z',
  download_url: '/api/v1/conversations/conversation-1/attachments/attachment-1',
};

describe('formatRelativeMessageTime', () => {
  it('shows elapsed time from the newest message to now', () => {
    const now = Date.parse('2026-08-30T12:00:00Z');

    expect(formatRelativeMessageTime('2026-08-30T11:55:00Z', 'en', now)).toBe('5 minutes ago');
    expect(formatRelativeMessageTime('2026-08-30T10:00:00Z', 'vi', now)).toBe('2 giờ trước');
  });
});

function socket(send: (data: string) => void = vi.fn()) {
  return { readyState: WebSocket.OPEN, send };
}

describe('uploadAndSendVoiceMessage', () => {
  it('reuses attachment upload and sends the dedicated voice event', async () => {
    const file = new File(['recorded-audio'], 'voice.ogg', { type: 'audio/ogg;codecs=vorbis' });
    const upload = vi.fn().mockResolvedValue(uploadedAudio);
    const send = vi.fn();
    const stages: string[] = [];
    const optimistic: Message[] = [];

    const clientMessageId = await uploadAndSendVoiceMessage({
      token: 'access-token',
      conversationId: 'conversation-1',
      file,
      replyTo: { id: 'reply-1', senderName: 'Recipient', content: 'Earlier message' },
      socket: socket(send),
      sender,
      onStage: (stage) => stages.push(stage),
      onOptimistic: (message) => optimistic.push(message),
      onOptimisticRejected: vi.fn(),
    }, {
      upload,
      createClientMessageId: () => 'client-voice-1',
      now: () => new Date('2026-08-27T10:01:00Z'),
    });

    expect(upload).toHaveBeenCalledWith('access-token', 'conversation-1', file);
    expect(stages).toEqual(['uploading', 'sending']);
    expect(clientMessageId).toBe('client-voice-1');
    expect(optimistic).toHaveLength(1);
    expect(optimistic[0]).toMatchObject({
      id: 'client-voice-1',
      clientMessageId: 'client-voice-1',
      content: '',
      messageType: 'voice',
      transcriptionStatus: 'pending',
      status: 'sending',
      attachments: [{ id: 'attachment-1', type: 'audio' }],
    });

    const event = JSON.parse(send.mock.calls[0][0]);
    expect(event).toEqual({
      type: 'send_voice_message',
      client_message_id: 'client-voice-1',
      conversation_id: 'conversation-1',
      attachment_id: 'attachment-1',
      reply_to_message_id: 'reply-1',
    });
    expect(event).not.toHaveProperty('text');
    expect(event.type).not.toBe('send_message');
  });

  it('fails cleanly when upload fails and never sends a socket event', async () => {
    const send = vi.fn();
    await expect(uploadAndSendVoiceMessage({
      token: 'access-token',
      conversationId: 'conversation-1',
      file: new File(['audio'], 'voice.webm', { type: 'audio/webm' }),
      socket: socket(send),
      sender,
      onStage: vi.fn(),
      onOptimistic: vi.fn(),
      onOptimisticRejected: vi.fn(),
    }, {
      upload: vi.fn().mockRejectedValue(new Error('upload failed')),
    })).rejects.toMatchObject({ code: 'voice_upload_failed' } satisfies Partial<VoiceRecorderError>);
    expect(send).not.toHaveBeenCalled();
  });

  it('removes the optimistic bubble after a synchronous socket send failure', async () => {
    const rejected = vi.fn();
    await expect(uploadAndSendVoiceMessage({
      token: 'access-token',
      conversationId: 'conversation-1',
      file: new File(['audio'], 'voice.webm', { type: 'audio/webm' }),
      socket: socket(() => { throw new Error('socket closed'); }),
      sender,
      onStage: vi.fn(),
      onOptimistic: vi.fn(),
      onOptimisticRejected: rejected,
    }, {
      upload: vi.fn().mockResolvedValue(uploadedAudio),
      createClientMessageId: () => 'client-failed',
    })).rejects.toMatchObject({ code: 'voice_send_failed' } satisfies Partial<VoiceRecorderError>);
    expect(rejected).toHaveBeenCalledWith('client-failed');
  });
});

describe('chat API voice mapping', () => {
  it.each([
    ['image/png', 'image'],
    ['audio/webm;codecs=opus', 'audio'],
    ['application/pdf', 'file'],
  ] as const)('maps %s attachments to %s without changing other attachment classes', (contentType, expected) => {
    expect(toMessageAttachment({ ...uploadedAudio, content_type: contentType }).type).toBe(expected);
  });

  it('preserves REST message type and transcription status', () => {
    const apiMessage: ApiMessage = {
      id: 'voice-1',
      client_message_id: 'client-voice-1',
      conversation_id: 'conversation-1',
      sender_id: 'sender-1',
      original_text: '',
      message_type: 'voice',
      transcription_status: 'pending',
      source_language: 'vi',
      translations: [],
      created_at: '2026-08-27T10:00:00Z',
      deleted_at: null,
      reply_to_message_id: null,
      attachment: uploadedAudio,
    };

    expect(toMessage(apiMessage, new Map(), 'en')).toMatchObject({
      id: 'voice-1',
      clientMessageId: 'client-voice-1',
      content: '',
      messageType: 'voice',
      transcriptionStatus: 'pending',
      attachments: [{ type: 'audio' }],
    });
  });

  it.each([
    ['pending', '', undefined],
    ['completed', 'Bản ghi đầy đủ sau khi tải lại.', 'The full transcript after refresh.'],
    ['failed', '', undefined],
  ] as const)('rehydrates a %s voice lifecycle from durable history', (status, transcript, translatedText) => {
    const message = toMessage({
      id: `voice-${status}`,
      client_message_id: `client-${status}`,
      conversation_id: 'conversation-1',
      sender_id: 'sender-1',
      original_text: transcript,
      message_type: 'voice',
      transcription_status: status,
      source_language: 'vi',
      translations: translatedText ? [{
        translation_id: 'translation-1',
        target_language: 'en',
        translated_text: translatedText,
      }] : [],
      created_at: '2026-08-27T10:00:00Z',
      deleted_at: null,
      reply_to_message_id: null,
      attachment: uploadedAudio,
    }, new Map(), 'en');

    expect(message).toMatchObject({
      clientMessageId: `client-${status}`,
      content: transcript,
      messageType: 'voice',
      transcriptionStatus: status,
      attachments: [{ id: 'attachment-1', type: 'audio' }],
    });
    expect(message.translation?.translatedText).toBe(translatedText);
  });

  it.each(['pending', 'failed'] as const)('localizes a %s voice conversation preview without fake transcript text', (status) => {
    const conversation = toConversation({
      id: 'conversation-1',
      type: 'direct',
      title: null,
      member_ids: ['current-user', 'sender-1'],
      members: [{
        id: 'sender-1',
        email: 'sender@example.com',
        username: 'sender',
        display_name: 'Sender',
        preferred_language: 'vi',
      }],
      last_message: '',
      last_message_at: '2026-08-27T10:00:00Z',
      last_message_type: 'voice',
      last_message_transcription_status: status,
      online_member_ids: [],
      unread_count: 2,
    }, 'current-user', 'vi');

    expect(conversation.lastMessage).toBe('Tin nhắn thoại');
    expect(conversation.lastMessageType).toBe('voice');
    expect(conversation.lastMessageTranscriptionStatus).toBe(status);
    expect(conversation.unreadCount).toBe(2);
  });

  it('preserves realtime message_created/message_received lifecycle fields', () => {
    const realtime = toApiMessageFromRealtime({
      id: 'voice-2',
      conversation_id: 'conversation-1',
      sender_id: 'sender-1',
      original_text: '',
      message_type: 'voice',
      transcription_status: 'pending',
      created_at: '2026-08-27T10:02:00Z',
      attachment: uploadedAudio,
    }, 'client-voice-2', 'vi');

    expect(realtime).toMatchObject({
      id: 'voice-2',
      client_message_id: 'client-voice-2',
      message_type: 'voice',
      transcription_status: 'pending',
      original_text: '',
      attachment: uploadedAudio,
    });
  });
});
