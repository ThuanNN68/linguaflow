import React, { useState, useMemo } from 'react';
import { Conversation, LanguageCode } from '../types';
import { ASSISTANT_AVATAR_URL } from '../api/chat-api';
import { assistantName } from '../i18n';
import { t } from '../i18n';
import { ConversationItem } from './ConversationItem';
import {
  Search,
  SquarePen,
  MoreHorizontal,
  X,
  CheckCheck,
  BellOff,
  Filter
} from 'lucide-react';

interface ConversationPanelProps {
  conversations: Conversation[];
  selectedConversationId: string | null;
  onSelectConversation: (conversation: Conversation) => void;
  onOpenNewChat: () => void;
  onMarkAllAsRead: () => void;
  assistantSelected?: boolean;
  onOpenAssistant?: () => void;
  assistantLastMessage?: string;
  assistantLastMessageTime?: string;
  language: LanguageCode;
}

type FilterType = 'all' | 'unread' | 'groups';

export const ConversationPanel: React.FC<ConversationPanelProps> = ({
  conversations,
  selectedConversationId,
  onSelectConversation,
  onOpenNewChat,
  onMarkAllAsRead,
  assistantSelected = false,
  onOpenAssistant,
  assistantLastMessage = 'Chào bạn! Tôi có thể hỗ trợ gì?',
  assistantLastMessageTime = 'Bây giờ',
  language,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');
  const [menuOpen, setMenuOpen] = useState(false);

  // Filter and search logic
  const filteredConversations = useMemo(() => {
    return conversations.filter((conv) => {
      // Filter tab check
      if (activeFilter === 'unread' && conv.unreadCount === 0) return false;
      if (activeFilter === 'groups' && conv.type !== 'group') return false;

      // Search query check
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const matchesName = conv.name.toLowerCase().includes(query);
        const matchesLastMsg = conv.lastMessage.toLowerCase().includes(query);
        const matchesUsername = conv.recipient?.username.toLowerCase().includes(query);
        return matchesName || matchesLastMsg || matchesUsername;
      }
      return true;
    });
  }, [conversations, activeFilter, searchQuery]);

  const totalUnreadCount = useMemo(() => {
    return conversations.reduce((acc, curr) => acc + curr.unreadCount, 0);
  }, [conversations]);

  return (
    <section
      id="conversation-list-panel"
      className="flex flex-col w-full md:w-[340px] md:min-w-[340px] md:max-w-[340px] h-screen bg-white dark:bg-[#1C1F27] border-r border-[#E8EAF0] dark:border-[#232630] z-20 flex-shrink-0 transition-colors"
      aria-label="Conversations Panel"
    >
      {/* Header: Title & Actions */}
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-bold text-[#1E2230] dark:text-[#F5F6FA] tracking-tight">
            {t(language, 'chats')}
          </h1>
          {totalUnreadCount > 0 && (
            <span className="px-2 py-0.5 text-xs font-bold text-[#2563EB] bg-[#EFF6FF] dark:bg-[#2563EB]/20 rounded-full">
              {totalUnreadCount} {t(language, 'new')}
            </span>
          )}
        </div>

        <div className="relative flex items-center gap-1">
          {/* More options menu */}
          <button
            id="chats-more-options-btn"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="More Chat Options"
            className="p-2 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-[#F5F6FA] hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
          >
            <MoreHorizontal className="w-5 h-5" />
          </button>

          {menuOpen && (
            <div
              className="absolute right-0 top-10 w-48 bg-white dark:bg-[#232630] rounded-xl shadow-xl border border-[#E8EAF0] dark:border-[#2A2E3D] p-1.5 z-50 animate-in fade-in zoom-in-95 duration-100"
            >
              <button
                onClick={() => {
                  onMarkAllAsRead();
                  setMenuOpen(false);
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left transition-colors"
              >
                <CheckCheck className="w-4 h-4 text-[#2563EB]" />
                <span>{t(language, 'markRead')}</span>
              </button>
              <button
                onClick={() => setMenuOpen(false)}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left transition-colors"
              >
                <BellOff className="w-4 h-4 text-[#74798C]" />
                <span>{t(language, 'mute')}</span>
              </button>
            </div>
          )}

          {/* New Conversation / Compose Button */}
          <button
            id="chats-compose-btn"
            onClick={onOpenNewChat}
            aria-label="New Conversation"
            className="p-2 rounded-xl bg-[#2563EB] text-white hover:bg-[#1D4ED8] shadow-sm hover:scale-105 active:scale-95 transition-all"
          >
            <SquarePen className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Search Bar */}
      <div className="px-4 py-1.5">
        <div className="relative flex items-center w-full">
          <Search className="absolute left-3.5 w-4 h-4 text-[#8A8F9E] dark:text-[#74798C] pointer-events-none" />
          <input
            id="conversation-search-input"
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t(language, 'search')}
            className="w-full h-10 pl-9.5 pr-8 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] dark:placeholder-[#74798C] rounded-xl border border-transparent focus:border-[#2563EB]/30 focus:bg-white dark:focus:bg-[#1C1F27] focus:outline-none transition-all"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 p-1 rounded-full text-[#74798C] hover:text-[#1E2230] dark:hover:text-white transition-colors"
              aria-label="Clear search"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Filter Pills */}
      <div className="flex items-center gap-1.5 px-4 py-2 overflow-x-auto no-scrollbar">
        <button
          id="filter-pill-all"
          onClick={() => setActiveFilter('all')}
          className={`px-3 py-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition-all ${
            activeFilter === 'all'
              ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]'
              : 'text-[#74798C] dark:text-[#9DA3B4] hover:bg-[#F7F8FC] dark:hover:bg-[#232630]'
          }`}
        >
          {t(language, 'all')}
        </button>

        <button
          id="filter-pill-unread"
          onClick={() => setActiveFilter('unread')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition-all ${
            activeFilter === 'unread'
              ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]'
              : 'text-[#74798C] dark:text-[#9DA3B4] hover:bg-[#F7F8FC] dark:hover:bg-[#232630]'
          }`}
        >
          <span>{t(language, 'unread')}</span>
          {totalUnreadCount > 0 && (
            <span className="w-2 h-2 rounded-full bg-[#2563EB]" />
          )}
        </button>

        <button
          id="filter-pill-groups"
          onClick={() => setActiveFilter('groups')}
          className={`px-3 py-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition-all ${
            activeFilter === 'groups'
              ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]'
              : 'text-[#74798C] dark:text-[#9DA3B4] hover:bg-[#F7F8FC] dark:hover:bg-[#232630]'
          }`}
        >
          {t(language, 'groups')}
        </button>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-1">
        {!searchQuery && activeFilter === 'all' && onOpenAssistant && (
          <button
            type="button"
            onClick={onOpenAssistant}
            className={`group relative flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-left transition-all duration-150 ${assistantSelected ? 'bg-[#EFF6FF] text-[#1E2230] dark:bg-[#2563EB]/15 dark:text-[#F5F6FA]' : 'text-[#1E2230] hover:bg-[#F7F8FC] dark:text-[#E2E5F0] dark:hover:bg-[#232630]/70'}`}
          >
            {assistantSelected && <span className="absolute left-0 top-3 bottom-3 w-1 rounded-r-full bg-[#2563EB]" />}
            <img src={ASSISTANT_AVATAR_URL} alt={assistantName(language)} className="h-12 w-12 shrink-0 rounded-full object-cover ring-1 ring-violet-200 dark:ring-violet-400/30" referrerPolicy="no-referrer" />
            <span className="min-w-0 flex-1"><span className="mb-1 flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-[#1E2230] dark:text-[#E2E5F0]">{assistantName(language)}</span><span className="shrink-0 whitespace-nowrap text-xs text-[#8A8F9E] dark:text-[#74798C]">{assistantLastMessageTime}</span></span><span className="block truncate text-xs text-[#74798C] dark:text-[#9DA3B4]">{assistantLastMessage}</span></span>
          </button>
        )}
        {filteredConversations.length > 0 ? (
          filteredConversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              isSelected={conversation.id === selectedConversationId}
              onSelect={onSelectConversation}
              language={language}
            />
          ))
        ) : (
          <div className="flex flex-col items-center justify-center h-48 px-4 text-center">
            <p className="text-sm font-medium text-[#74798C] dark:text-[#9DA3B4]">
              {t(language, 'noChats')}
            </p>
            <p className="text-xs text-[#9DA3B4] dark:text-[#74798C] mt-1">
              {t(language, 'adjustSearch')}
            </p>
          </div>
        )}
      </div>
    </section>
  );
};
