import { describe, expect, it } from 'vitest';
import {
  applyTranslationCompleted,
  applyVoiceTranscriptionCompleted,
  applyVoiceTranscriptionFailed,
  applyVoiceTranscriptionRetryAccepted,
  reconcileRealtimeMessage,
} from './message-events';
import type { Conversation, Message } from './types';

const audioAttachment = {
  id: 'audio-1',
  type: 'audio' as const,
  url: '/protected/audio-1',
  name: 'voice.webm',
  contentType: 'audio/webm',
};

function pendingVoice(overrides: Partial<Message> = {}): Message {
  return {
    id: 'voice-1',
    senderId: 'sender-1',
    senderName: 'Sender',
    conversationId: 'conversation-1',
    content: '',
    messageType: 'voice',
    transcriptionStatus: 'pending',
    timestamp: '10:00',
    status: 'delivered',
    attachments: [audioAttachment],
    ...overrides,
  };
}

const directConversation: Conversation = {
  id: 'conversation-1',
  type: 'direct',
  name: 'Recipient',
  avatar: '',
  recipient: {
    id: 'recipient-1',
    email: 'recipient@example.com',
    name: 'Recipient',
    username: 'recipient',
    avatar: '',
    nativeLanguage: 'en',
    onlineStatus: 'online',
  },
  lastMessage: '',
  lastMessageTime: '',
  unreadCount: 0,
};

describe('voice transcription lifecycle events', () => {
  it('reconciles an optimistic voice bubble to one authoritative pending message', () => {
    const optimistic = pendingVoice({ id: 'client-voice-1', status: 'sending' });
    const authoritative = pendingVoice({ id: 'voice-1', status: 'delivered' });
    const reconciled = reconcileRealtimeMessage(
      [optimistic],
      authoritative,
      'client-voice-1',
      'sender-1',
    );

    expect(reconciled).toEqual([authoritative]);
    expect(reconciled[0].messageType).toBe('voice');
    expect(reconciled[0].transcriptionStatus).toBe('pending');
  });

  it('does not duplicate an already authoritative voice bubble', () => {
    const authoritative = pendingVoice({ id: 'voice-1', status: 'delivered' });
    expect(reconcileRealtimeMessage(
      [authoritative],
      authoritative,
      undefined,
      'sender-1',
    )).toEqual([authoritative]);
  });

  it('installs the complete authoritative transcript and preserves audio metadata', () => {
    const fullTranscript = 'Dòng đầu đầy đủ.\nDòng hai giữ mã A-017 và số 1.250.000 đồng.';
    const [completed] = applyVoiceTranscriptionCompleted([pendingVoice()], {
      message_id: 'voice-1',
      conversation_id: 'conversation-1',
      original_text: fullTranscript,
      transcription_status: 'completed',
    });

    expect(completed.content).toBe(fullTranscript);
    expect(completed.transcriptionStatus).toBe('completed');
    expect(completed.attachments).toEqual([audioAttachment]);
    expect(completed.translation).toBeUndefined();
  });

  it('keeps canonical text empty and audio playable after failure', () => {
    const [failed] = applyVoiceTranscriptionFailed([pendingVoice()], {
      message_id: 'voice-1',
      conversation_id: 'conversation-1',
      transcription_status: 'failed',
      retryable: true,
    });

    expect(failed.content).toBe('');
    expect(failed.transcriptionStatus).toBe('failed');
    expect(failed.attachments).toEqual([audioAttachment]);
  });

  it('does not regress a completed voice message on a stale failure event', () => {
    const completed = pendingVoice({
      content: 'Full transcript',
      transcriptionStatus: 'completed',
    });
    expect(applyVoiceTranscriptionFailed([completed], {
      message_id: 'voice-1',
      conversation_id: 'conversation-1',
      transcription_status: 'failed',
      retryable: false,
    })[0]).toBe(completed);
  });

  it('moves a failed retry to pending in the same bubble without losing audio', () => {
    const failed = pendingVoice({ transcriptionStatus: 'failed' });
    const [retried] = applyVoiceTranscriptionRetryAccepted(
      [failed],
      'voice-1',
      'conversation-1',
    );

    expect(retried.id).toBe('voice-1');
    expect(retried.transcriptionStatus).toBe('pending');
    expect(retried.content).toBe('');
    expect(retried.attachments).toEqual([audioAttachment]);
  });

  it('ignores late transcription and translation events for a deleted voice message', () => {
    const deleted = pendingVoice({ deletedAt: '2026-08-27T10:02:00Z' });
    const completion = applyVoiceTranscriptionCompleted([deleted], {
      message_id: 'voice-1',
      conversation_id: 'conversation-1',
      original_text: 'Late provider result',
      transcription_status: 'completed',
    })[0];
    const failure = applyVoiceTranscriptionFailed([deleted], {
      message_id: 'voice-1',
      conversation_id: 'conversation-1',
      transcription_status: 'failed',
      retryable: true,
    })[0];
    const translation = applyTranslationCompleted([deleted], {
      message_id: 'voice-1',
      translation_id: 'late-translation',
      source_language: 'vi',
      target_language: 'en',
      translated_text: 'Late translation',
    }, {
      conversation: directConversation,
      currentUserId: 'recipient-1',
      preferredLanguage: 'en',
      showOriginalByDefault: false,
    })[0];

    expect(completion).toBe(deleted);
    expect(failure).toBe(deleted);
    expect(translation).toBe(deleted);
  });
});

describe('existing translation completion reducer', () => {
  it('updates completed voice through the same translation model as text', () => {
    const transcript = 'Đây là toàn bộ nội dung giọng nói, không rút gọn.';
    const completedVoice = pendingVoice({
      content: transcript,
      transcriptionStatus: 'completed',
    });
    const [translated] = applyTranslationCompleted([completedVoice], {
      message_id: 'voice-1',
      translation_id: 'translation-1',
      source_language: 'vi',
      target_language: 'en',
      translated_text: 'This is the complete voice content, without shortening.',
    }, {
      conversation: directConversation,
      currentUserId: 'recipient-1',
      preferredLanguage: 'en',
      showOriginalByDefault: false,
    });

    expect(translated.translation).toMatchObject({
      translationId: 'translation-1',
      originalText: transcript,
      translatedText: 'This is the complete voice content, without shortening.',
      status: 'success',
    });
    expect(translated.messageType).toBe('voice');
    expect(translated.attachments).toEqual([audioAttachment]);
  });

  it('preserves normal text translation behavior', () => {
    const textMessage: Message = {
      ...pendingVoice(),
      id: 'text-1',
      content: 'Xin chào',
      messageType: 'text',
      transcriptionStatus: null,
      attachments: undefined,
    };
    const [translated] = applyTranslationCompleted([textMessage], {
      message_id: 'text-1',
      translation_id: 'translation-text',
      source_language: 'vi',
      target_language: 'en',
      translated_text: 'Hello',
    }, {
      conversation: directConversation,
      currentUserId: 'recipient-1',
      preferredLanguage: 'en',
      showOriginalByDefault: false,
    });

    expect(translated.translation?.translatedText).toBe('Hello');
    expect(translated.messageType).toBe('text');
    expect(translated.transcriptionStatus).toBeNull();
  });
});
