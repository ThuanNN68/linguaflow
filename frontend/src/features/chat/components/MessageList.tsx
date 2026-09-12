import React, { useEffect, useRef } from 'react';
import { CalendarDays, Languages } from 'lucide-react';
import { Message, User, Conversation, LanguageCode, MessageAttachment } from '../types';
import { interactionText } from '../i18n';
import { MessageBubble } from './MessageBubble';
import type { ApiActionProposal } from '../api/chat-api';
import {
  hasUnresolvedAppointmentClaim,
  latestAssistantReplyId,
  proposalsForAssistantReply,
} from '../proposal-linking';
import { MissingAppointmentCard } from './MissingAppointmentCard';
import { missingFields } from '../proposal-approval';

function missingProposalSummary(proposal: ApiActionProposal): string | null {
  return missingFields(proposal).length > 0
    ? 'Thông tin lịch cần xác nhận'
    : null;
}

interface MessageListProps {
  messages: Message[];
  currentUser: User;
  conversation: Conversation;
  onReact: (messageId: string, emoji: string) => void;
  onReply: (message: Message) => void;
  onCopy: (text: string) => void;
  onSpeak?: (messageId: string, language: string, source: 'original' | 'translation') => void;
  onStopSpeak?: () => void;
  speakingMessageId?: string | null;
  onToggleOriginal: (messageId: string) => void;
  onRetryTranslation: (messageId: string) => void;
  onRetryTranscription: (messageId: string) => void;
  retryingTranscriptionIds: ReadonlySet<string>;
  onRateTranslation: (messageId: string, translationId: string, rating: 1 | 5) => void;
  onEditTranslation: (messageId: string, translationId: string, editedText: string) => void;
  onForward: (message: Message) => void;
  onSaveMessage: (messageId: string) => void;
  onDeleteMessage?: (messageId: string) => void;
  onDownloadAttachment: (attachment: MessageAttachment) => void;
  onLoadAttachmentPreview: (attachment: MessageAttachment) => Promise<string>;
  onStartDirectChat?: (userId: string) => void;
  pendingProposals?: ApiActionProposal[];
  calendarToken?: string;
  onReviewProposals?: (proposals: ApiActionProposal[]) => void;
  language: LanguageCode;
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  currentUser,
  conversation,
  onReact,
  onReply,
  onCopy,
  onSpeak,
  onStopSpeak,
  speakingMessageId,
  onToggleOriginal,
  onRetryTranslation,
  onRetryTranscription,
  retryingTranscriptionIds,
  onRateTranslation,
  onEditTranslation,
  onForward,
  onSaveMessage,
  onDeleteMessage,
  onDownloadAttachment,
  onLoadAttachmentPreview,
  onStartDirectChat,
  pendingProposals = [],
  calendarToken,
  onReviewProposals,
  language,
}) => {
  const newestAssistantReplyId = latestAssistantReplyId(messages);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isGroup = conversation.type === 'group';

  const dateKey = (value?: string) => value ? new Date(value).toLocaleDateString('en-CA') : undefined;
  const dateLabel = (value?: string) => {
    if (!value) return undefined;
    const date = new Date(value);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (dateKey(value) === dateKey(today.toISOString())) return language === 'vi' ? 'Hôm nay' : 'Today';
    if (dateKey(value) === dateKey(yesterday.toISOString())) return language === 'vi' ? 'Hôm qua' : 'Yesterday';
    return new Intl.DateTimeFormat(language === 'vi' ? 'vi-VN' : 'en-US', {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
    }).format(date);
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, conversation.isTyping]);

  return (
    <div
      id="chat-messages-container"
      className="flex-1 overflow-y-auto w-full px-4 sm:px-6 md:px-8 lg:px-10 py-6 space-y-1.5 transition-colors"
      role="log"
      aria-label="Chat messages"
    >
      {/* Encryption & Security Greeting Banner */}
      <div className="flex justify-center my-3">
        <div className="px-3.5 py-1.5 rounded-full text-[11px] font-medium text-[#74798C] dark:text-[#9DA3B4] bg-[#F4F5F8] dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2A2E3D] text-center max-w-md">
          <Languages className="mr-1 inline-block h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
          {interactionText(language, 'Messages are translated in real-time. Speak your native language freely.')}
        </div>
      </div>

      {messages.map((message, index) => {
        const isOutgoing = message.senderId === currentUser.id && !message.isAssistant;
        const prevMessage = index > 0 ? messages[index - 1] : null;
        const nextMessage = index < messages.length - 1 ? messages[index + 1] : null;

        // Determine if consecutive message from same sender
        const isSameSenderAsPrev = prevMessage?.senderId === message.senderId && prevMessage?.isAssistant === message.isAssistant;
        const isSameSenderAsNext = nextMessage?.senderId === message.senderId && nextMessage?.isAssistant === message.isAssistant;

        // Show avatar only for the last message in a consecutive cluster (for incoming)
        const showAvatar = !isOutgoing && !isSameSenderAsNext;
        // Show sender name on first message of cluster (for incoming group chats)
        const showSenderName = !isOutgoing && !isSameSenderAsPrev;
        const divider = message.dateDivider ?? (dateKey(message.createdAt) !== dateKey(prevMessage?.createdAt)
          ? dateLabel(message.createdAt)
          : undefined);
        // An assistant answer replies to the message the proposal was extracted
        // from.  This keeps the approval control attached to that answer, rather
        // than making people hunt for a separate card at the bottom of the chat.
        const messageProposals = proposalsForAssistantReply(message, pendingProposals);
        const firstProposal = messageProposals[0];
        const hasMultipleProposals = messageProposals.length > 1;
        const missingSummary = firstProposal ? missingProposalSummary(firstProposal) : null;
        const isNewestAssistantReply = message.id === newestAssistantReplyId;
        const reviewFooter = isNewestAssistantReply && firstProposal && onReviewProposals ? (
          <div className="-mx-4 -mb-2.5 mt-3 flex items-center gap-2.5 border-t border-[#E8EAF0] px-4 py-3 dark:border-[#363B49]">
            <CalendarDays className="h-5 w-5 flex-none text-[#2563EB]" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">Xem lịch hẹn</span>
              <span className={`mt-0.5 block truncate text-[11px] ${
                missingSummary ? 'text-amber-700 dark:text-amber-300' : 'text-[#74798C] dark:text-[#9DA3B4]'
              }`}>
                {hasMultipleProposals
                  ? `${messageProposals.length} đề xuất cần xem`
                  : missingSummary ? 'Cần xác nhận' : 'Sẵn sàng để xem'}
              </span>
            </span>
            <button
              type="button"
              onClick={() => onReviewProposals(messageProposals)}
              aria-label={hasMultipleProposals ? 'Xem các lịch trợ lý đề xuất' : `Xem và chỉnh lịch ${firstProposal.title}`}
              className="shrink-0 rounded-lg bg-[#2563EB] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[#1D4ED8]"
            >
              Xem
            </button>
          </div>
        ) : isNewestAssistantReply && calendarToken && onReviewProposals && hasUnresolvedAppointmentClaim(message) ? (
          <MissingAppointmentCard token={calendarToken} conversationId={conversation.id}
            sourceMessageId={message.replyTo!.id} onReview={onReviewProposals} />
        ) : undefined;

        return (
          <React.Fragment key={message.id}>
            {/* Time / Date Separator */}
            {divider && (
              <div className="flex items-center justify-center my-6">
                <div className="flex items-center w-full max-w-sm gap-3">
                  <div className="flex-1 h-[1px] bg-[#E8EAF0] dark:bg-[#2A2E3D]" />
                  <span className="text-[11px] font-bold tracking-wider text-[#8A8F9E] dark:text-[#74798C] uppercase select-none px-1">
                    {divider}
                  </span>
                  <div className="flex-1 h-[1px] bg-[#E8EAF0] dark:bg-[#2A2E3D]" />
                </div>
              </div>
            )}

            {/* Bubble */}
            <MessageBubble
              message={message}
              currentUser={currentUser}
              isGroup={isGroup}
              showAvatar={showAvatar}
              showSenderName={showSenderName}
              onReact={onReact}
              onReply={onReply}
              onCopy={onCopy}
              onSpeak={onSpeak}
              onStopSpeak={onStopSpeak}
              isSpeaking={speakingMessageId === message.id}
              onToggleOriginal={onToggleOriginal}
              onRetryTranslation={onRetryTranslation}
              onRetryTranscription={onRetryTranscription}
              isRetryingTranscription={retryingTranscriptionIds.has(message.id)}
              onRateTranslation={onRateTranslation}
              onEditTranslation={onEditTranslation}
              onForward={onForward}
              onSaveMessage={onSaveMessage}
              onDeleteMessage={onDeleteMessage}
              onDownloadAttachment={onDownloadAttachment}
              onLoadAttachmentPreview={onLoadAttachmentPreview}
              onStartDirectChat={onStartDirectChat}
              footer={reviewFooter}
              language={language}
            />
          </React.Fragment>
        );
      })}

      {/* Typing Indicator */}
      {conversation.isTyping && (
        <div className="flex items-center gap-2.5 my-2 animate-in fade-in duration-200">
          <img
            src={conversation.avatar}
            alt={conversation.name}
            className="w-7 h-7 rounded-full object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
            referrerPolicy="no-referrer"
          />
          <div className="flex items-center gap-2 px-3.5 py-2 rounded-2xl rounded-bl-sm bg-white dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2E3342] shadow-sm">
            <span className="text-xs text-[#2563EB] font-medium">
              {conversation.typingUser || conversation.name} is typing
            </span>
            <div className="flex gap-1 items-center">
              <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce" />
              <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce [animation-delay:0.2s]" />
              <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce [animation-delay:0.4s]" />
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
};
