import React, { useState } from 'react';
import { Message, User, LanguageCode, MessageAttachment } from '../types';
import { interactionText } from '../i18n';
import { MessageAttachmentCard } from './MessageAttachmentCard';
import {
  Check,
  CheckCheck,
  Lock,
  RotateCw,
  Sparkles,
  Smile,
  Reply,
  Copy,
  MoreHorizontal,
  Bookmark,
  Trash2,
  AlertCircle,
  ThumbsDown,
  ThumbsUp,
  Pencil,
  X,
  Volume2,
  VolumeX
} from 'lucide-react';

interface MessageBubbleProps {
  message: Message;
  currentUser: User;
  isGroup: boolean;
  showAvatar: boolean;
  showSenderName: boolean;
  onReact: (messageId: string, emoji: string) => void;
  onReply: (message: Message) => void;
  onCopy: (text: string) => void;
  onToggleOriginal: (messageId: string) => void;
  onRetryTranslation: (messageId: string) => void;
  onRetryTranscription: (messageId: string) => void;
  isRetryingTranscription: boolean;
  onRateTranslation: (messageId: string, translationId: string, rating: 1 | 5) => void;
  onEditTranslation: (messageId: string, translationId: string, editedText: string) => void;
  onForward: (message: Message) => void;
  onSaveMessage: (messageId: string) => void;
  onDeleteMessage?: (messageId: string) => void;
  onDownloadAttachment: (attachment: MessageAttachment) => void;
  onLoadAttachmentPreview: (attachment: MessageAttachment) => Promise<string>;
  onStartDirectChat?: (userId: string) => void;
  onSpeak?: (messageId: string, language: string, source: 'original' | 'translation') => void;
  isSpeaking?: boolean;
  onStopSpeak?: () => void;
  /** Optional action content that belongs immediately below this message. */
  footer?: React.ReactNode;
  language: LanguageCode;
}

