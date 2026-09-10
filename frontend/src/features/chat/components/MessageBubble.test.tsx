import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageAttachmentCard } from './MessageAttachmentCard';
import { MessageBubble } from './MessageBubble';
import type { Message, MessageAttachment, User } from '../types';

const currentUser: User = {
  id: 'recipient-1',
  email: 'recipient@example.com',
  name: 'Recipient',
  username: 'recipient',
  avatar: '',
  nativeLanguage: 'en',
  onlineStatus: 'online',
};

const audioAttachment: MessageAttachment = {
  id: 'audio-1',
  type: 'audio',
  url: '/protected/audio-1',
  name: 'voice.webm',
  size: '2 KB',
  contentType: 'audio/webm',
};

function voiceMessage(overrides: Partial<Message> = {}): Message {
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

function renderBubble(
  message: Message,
  overrides: Partial<React.ComponentProps<typeof MessageBubble>> = {},
) {
  const props: React.ComponentProps<typeof MessageBubble> = {
    message,
    currentUser,
    isGroup: false,
    showAvatar: true,
    showSenderName: true,
    onReact: vi.fn(),
    onReply: vi.fn(),
    onCopy: vi.fn(),
    onToggleOriginal: vi.fn(),
    onRetryTranslation: vi.fn(),
    onRetryTranscription: vi.fn(),
    isRetryingTranscription: false,
    onRateTranslation: vi.fn(),
    onEditTranslation: vi.fn(),
    onForward: vi.fn(),
    onSaveMessage: vi.fn(),
    onDeleteMessage: vi.fn(),
    onDownloadAttachment: vi.fn(),
    onLoadAttachmentPreview: vi.fn().mockResolvedValue('blob:protected-audio'),
    language: 'en',
    ...overrides,
  };
  return { ...render(<MessageBubble {...props} />), props };
}

describe('authenticated audio attachment playback', () => {
  it('loads protected audio through the existing preview callback and revokes its object URL', async () => {
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const onLoadPreview = vi.fn().mockResolvedValue('blob:authenticated-audio');
    const view = render(
      <MessageAttachmentCard
        attachment={audioAttachment}
        language="en"
        onDownload={vi.fn()}
        onLoadPreview={onLoadPreview}
      />,
    );

    await waitFor(() => expect(view.container.querySelector('audio')?.getAttribute('src')).toBe('blob:authenticated-audio'));
    expect(onLoadPreview).toHaveBeenCalledWith(audioAttachment);

    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:authenticated-audio');
  });

  it('keeps ordinary file attachments on the download path', () => {
    const fileAttachment: MessageAttachment = {
      ...audioAttachment,
      id: 'file-1',
      type: 'file',
      name: 'notes.pdf',
      contentType: 'application/pdf',
    };
    const onDownload = vi.fn();
    const onLoadPreview = vi.fn();
    render(
      <MessageAttachmentCard
        attachment={fileAttachment}
        language="en"
        onDownload={onDownload}
        onLoadPreview={onLoadPreview}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Download file: notes.pdf' }));
    expect(onDownload).toHaveBeenCalledWith(fileAttachment);
    expect(onLoadPreview).not.toHaveBeenCalled();
  });

  it('keeps image attachments on the authenticated preview path', async () => {
    const imageAttachment: MessageAttachment = {
      ...audioAttachment,
      id: 'image-1',
      type: 'image',
      name: 'photo.png',
      contentType: 'image/png',
    };
    const onLoadPreview = vi.fn().mockResolvedValue('blob:authenticated-image');
    const view = render(
      <MessageAttachmentCard
        attachment={imageAttachment}
        language="en"
        onDownload={vi.fn()}
        onLoadPreview={onLoadPreview}
      />,
    );

    await waitFor(() => expect(view.container.querySelector('img')?.getAttribute('src')).toBe('blob:authenticated-image'));
    expect(onLoadPreview).toHaveBeenCalledWith(imageAttachment);
  });
});

describe('voice bubble lifecycle and translation UI', () => {
  it('renders playable audio and localized pending transcription state', async () => {
    const view = renderBubble(voiceMessage());

    expect(screen.getByText('Transcribing…')).toBeDefined();
    await waitFor(() => expect(view.container.querySelector('audio')).not.toBeNull());
    expect(view.props.onLoadAttachmentPreview).toHaveBeenCalledWith(audioAttachment);
  });

  it('keeps audio while rendering transcription failure without fake message text', async () => {
    const view = renderBubble(voiceMessage({ transcriptionStatus: 'failed' }));

    expect(screen.getByText('Transcription unavailable')).toBeDefined();
    expect(screen.queryByText('Voice message', { selector: 'p' })).toBeNull();
    await waitFor(() => expect(view.container.querySelector('audio')).not.toBeNull());
  });

  it('retries a failed transcription in place and disables duplicate clicks while accepting it', async () => {
    const message = voiceMessage({ transcriptionStatus: 'failed' });
    const view = renderBubble(message);
    const retry = screen.getByRole('button', { name: 'Retry transcription' });

    fireEvent.click(retry);
    expect(view.props.onRetryTranscription).toHaveBeenCalledWith('voice-1');

    view.rerender(<MessageBubble
      {...view.props}
      message={message}
      isRetryingTranscription
    />);
    expect((screen.getByRole('button', { name: 'Retrying transcription…' }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(view.container.querySelector('audio')).not.toBeNull());
  });

  it('renders the full completed transcript through normal message text', () => {
    const transcript = 'First complete sentence.\nSecond complete sentence with code A-017.';
    const view = renderBubble(voiceMessage({
      content: transcript,
      transcriptionStatus: 'completed',
    }));

    expect(view.container.querySelector('p.whitespace-pre-wrap')?.textContent).toBe(transcript);
    expect(screen.queryByText('Transcribing…')).toBeNull();
  });

  it('uses the existing Show original / Show translation controls for completed voice', () => {
    const transcript = 'Toàn bộ bản ghi gốc.';
    const translatedText = 'The complete original transcript.';
    const message = voiceMessage({
      content: transcript,
      transcriptionStatus: 'completed',
      translation: {
        translationId: 'translation-1',
        originalText: transcript,
        originalLanguage: 'vi',
        translatedText,
        targetLanguage: 'en',
        status: 'success',
        showOriginal: false,
      },
    });
    const view = renderBubble(message);

    expect(screen.getByText(translatedText)).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));
    expect(view.props.onToggleOriginal).toHaveBeenCalledWith('voice-1');

    view.rerender(<MessageBubble
      {...view.props}
      message={{
        ...message,
        translation: { ...message.translation!, showOriginal: true },
      }}
    />);
    expect(screen.getByText(transcript)).toBeDefined();
    expect(screen.getByRole('button', { name: 'Show translation' })).toBeDefined();
  });
});

describe('message action footer', () => {
  it('renders the review action inside the same visual message bubble', () => {
    const view = renderBubble(
      voiceMessage({
        id: 'assistant-1',
        content: 'Tôi đã chuẩn bị lịch hẹn để bạn duyệt.',
        messageType: 'text',
        transcriptionStatus: null,
        attachments: [],
        isAssistant: true,
      }),
      { footer: <button type="button">Kiểm tra khung xét duyệt lịch</button> },
    );

    const action = screen.getByRole('button', { name: 'Kiểm tra khung xét duyệt lịch' });
    const bubble = action.closest('div.relative.rounded-2xl');

    expect(bubble).not.toBeNull();
    expect(bubble?.contains(view.container.querySelector('p.whitespace-pre-wrap'))).toBe(true);
  });
});
