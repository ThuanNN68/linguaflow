import type { Conversation, Message } from './types';
import { toLanguageCode } from './api/chat-api';

export function reconcileRealtimeMessage(
  messages: Message[],
  authoritative: Message,
  clientMessageId: string | undefined,
  currentUserId: string,
): Message[] {
  const withoutOptimistic = clientMessageId
    ? messages.filter((message) => message.id !== clientMessageId)
    : messages;
  if (withoutOptimistic.some((message) => message.id === authoritative.id)) {
    return withoutOptimistic;
  }

  const repliedMessage = authoritative.replyTo
    ? withoutOptimistic.find((message) => message.id === authoritative.replyTo?.id)
    : undefined;
  const replyContent = repliedMessage
    ? (
        repliedMessage.senderId === currentUserId || repliedMessage.translation?.showOriginal
          ? repliedMessage.content
          : repliedMessage.translation?.translatedText || repliedMessage.content
      )
    : undefined;
  const messageWithReply = repliedMessage && authoritative.replyTo ? {
    ...authoritative,
    replyTo: {
      id: repliedMessage.id,
      senderName: repliedMessage.senderName || 'Message',
      content: replyContent || repliedMessage.content,
    },
  } : authoritative;
  return [...withoutOptimistic, messageWithReply];
}

export interface VoiceTranscriptionCompletedPayload {
  message_id: string;
  conversation_id: string;
  original_text: string;
  transcription_status: 'completed';
}

export interface VoiceTranscriptionFailedPayload {
  message_id: string;
  conversation_id: string;
  transcription_status: 'failed';
  retryable: boolean;
}

export function applyVoiceTranscriptionCompleted(
  messages: Message[],
  event: VoiceTranscriptionCompletedPayload,
): Message[] {
  return messages.map((message) => (
    message.id === event.message_id
      && message.conversationId === event.conversation_id
      && message.messageType === 'voice'
      && (message.transcriptionStatus === 'pending' || message.transcriptionStatus === 'failed')
      && !message.deletedAt
      ? {
          ...message,
          content: event.original_text,
          transcriptionStatus: event.transcription_status,
        }
      : message
  ));
}

export function applyVoiceTranscriptionFailed(
  messages: Message[],
  event: VoiceTranscriptionFailedPayload,
): Message[] {
  return messages.map((message) => (
    message.id === event.message_id
      && message.conversationId === event.conversation_id
      && message.messageType === 'voice'
      && message.transcriptionStatus === 'pending'
      && !message.deletedAt
      ? {
          ...message,
          content: '',
          transcriptionStatus: event.transcription_status,
        }
      : message
  ));
}

export function applyVoiceTranscriptionRetryAccepted(
  messages: Message[],
  messageId: string,
  conversationId: string,
): Message[] {
  return messages.map((message) => (
    message.id === messageId
      && message.conversationId === conversationId
      && message.messageType === 'voice'
      && message.transcriptionStatus === 'failed'
      && !message.deletedAt
      ? { ...message, content: '', transcriptionStatus: 'pending' }
      : message
  ));
}

interface TranslationCompletedPayload {
  message_id: string;
  translation_id?: unknown;
  source_language?: unknown;
  target_language?: unknown;
  translated_text?: unknown;
  content?: unknown;
  status?: unknown;
}

interface TranslationDisplayOptions {
  conversation?: Conversation;
  currentUserId: string;
  preferredLanguage: string;
  showOriginalByDefault: boolean;
}

export function applyTranslationCompleted(
  messages: Message[],
  event: TranslationCompletedPayload,
  options: TranslationDisplayOptions,
): Message[] {
  const targetLanguage = String(event.target_language || options.preferredLanguage);
  const translatedText = String(event.translated_text ?? event.content ?? '');
  const sourceLanguage = toLanguageCode(String(event.source_language || 'en'));
  const status = event.status === 'failed' || !translatedText ? 'failed' : 'success';

  return messages.map((message) => {
    if (message.id !== event.message_id || message.deletedAt) return message;
    const isOwnMessage = message.senderId === options.currentUserId;
    if (isOwnMessage && options.conversation?.type !== 'direct') return message;
    const displayLanguage = isOwnMessage
      ? options.conversation?.recipient?.nativeLanguage
      : options.preferredLanguage;
    if (!displayLanguage || targetLanguage !== displayLanguage) return message;
    return {
      ...message,
      translation: {
        translationId: String(event.translation_id),
        originalText: message.content,
        originalLanguage: sourceLanguage,
        translatedText,
        targetLanguage: toLanguageCode(targetLanguage),
        status,
        showOriginal: isOwnMessage || options.showOriginalByDefault,
      },
    };
  });
}
