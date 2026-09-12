import React, { useState } from 'react';
import { Conversation, Message, User, MessageMention, MessageReply, MessageAttachment } from '../types';
import { ChatHeader } from './ChatHeader';
import { MessageList } from './MessageList';
import { MessageComposer } from './MessageComposer';
import { ConversationDetailsDrawer } from './ConversationDetailsDrawer';
import { MessageSearchPanel, type ConversationSearchResult } from './MessageSearchPanel';
import { FullEventModal, type CalendarTask } from './PersonalCalendar';
import { CalendarCheck, ChevronRight, MessageSquare, Sparkles, Plus, Globe, X } from 'lucide-react';
import { conversationSummaryText, emptyChatText } from '../i18n';
import { formatProposalWhen, toLocalInputValue } from '../proposal-approval';
import type { VoiceRecorderStage } from '../voice-recorder';
import type { ApiActionProposal } from '../api/chat-api';

interface ChatViewProps {
  conversation: Conversation | null;
  messages: Message[];
  currentUser: User;
  onBack?: () => void;
  onSendMessage: (text: string, replyToMessageId?: string, mentions?: MessageMention[]) => void;
  onSendAttachment?: (file: File) => void;
  onSendVoice?: (
    file: File,
    replyToMessageId: string | undefined,
    onStage: (stage: Extract<VoiceRecorderStage, 'uploading' | 'sending'>) => void,
  ) => Promise<void>;
  onTyping?: (isTyping: boolean) => void;
  onReact: (messageId: string, emoji: string) => void;
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
  onToggleMute: (conversationId: string) => void;
  onTogglePin: (conversationId: string) => void;
  onBlockContact: (conversationId: string) => void;
  onSearchMessages: (query: string) => Promise<ConversationSearchResult[]>;
  onOpenNewChat: () => void;
  /** Proposals raised in this conversation that nobody has decided on yet. */
  pendingProposals?: ApiActionProposal[];
  proposalBusyId?: string | null;
  onApproveProposal?: (proposal: ApiActionProposal, corrections: Record<string, unknown>) => void;
  onRejectProposal?: (proposal: ApiActionProposal) => void;
  onStartCall: (type: 'voice' | 'video') => void;
  language: User['nativeLanguage'];
  attachments: MessageAttachment[];
  onDownloadAttachment: (attachment: MessageAttachment) => void;
  onLoadAttachmentPreview: (attachment: MessageAttachment) => Promise<string>;
  onLeaveGroup?: (conversationId: string) => void;
  onDeleteGroup?: (conversationId: string) => void;
  availableUsers: User[];
  onAddMembers?: (conversationId: string, userIds: string[]) => void;
  onRemoveMember?: (conversationId: string, userId: string) => void;
  onChangeMemberRole?: (conversationId: string, userId: string, role: 'admin' | 'member') => void;
  onSearchUsers?: (query: string) => void;
  onTransferOwnership?: (conversationId: string, userId: string) => void;
  onUpdateGroup?: (conversationId: string, title: string, description: string) => void;
  onStartDirectChat?: (userId: string) => void;
  assistantMode?: boolean;
  calendarToken?: string;
  onCreateAppointment?: (task: CalendarTask) => void;
  onScanAppointments?: (conversationId: string, amount: number, unit: 'hours' | 'days' | 'weeks') => Promise<ApiActionProposal[]>;
}