const QUICK_EMOJIS = ['❤️', '👍', '🔥', '😂', '🎉', '👏'];

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  currentUser,
  isGroup,
  showAvatar,
  showSenderName,
  onReact,
  onReply,
  onCopy,
  onToggleOriginal,
  onRetryTranslation,
  onRetryTranscription,
  isRetryingTranscription,
  onRateTranslation,
  onEditTranslation,
  onForward,
  onSaveMessage,
  onDeleteMessage,
  onDownloadAttachment,
  onLoadAttachmentPreview,
  onStartDirectChat,
  onSpeak,
  isSpeaking,
  onStopSpeak,
  footer,
  language,
}) => {
  const [showActions, setShowActions] = useState(false);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [isEditingTranslation, setIsEditingTranslation] = useState(false);
  const [editedTranslation, setEditedTranslation] = useState("");

  const isOutgoing = message.senderId === currentUser.id && !message.isAssistant;
  const isVoice = message.messageType === 'voice';
  const isDeleted = Boolean(message.deletedAt);
  const hasAttachments = Boolean(message.attachments?.length);
  const isAttachmentCaption = !isVoice && hasAttachments && /^Shared\s+/i.test(message.content.trim());
  const hasTranslation = !!message.translation;
  // Direct-message senders can review the exact wording delivered to the
  // other participant. Group senders stay on the original-only path because
  // several recipient languages make one bubble ambiguous.
  const canShowTranslation = !message.isAssistant
    && !isDeleted
    && !isAttachmentCaption
    && (!isVoice || message.transcriptionStatus === 'completed')
    && (!isOutgoing || !isGroup);
  const isTranslated = canShowTranslation && hasTranslation && message.translation?.status === 'success';
  const isTranslating = canShowTranslation && hasTranslation && message.translation?.status === 'pending';
  const isTranslationFailed = canShowTranslation && hasTranslation && message.translation?.status === 'failed';
  const isShowingOriginal = message.translation?.showOriginal ?? (isOutgoing && !isGroup);
  const showTranslatedAsPrimary = isTranslated && !isShowingOriginal;
  const canReviewTranslation = Boolean(
    canShowTranslation && message.translation?.translationId && message.translation.status === 'success',
  );
  const shouldRenderText = isDeleted || !isVoice || message.transcriptionStatus === 'completed';
  const hasUserMention = message.mentions?.some((mention) => mention.type === 'user');
  const hasAssistantMention = message.mentions?.some((mention) => mention.type === 'assistant');
  const renderMessageContent = (value: string) => value.split(/(\*\*[\s\S]+?\*\*|\*[^*\n]+?\*|@[A-Za-z0-9_.-]+)/g).map((part, index) => {
    if (part.toLowerCase() === '@assistant' && hasAssistantMention) {
      return <span key={index} className="rounded bg-violet-100 px-1 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200">{part}</span>;
    }
    if (part.startsWith('@') && hasUserMention) {
      return <span key={index} className="rounded bg-[#EFF6FF] px-1 text-[#2563EB] dark:bg-[#2563EB]/20 dark:text-[#93C5FD]">{part}</span>;
    }
    // Assistant text is model output.  We deliberately support just the
    // harmless inline emphasis syntax instead of parsing HTML or injecting it
    // into the DOM.  React still escapes the actual text content.
    if (message.isAssistant && part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index} className="font-semibold">{part.slice(2, -2)}</strong>;
    }
    if (message.isAssistant && part.startsWith('*') && part.endsWith('*')) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return part;
  });

  const beginTranslationEdit = () => {
    setEditedTranslation(message.translation?.editedText || message.translation?.translatedText || '');
    setIsEditingTranslation(true);
  };

  const saveTranslationEdit = () => {
    const text = editedTranslation.trim();
    if (!text || !message.translation?.translationId) return;
    onEditTranslation(message.id, message.translation.translationId, text);
    setIsEditingTranslation(false);
  };

  return (
    <div
      id={`message-${message.id}`}
      onMouseLeave={() => {
        setShowActions(false);
        setShowEmojiPicker(false);
        setShowMoreMenu(false);
      }}
      className={`group relative flex gap-2.5 my-1 transition-all ${
        isOutgoing ? 'justify-end' : 'justify-start'
      }`}
    >
      {/* Incoming Avatar */}
      {!isOutgoing && (
        <div className="w-8 flex-shrink-0 flex items-end">
          {showAvatar && message.isAssistant ? (
            message.senderAvatar ? (
              <img
                src={message.senderAvatar}
                alt="Trợ lý thông minh"
                className="h-8 w-8 rounded-full object-cover ring-1 ring-violet-200 dark:ring-violet-400/30"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-violet-100 text-violet-600 ring-1 ring-violet-200 dark:bg-violet-500/20 dark:text-violet-300 dark:ring-violet-400/30" aria-label="Trợ lý thông minh">
                <Sparkles className="h-4 w-4" />
              </div>
            )
          ) : showAvatar && message.senderAvatar ? (
            isGroup && onStartDirectChat ? (
              <button
                type="button"
                onClick={() => onStartDirectChat(message.senderId)}
                aria-label={`Open direct chat with ${message.senderName || 'sender'}`}
                title={`Chat with ${message.senderName || 'sender'}`}
                className="rounded-full focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:ring-offset-2 dark:focus:ring-offset-[#14161C]"
              >
                <img
                  src={message.senderAvatar}
                  alt={message.senderName || 'Sender'}
                  className="w-8 h-8 rounded-full object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
                  referrerPolicy="no-referrer"
                />
              </button>
            ) : (
              <img
                src={message.senderAvatar}
                alt={message.senderName || 'Sender'}
                className="w-8 h-8 rounded-full object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
                referrerPolicy="no-referrer"
              />
            )
          ) : showAvatar ? (
            isGroup && onStartDirectChat ? (
              <button
                type="button"
                onClick={() => onStartDirectChat(message.senderId)}
                aria-label={`Open direct chat with ${message.senderName || 'sender'}`}
                title={`Chat with ${message.senderName || 'sender'}`}
                className="flex items-center justify-center w-8 h-8 rounded-full bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[10px] font-semibold text-[#2563EB] ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D] focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:ring-offset-2 dark:focus:ring-offset-[#14161C]"
              >
                {(message.senderName || "?").slice(0, 2).toUpperCase()}
              </button>
            ) : (
              <div className="flex items-center justify-center w-8 h-8 rounded-full bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[10px] font-semibold text-[#2563EB] ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]">
                {(message.senderName || "?").slice(0, 2).toUpperCase()}
              </div>
            )
          ) : (
            <div className="w-8" />
          )}
        </div>
      )}

      {/* Message Container */}
      <div
        className={`relative flex flex-col max-w-[85%] sm:max-w-[72%] md:max-w-[62%] ${
          isOutgoing ? 'items-end' : 'items-start'
        }`}
      >
        {/* Sender Name for Group Chats */}
        {isGroup && !isOutgoing && showSenderName && (
          <span className="text-[11px] font-bold text-[#1E2230] dark:text-[#F5F6FA] ml-2 mb-1">
            {message.senderName}
          </span>
        )}

        {/* An assistant answer inside a group is delivered only to whoever
            tagged it. Saying so matters: without the label this reads as a
            message the whole group can see, and someone will reply to it
            expecting the others to have the context. */}
        {message.isPrivate && (
          <span className="mb-1 ml-2 inline-flex items-center gap-1 text-[11px] font-medium text-violet-600 dark:text-violet-300">
            <Lock className="h-3 w-3" /> {interactionText(language, 'Only you can see this')}
          </span>
        )}

        {/* Reply Quote Preview */}
        {message.forwardedFromMessageId && (
          <span className="mb-1 ml-2 inline-flex items-center gap-1 text-[11px] font-medium text-[#74798C] dark:text-[#9DA3B4]">
            <RotateCw className="h-3 w-3" /> {interactionText(language, 'Forwarded')}
          </span>
        )}
        {message.replyTo && (
          <div
            className={`flex items-start gap-1.5 px-3 py-1.5 mb-1 rounded-xl text-xs max-w-full border-l-2 ${
              isOutgoing
                ? 'bg-[#1D4ED8]/45 border-white/70 text-white/75'
                : 'bg-[#F4F5F8] dark:bg-[#232630] border-[#2563EB] text-[#74798C] dark:text-[#9DA3B4] opacity-80'
            }`}
          >
            <Reply className="w-3 h-3 flex-shrink-0 mt-0.5" />
            <div className="min-w-0">
              <span className="font-semibold block truncate opacity-90">
                {message.replyTo.senderName}
              </span>
              <span className="truncate block opacity-75">
                {message.replyTo.content}
              </span>
            </div>
          </div>
        )}

        {/* Message Bubble Box */}
        <div
          onMouseEnter={() => setShowActions(true)}
          className={`relative overflow-hidden rounded-2xl text-sm leading-relaxed transition-colors select-text ${
            isAttachmentCaption
              ? 'bg-transparent p-0 shadow-none'
              : isOutgoing
                ? 'bg-[#DBEAFE] px-4 py-2.5 text-[#1E3A8A] rounded-br-sm shadow-sm dark:bg-[#1E40AF]/45 dark:text-[#DBEAFE]'
                : 'bg-white px-4 py-2.5 dark:bg-[#232630] text-[#1E2230] dark:text-[#F5F6FA] border border-[#E8EAF0] dark:border-[#2E3342] rounded-bl-sm shadow-sm'
          }`}
        >
          {/* Main message text */}
          <div className="space-y-1">
            {!isDeleted && message.attachments?.map((attachment) => (
              <MessageAttachmentCard
                key={attachment.id}
                attachment={attachment}
                language={language}
                onDownload={onDownloadAttachment}
                onLoadPreview={onLoadAttachmentPreview}
              />
            ))}
            {!isAttachmentCaption && shouldRenderText && (
              isEditingTranslation ? (
                <div className="space-y-1.5">
                  <textarea
                    aria-label="Edit translated text"
                    autoFocus
                    rows={2}
                    value={editedTranslation}
                    onChange={(event) => setEditedTranslation(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') setIsEditingTranslation(false);
                      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        saveTranslationEdit();
                      }
                    }}
                    className="w-full resize-none rounded-md border border-[#2563EB]/60 bg-white/70 px-2 py-1 text-sm leading-relaxed text-[#1E2230] outline-none focus:border-[#2563EB] dark:bg-[#1C1F27] dark:text-[#F5F6FA]"
                  />
                  <div className="flex justify-end gap-1">
                    <button type="button" aria-label="Cancel edit" title="Cancel" onClick={() => setIsEditingTranslation(false)} className="rounded p-1 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]"><X className="h-3.5 w-3.5" /></button>
                    <button type="button" aria-label="Save translation edit" title="Save" onClick={saveTranslationEdit} className="rounded p-1 text-[#2563EB] hover:bg-[#EFF6FF] dark:text-[#60A5FA] dark:hover:bg-[#2563EB]/20"><Check className="h-3.5 w-3.5" /></button>
                  </div>
                </div>
              ) : showTranslatedAsPrimary ? (
                <div className="flex items-start justify-between gap-2">
                  <p className="whitespace-pre-wrap break-words font-normal flex-1">{message.translation?.editedText || message.translation?.translatedText}</p>
                  {onSpeak && (
                    <button
                      type="button"
                      aria-label="Speak text"
                      title="Speak text"
                      onClick={() => isSpeaking && onStopSpeak ? onStopSpeak() : onSpeak(message.id, message.translation!.targetLanguage, 'translation')}
                      className="mt-0.5 text-[#74798C] hover:text-[#2563EB] transition-colors"
                    >
                      {isSpeaking ? <VolumeX className="h-4 w-4 text-[#2563EB] animate-pulse" /> : <Volume2 className="h-4 w-4" />}
                    </button>
                  )}
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <p className="whitespace-pre-wrap break-words font-normal flex-1">{renderMessageContent(message.content)}</p>
                  {onSpeak && (
                    <button
                      type="button"
                      aria-label="Speak text"
                      title="Speak text"
                      onClick={() => isSpeaking && onStopSpeak ? onStopSpeak() : onSpeak(message.id, message.translation?.originalLanguage || language, 'original')}
                      className="mt-0.5 text-[#74798C] hover:text-[#2563EB] transition-colors"
                    >
                      {isSpeaking ? <VolumeX className="h-4 w-4 text-[#2563EB] animate-pulse" /> : <Volume2 className="h-4 w-4" />}
                    </button>
                  )}
                </div>
              )
            )}

            {!isDeleted && isVoice && message.transcriptionStatus === 'pending' && (
              <div className="flex items-center gap-2 py-1 text-xs text-[#74798C] dark:text-[#9DA3B4]" aria-live="polite">
                <div className="flex gap-1" aria-hidden="true">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#2563EB]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#2563EB] [animation-delay:0.2s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#2563EB] [animation-delay:0.4s]" />
                </div>
                <span className="italic">{interactionText(language, 'Transcribing…')}</span>
              </div>
            )}

            {!isDeleted && isVoice && message.transcriptionStatus === 'failed' && (
              <div className="flex flex-wrap items-center gap-2 py-1 text-xs text-amber-600 dark:text-amber-400" role="status">
                <AlertCircle className="h-3.5 w-3.5" />
                <span>{interactionText(language, 'Transcription unavailable')}</span>
                <button
                  type="button"
                  onClick={() => onRetryTranscription(message.id)}
                  disabled={isRetryingTranscription}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-semibold text-[#2563EB] hover:bg-[#EFF6FF] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#60A5FA] dark:hover:bg-[#2563EB]/20"
                >
                  <RotateCw className={`h-3 w-3 ${isRetryingTranscription ? 'animate-spin' : ''}`} />
                  {interactionText(
                    language,
                    isRetryingTranscription ? 'Retrying transcription…' : 'Retry transcription',
                  )}
                </button>
              </div>
            )}

            {/* Translating Pending State */}
            {isTranslating && (
              <div className="flex items-center gap-1.5 py-1 text-xs text-[#74798C] dark:text-[#9DA3B4]">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce" />
                  <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce [animation-delay:0.2s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-bounce [animation-delay:0.4s]" />
                </div>
                <span className="italic">Translating…</span>
              </div>
            )}

            {/* Translation Failure State */}
            {isTranslationFailed && (
              <div className="flex items-center gap-2 pt-1 text-xs text-amber-600 dark:text-amber-400">
                <AlertCircle className="w-3.5 h-3.5" />
                <span>Translation unavailable</span>
                <button
                  onClick={() => onRetryTranslation(message.id)}
                  className="underline font-semibold hover:text-amber-700"
                >
                  Retry
                </button>

              </div>
            )}
          </div>

          {canReviewTranslation && message.translation && (
            <div className="mt-1.5 flex items-center gap-0.5 text-[#74798C] dark:text-[#9DA3B4]">
                <button
                  type="button"
                  onClick={() => onToggleOriginal(message.id)}
                  className="mr-1 inline-flex rounded-md p-1 text-[11px] font-medium text-[#2563EB] hover:bg-[#EFF6FF] dark:text-[#60A5FA] dark:hover:bg-[#2563EB]/20"
                >
                  <span>{interactionText(language, isShowingOriginal ? 'Show translation' : 'Show original')}</span>
                </button>
                <button
                  type="button"
                  aria-label="Like translation"
                  title="Like translation"
                  aria-pressed={message.translation.rating === 5}
                  onClick={() => onRateTranslation(message.id, message.translation!.translationId, 5)}
                  className={`inline-flex rounded-md p-1 transition-colors ${message.translation.rating === 5 ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300' : 'hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]'}`}
                >
                  <ThumbsUp className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  aria-label="Dislike translation"
                  title="Dislike translation"
                  aria-pressed={message.translation.rating === 1}
                  onClick={() => onRateTranslation(message.id, message.translation!.translationId, 1)}
                  className={`inline-flex rounded-md p-1 transition-colors ${message.translation.rating === 1 ? 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300' : 'hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]'}`}
                >
                  <ThumbsDown className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  aria-label="Edit translation"
                  title="Edit translation"
                  onClick={beginTranslationEdit}
                  className="inline-flex rounded-md p-1 text-[#2563EB] hover:bg-[#EFF6FF] dark:text-[#60A5FA] dark:hover:bg-[#2563EB]/20"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
            </div>
          )}

          {footer}
        </div>

        {/* Timestamp & Status info */}
        <div
          className={`flex items-center gap-1.5 mt-1 px-1 text-[11px] text-[#8A8F9E] dark:text-[#74798C] select-none ${
            isOutgoing ? 'justify-end' : 'justify-start'
          }`}
        >
          <span>{message.timestamp}</span>

          {isOutgoing && (
            <span className="flex items-center">
              {message.status === 'sending' && (
                <span className="italic text-[10px]">Sending...</span>
              )}
              {message.status === 'sent' && <Check className="w-3.5 h-3.5" />}
              {message.status === 'delivered' && (
                <CheckCheck className="w-3.5 h-3.5 text-[#8A8F9E]" />
              )}
              {message.status === 'read' && (
                <CheckCheck className="w-3.5 h-3.5 text-[#2563EB]" />
              )}
            </span>
          )}
        </div>

        {/* Reaction Badges */}
        {message.reactions && message.reactions.length > 0 && (
          <div
            className={`flex flex-wrap gap-1 mt-0.5 ${
              isOutgoing ? 'justify-end' : 'justify-start'
            }`}
          >
            {message.reactions.map((reaction, idx) => {
              const hasReacted = reaction.users.includes(currentUser.id);
              return (
                <button
                  key={idx}
                  onClick={() => onReact(message.id, reaction.emoji)}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition-all ${
                    hasReacted
                      ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/25 text-[#2563EB] border border-[#2563EB]/30 font-semibold scale-105'
                      : 'bg-white dark:bg-[#232630] text-[#1E2230] dark:text-[#E2E5F0] border border-[#E8EAF0] dark:border-[#2E3342] hover:bg-[#F7F8FC]'
                  } shadow-2xs`}
                >
                  <span>{reaction.emoji}</span>
                  <span className="text-[10px] font-medium">{reaction.count}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Hover on desktop; tap/click remains available for touch devices. */}
      {showActions && (
        <div
          className={`absolute left-1/2 translate-y-1 flex items-center gap-0.5 rounded-xl border border-[#E8EAF0] bg-white p-1 shadow-lg transition-all z-20 dark:border-[#2E3342] dark:bg-[#232630] ${
            isOutgoing ? '-top-9' : 'top-0'
          }`}
        >
          {/* Reaction Picker Button */}
          <div className="relative">
            <button
              onClick={() => setShowEmojiPicker(!showEmojiPicker)}
              aria-label="Add reaction"
              className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] transition-colors"
            >
              <Smile className="w-3.5 h-3.5" />
            </button>

            {/* Quick Emoji Bar */}
            {showEmojiPicker && (
              <div className="absolute left-0 bottom-full mb-1 flex items-center gap-1 p-1 bg-white dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2E3342] rounded-full shadow-xl z-30">
                {QUICK_EMOJIS.map((emoji) => (
                  <button
                    key={emoji}
                    onClick={() => {
                      onReact(message.id, emoji);
                      setShowEmojiPicker(false);
                    }}
                    className="w-7 h-7 flex items-center justify-center hover:scale-125 text-base transition-transform"
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Reply Button */}
          <button
            onClick={() => onReply(message)}
            aria-label="Reply"
            className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] transition-colors"
          >
            <Reply className="w-3.5 h-3.5" />
          </button>

          {/* Copy Button */}
          <button
            onClick={() =>
              onCopy(
                isOutgoing || message.translation?.showOriginal
                  ? message.content
                  : message.translation?.translatedText || message.content
              )
            }
            aria-label="Copy text"
            className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] transition-colors"
          >
            <Copy className="w-3.5 h-3.5" />
          </button>


          {isOutgoing && onDeleteMessage && (
            <button
              onClick={() => {
                if (window.confirm("Delete this message?")) onDeleteMessage(message.id);
              }}
              aria-label="Delete message"
              title="Delete message"
              className="p-1 rounded-lg text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}

          {/* More Menu Dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowMoreMenu(!showMoreMenu)}
              aria-label="More actions"
              className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] transition-colors"
            >
              <MoreHorizontal className="w-3.5 h-3.5" />
            </button>

            {showMoreMenu && (
              <div className="absolute right-0 top-full mt-1 w-36 bg-white dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2E3342] rounded-xl shadow-xl p-1 z-30 text-xs">
                <button
                  onClick={() => { onForward(message); setShowMoreMenu(false); }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[#1E2230] dark:text-[#E2E5F0] hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <RotateCw className="w-3 h-3 text-[#2563EB]" />
                  <span>{interactionText(language, 'Forward')}</span>
                </button>
                <button
                  onClick={() => {
                    onRetryTranslation(message.id);
                    setShowMoreMenu(false);
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[#1E2230] dark:text-[#E2E5F0] hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <RotateCw className="w-3 h-3 text-[#2563EB]" />
                  <span>{interactionText(language, 'Translate again')}</span>
                </button>

                <button
                  onClick={() => {
                    onSaveMessage(message.id);
                    setShowMoreMenu(false);
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[#1E2230] dark:text-[#E2E5F0] hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <Bookmark className="w-3 h-3 text-[#74798C]" />
                  <span>{interactionText(language, message.isSaved ? 'Unsave message' : 'Save message')}</span>
                </button>

              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
};
