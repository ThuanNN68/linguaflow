import React from 'react';
import { Conversation, User } from '../types';
import { tx } from '../i18n';
import {
  Search,
  Phone,
  Video,
  Info,
  ChevronLeft,
  UsersRound,
  Globe
} from 'lucide-react';

interface ChatHeaderProps {
  conversation: Conversation;
  onBack?: () => void;
  onToggleDetails: () => void;
  isDetailsOpen: boolean;
  onStartCall: (type: 'voice' | 'video') => void;
  onSearchInChat: () => void;
  language: User['nativeLanguage'];
  assistantMode?: boolean;
}

export const ChatHeader: React.FC<ChatHeaderProps> = ({
  conversation,
  onBack,
  onToggleDetails,
  isDetailsOpen,
  onStartCall,
  onSearchInChat,
  language,
  assistantMode = false,
}) => {
  const isGroup = conversation.type === 'group' && !assistantMode;

  return (
    <header
      id="active-chat-header"
      className="flex items-center justify-between w-full h-[68px] px-4 md:px-6 bg-white/95 dark:bg-[#1C1F27]/95 backdrop-blur border-b border-[#E8EAF0] dark:border-[#232630] z-10 select-none flex-shrink-0 transition-colors"
    >
      {/* Left: Back Button (Mobile) + Avatar + Recipient Info */}
      <div className="flex items-center gap-3 min-w-0">
        {onBack && (
          <button
            id="chat-header-mobile-back"
            onClick={onBack}
            aria-label="Back to conversations"
            className="md:hidden p-1.5 -ml-1 text-[#74798C] hover:text-[#1E2230] dark:hover:text-white rounded-lg transition-colors"
          >
            <ChevronLeft className="w-6 h-6" />
          </button>
        )}

        {/* Avatar with Status */}
        <div className={`relative flex-shrink-0 ${assistantMode ? '' : 'cursor-pointer'}`} onClick={assistantMode ? undefined : onToggleDetails}>
          <img
            src={conversation.avatar}
            alt={conversation.name}
            className="w-10 h-10 rounded-full object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
            referrerPolicy="no-referrer"
          />
          {isGroup ? (
            <span className="absolute -bottom-0.5 -right-0.5 flex items-center justify-center w-4 h-4 bg-[#2563EB] text-white rounded-full ring-2 ring-white dark:ring-[#1C1F27]">
              <UsersRound className="w-2.5 h-2.5" />
            </span>
          ) : conversation.isOnline ? (
            <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
          ) : null}
        </div>

        {/* Name and Metadata */}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA] truncate">
              {conversation.name}
            </h2>

            {/* Language or Group pill */}
            {conversation.recipient && (
              <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium bg-[#2563EB]/10 text-[#2563EB] dark:bg-[#2563EB]/20 rounded-md">
                <Globe className="w-3 h-3" />
                {conversation.recipient.nativeLanguage.toUpperCase()}
              </span>
            )}
          </div>

          <div className="flex items-center gap-1 text-xs text-[#74798C] dark:text-[#9DA3B4]">
            {assistantMode ? (
              <span>Không gian riêng tư của bạn</span>
            ) : isGroup ? (
              <span>{conversation.memberCount || 8} {tx(language, 'Members').toLowerCase()}</span>
            ) : conversation.isTyping ? (
              <span className="text-[#2563EB] font-medium animate-pulse">
                typing...
              </span>
            ) : conversation.isOnline ? (
              <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                {tx(language, 'Online')}
              </span>
            ) : (
              <span>{conversation.recipient?.lastSeen || tx(language, 'Last seen recently')}</span>
            )}
          </div>
        </div>
      </div>

      {/* Right: Actions (Search, Call, Video, Info) */}
      {!assistantMode && <div className="flex items-center gap-1 md:gap-1.5">
        <button
          id="chat-action-search"
          onClick={onSearchInChat}
          aria-label="Search in conversation"
          className="p-2 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-[#F5F6FA] hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
        >
          <Search className="w-5 h-5" />
        </button>

        {!isGroup && <button
          id="chat-action-voice-call"
          onClick={() => onStartCall('voice')}
          aria-label="Voice Call"
          className="p-2 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-[#F5F6FA] hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
        >
          <Phone className="w-5 h-5" />
        </button>}

        {!isGroup && <button
          id="chat-action-video-call"
          onClick={() => onStartCall('video')}
          aria-label="Video Call"
          className="p-2 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-[#F5F6FA] hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
        >
          <Video className="w-5 h-5" />
        </button>}

        <button
          id="chat-action-info-toggle"
          onClick={onToggleDetails}
          aria-label="Conversation Info"
          className={`p-2 rounded-xl transition-colors ${
            isDetailsOpen
              ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/25 text-[#2563EB]'
              : 'text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-[#F5F6FA] hover:bg-[#F7F8FC] dark:hover:bg-[#232630]'
          }`}
        >
          <Info className="w-5 h-5" />
        </button>
      </div>}
    </header>
  );
};
