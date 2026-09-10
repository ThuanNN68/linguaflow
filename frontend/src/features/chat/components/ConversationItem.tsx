import React, { useEffect, useState } from 'react';
import { Conversation, LanguageCode } from '../types';
import { formatRelativeMessageTime } from '../api/chat-api';
import { UsersRound, VolumeX, Pin } from 'lucide-react';

interface ConversationItemProps {
  conversation: Conversation;
  isSelected: boolean;
  onSelect: (conversation: Conversation) => void;
  language: LanguageCode;
}

export const ConversationItem: React.FC<ConversationItemProps> = ({
  conversation,
  isSelected,
  onSelect,
  language,
}) => {
  const isGroup = conversation.type === 'group';
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(interval);
  }, []);

  const lastMessageTime = conversation.lastMessageAt
    ? formatRelativeMessageTime(conversation.lastMessageAt, language, now)
    : conversation.lastMessageTime;

  return (
    <div
      id={`conversation-item-${conversation.id}`}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(conversation)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          onSelect(conversation);
        }
      }}
      className={`group relative flex items-center gap-3 px-3.5 py-3 rounded-xl cursor-pointer transition-all duration-150 select-none ${
        isSelected
          ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/15 text-[#1E2230] dark:text-[#F5F6FA]'
          : 'hover:bg-[#F7F8FC] dark:hover:bg-[#232630]/70 text-[#1E2230] dark:text-[#E2E5F0]'
      }`}
    >
      {/* Selected Indicator on Left Edge */}
      {isSelected && (
        <span className="absolute left-0 top-3 bottom-3 w-1 bg-[#2563EB] rounded-r-full" />
      )}

      {/* Avatar with Status Badge */}
      <div className="relative flex-shrink-0">
        <img
          src={conversation.avatar}
          alt={conversation.name}
          className="w-12 h-12 rounded-full object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
          referrerPolicy="no-referrer"
        />

        {/* Group Icon Badge or Online Status Dot */}
        {isGroup ? (
          <span className="absolute -bottom-0.5 -right-0.5 flex items-center justify-center w-5 h-5 bg-[#2563EB] text-white rounded-full ring-2 ring-white dark:ring-[#1C1F27]">
            <UsersRound className="w-2.5 h-2.5" />
          </span>
        ) : conversation.isOnline ? (
          <span className="absolute bottom-0 right-0 w-3 h-3 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
        ) : (
          <span className="absolute bottom-0 right-0 w-3 h-3 bg-[#A0A5B5] rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
        )}
      </div>

      {/* Center Details */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <h4
              className={`text-sm truncate ${
                conversation.unreadCount > 0 ? 'font-bold text-[#1E2230] dark:text-[#F5F6FA]' : 'font-semibold text-[#1E2230] dark:text-[#E2E5F0]'
              }`}
            >
              {conversation.name}
            </h4>
            {conversation.isPinned && (
              <Pin className="w-3 h-3 text-[#74798C] flex-shrink-0 fill-current" />
            )}
          </div>
          <span
            className={`text-xs whitespace-nowrap flex-shrink-0 ${
              conversation.unreadCount > 0
                ? 'text-[#2563EB] font-semibold'
                : 'text-[#8A8F9E] dark:text-[#74798C]'
            }`}
          >
            {lastMessageTime}
          </span>
        </div>

        <div className="flex items-center justify-between gap-2">
          {/* Message snippet or typing state */}
          {conversation.isTyping ? (
            <p className="text-xs text-[#2563EB] font-medium italic truncate flex items-center gap-1">
              <span>typing...</span>
            </p>
          ) : (
            <p
              className={`text-xs truncate ${
                conversation.unreadCount > 0
                  ? 'text-[#1E2230] dark:text-[#F5F6FA] font-medium'
                  : 'text-[#74798C] dark:text-[#9DA3B4]'
              }`}
            >
              {conversation.lastMessage}
            </p>
          )}

          {/* Badges / Mute */}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {conversation.isMuted && (
              <VolumeX className="w-3.5 h-3.5 text-[#A0A5B5]" />
            )}
            {conversation.unreadCount > 0 && (
              <span className="flex items-center justify-center min-w-[20px] h-5 px-1.5 text-[11px] font-bold text-white bg-[#2563EB] rounded-full">
                {conversation.unreadCount}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