export const ChatView: React.FC<ChatViewProps> = ({
  conversation,
  messages,
  currentUser,
  onBack,
  onSendMessage,
  onSendAttachment,
  onSendVoice,
  onTyping,
  onReact,
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
  onToggleMute,
  onTogglePin,
  onBlockContact,
  onSearchMessages,
  onOpenNewChat,
  pendingProposals = [],
  onApproveProposal,
  onStartCall,
  language,
  attachments,
  onDownloadAttachment,
  onLoadAttachmentPreview,
  onLeaveGroup,
  onDeleteGroup,
  availableUsers,
  onAddMembers,
  onRemoveMember,
  onChangeMemberRole,
  onSearchUsers,
  onTransferOwnership,
  onUpdateGroup,
  onStartDirectChat,
  assistantMode = false,
  onCreateAppointment,
  onScanAppointments,
  calendarToken,
}) => {
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [replyTo, setReplyTo] = useState<MessageReply | null>(null);
  const [isCreatingAppointment, setIsCreatingAppointment] = useState(false);
  const [reviewingProposal, setReviewingProposal] = useState<ApiActionProposal | null>(null);
  const [proposalList, setProposalList] = useState<ApiActionProposal[]>([]);

  // If no conversation is active, render the clean centered empty state
  if (!conversation) {
    const [emptyTitle, emptyDescription, startConversation] = emptyChatText(language);
    return (
      <div
        id="empty-chat-state"
        className="flex-1 flex flex-col items-center justify-center h-screen bg-[#F7F8FC] dark:bg-[#14161C] px-6 text-center select-none"
      >
        <div className="flex items-center justify-center w-16 h-16 rounded-3xl bg-gradient-to-tr from-[#2563EB] to-[#60A5FA] text-white shadow-lg shadow-[#2563EB]/20 mb-4">
          <MessageSquare className="w-8 h-8" />
        </div>

        <h2 className="text-xl font-bold text-[#1E2230] dark:text-[#F5F6FA] mb-1.5">
          {emptyTitle}
        </h2>
        <p className="text-sm text-[#74798C] dark:text-[#9DA3B4] max-w-sm mb-6 leading-relaxed">
          {emptyDescription}
        </p>

        <button
          onClick={onOpenNewChat}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#2563EB] text-white font-semibold text-sm hover:bg-[#1D4ED8] shadow-md shadow-[#2563EB]/25 hover:scale-105 active:scale-95 transition-all"
        >
          <Plus className="w-4 h-4" />
          <span>{startConversation}</span>
        </button>
      </div>
    );
  }

  const handleReply = (message: Message) => {
    setReplyTo({
      id: message.id,
      senderName: message.senderName || 'Sender',
      content: message.senderId === currentUser.id || message.translation?.showOriginal
        ? message.content
        : message.translation?.translatedText || message.content,
    });
  };

  return (
    <div className="flex-1 flex h-screen min-w-0 overflow-hidden bg-[#F7F8FC] dark:bg-[#14161C]">
      {/* Main Active Chat Column - Stretches to fill entire available width */}
      <main
        id="active-chat-panel"
        className="relative flex-1 flex flex-col h-screen min-w-0 bg-[#F7F8FC] dark:bg-[#14161C] transition-colors"
      >
        {/* Full-width Chat Header */}
        <ChatHeader
          conversation={conversation}
          onBack={onBack}
          onToggleDetails={() => setIsDetailsOpen(!isDetailsOpen)}
          isDetailsOpen={isDetailsOpen}
          onStartCall={onStartCall}
          onSearchInChat={() => setIsSearchOpen(true)}
          language={language}
          assistantMode={assistantMode}
        />

        {isSearchOpen && (
          <MessageSearchPanel
            onClose={() => setIsSearchOpen(false)}
            onSearch={onSearchMessages}
            onSelect={(messageId) => {
              document.getElementById(`message-${messageId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              setIsSearchOpen(false);
            }}
          />
        )}

        {/* Full-width Messages Container */}
        <MessageList
          calendarToken={calendarToken}
          messages={messages}
          currentUser={currentUser}
          conversation={conversation}
          onReact={onReact}
          onReply={handleReply}
          onCopy={onCopy}
          onSpeak={onSpeak}
          onStopSpeak={onStopSpeak}
          speakingMessageId={speakingMessageId}
          onToggleOriginal={onToggleOriginal}
          onRetryTranslation={onRetryTranslation}
          onRetryTranscription={onRetryTranscription}
          retryingTranscriptionIds={retryingTranscriptionIds}
          onRateTranslation={onRateTranslation}
          onEditTranslation={onEditTranslation}
          onForward={onForward}
          onSaveMessage={onSaveMessage}
          onDeleteMessage={onDeleteMessage}
          onDownloadAttachment={onDownloadAttachment}
          onLoadAttachmentPreview={onLoadAttachmentPreview}
          onStartDirectChat={onStartDirectChat}
          pendingProposals={pendingProposals}
          onReviewProposals={(proposals) => {
            if (proposals.length === 1) setReviewingProposal(proposals[0]);
            else setProposalList(proposals);
          }}
          language={language}
        />

        {/* Full-width Composer */}
        <MessageComposer
          recipientName={conversation.name}
          onSendMessage={onSendMessage}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
          onSendAttachment={onSendAttachment}
          onSendVoice={onSendVoice}
          onTyping={onTyping}
          mentionCandidates={assistantMode ? [] : conversation.type === 'group'
            ? (conversation.members || []).filter((member) => member.id !== currentUser.id)
            : (conversation.recipient ? [conversation.recipient] : [])}
          language={language}
          onCreateAppointment={onCreateAppointment ? () => setIsCreatingAppointment(true) : undefined}
          assistantMode={assistantMode}
          onScanAppointments={onScanAppointments && conversation ? async (amount, unit) => {
            const proposals = await onScanAppointments(conversation.id, amount, unit);
            const pending = proposals.filter((proposal) =>
              proposal.status === 'pending_confirmation' || proposal.status === 'needs_clarification');
            if (pending.length === 1) setReviewingProposal(pending[0]);
            else if (pending.length > 1) setProposalList(pending);
          } : undefined}
          onSummarizeConversation={conversation ? () => {
            onSendMessage(conversationSummaryText(language).prompt, undefined, [{ type: 'assistant' }]);
          } : undefined}
        />
      </main>


      {isCreatingAppointment && calendarToken && (
        <FullEventModal
          token={calendarToken}
          initialKind="event"
          onClose={() => setIsCreatingAppointment(false)}
          onSubmit={(task) => {
            onCreateAppointment?.(task);
            setIsCreatingAppointment(false);
          }}
        />
      )}

      {proposalList.length > 1 && (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center bg-black/35 p-4 backdrop-blur-[2px]"
          role="dialog"
          aria-modal="true"
          aria-labelledby="proposal-list-title"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setProposalList([]);
          }}
        >
          <div className="w-full max-w-md overflow-hidden rounded-2xl border border-[#E8EAF0] bg-white shadow-2xl dark:border-[#363B49] dark:bg-[#1C1F27]">
            <div className="flex items-center justify-between border-b border-[#E8EAF0] px-5 py-4 dark:border-[#363B49]">
              <div>
                <h3 id="proposal-list-title" className="text-sm font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                  {proposalList.length} đề xuất đang chờ duyệt
                </h3>
                <p className="mt-0.5 text-xs text-[#74798C] dark:text-[#9DA3B4]">Chọn một đề xuất để xem chi tiết</p>
              </div>
              <button
                type="button"
                onClick={() => setProposalList([])}
                aria-label="Đóng danh sách đề xuất"
                className="rounded-full p-2 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="max-h-[60vh] space-y-2 overflow-y-auto p-3">
              {proposalList.map((proposal) => (
                <button
                  key={proposal.id}
                  type="button"
                  onClick={() => {
                    setProposalList([]);
                    setReviewingProposal(proposal);
                  }}
                  className="group flex w-full items-center gap-3 rounded-xl border border-[#E8EAF0] p-3 text-left hover:border-[#BFDBFE] hover:bg-[#F7FAFF] dark:border-[#363B49] dark:hover:border-[#2563EB]/50 dark:hover:bg-[#2563EB]/10"
                >
                  <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/20 dark:text-[#93C5FD]">
                    <CalendarCheck className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">
                      {proposal.title}
                    </span>
                    <span className="mt-1 block truncate text-[11px] text-[#74798C] dark:text-[#9DA3B4]">
                      {formatProposalWhen(proposal)}
                    </span>
                  </span>
                  <ChevronRight className="h-4 w-4 flex-none text-[#9DA3B4] group-hover:text-[#2563EB]" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {reviewingProposal && calendarToken && onApproveProposal && (
        <FullEventModal
          token={calendarToken}
          initialKind={reviewingProposal.action_type === 'appointment' ? 'event' : 'task'}
          initialTitle={reviewingProposal.title}
          initialDate={toLocalInputValue(reviewingProposal.scheduled_start_at || reviewingProposal.due_at).slice(0, 10) || undefined}
          initialStartTime={toLocalInputValue(reviewingProposal.scheduled_start_at || reviewingProposal.due_at).slice(11) || undefined}
          initialEndTime={toLocalInputValue(reviewingProposal.scheduled_end_at).slice(11) || undefined}
          initialLocation={reviewingProposal.location ?? ''}
          initialNote={reviewingProposal.details ?? ''}
          submitLabel="Duyệt"
          reviewWarning={reviewingProposal.status === 'needs_clarification'
            ? 'Chưa có đủ ngày hoặc giờ họp. Bạn có thể điều chỉnh trước khi duyệt.'
            : !reviewingProposal.scheduled_start_at && !reviewingProposal.due_at
              ? 'Chưa có thời gian cụ thể. Vui lòng chọn ngày và giờ trước khi tạo lịch.'
              : undefined}
          onClose={() => setReviewingProposal(null)}
          onSubmit={(task) => {
            const reminder = task.reminders?.find((item) => item.method === 'popup');
            onApproveProposal(reviewingProposal, {
              title: task.title,
              details: task.note ?? null,
              location: task.location ?? null,
              scheduled_start_at: task.dueAt,
              scheduled_end_at: task.endAt ?? null,
              resolved_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
              reminder_minutes_before: reminder?.minutes ?? null,
            });
            setReviewingProposal(null);
          }}
        />
      )}

      {/* Temporary Conversation Details Drawer */}
      {!assistantMode && <ConversationDetailsDrawer
        conversation={conversation}
        isOpen={isDetailsOpen}
        onClose={() => setIsDetailsOpen(false)}
        onToggleMute={onToggleMute}
        onTogglePin={onTogglePin}
        onLeaveGroup={onLeaveGroup}
        onBlockContact={onBlockContact}
        language={language}
        attachments={attachments}
        onDownloadAttachment={onDownloadAttachment}
        calendarToken={calendarToken}
        onDeleteGroup={onDeleteGroup}
        currentUserId={currentUser.id}
        availableUsers={availableUsers}
        onAddMembers={onAddMembers}
        onRemoveMember={onRemoveMember}
        onChangeMemberRole={onChangeMemberRole}
        onSearchUsers={onSearchUsers}
        onTransferOwnership={onTransferOwnership}
        onUpdateGroup={onUpdateGroup}
        onStartDirectChat={onStartDirectChat}
      />}
    </div>
  );
};
