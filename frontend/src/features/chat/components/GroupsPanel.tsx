import React, { useState } from 'react';
import { Conversation, LanguageCode } from '../types';
import { interactionText, tx } from '../i18n';
import { UsersRound, Plus, Search, Shield, Globe } from 'lucide-react';

interface GroupsPanelProps {
  conversations: Conversation[];
  selectedConversationId: string | null;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateGroupClick: () => void;
  language: LanguageCode;
}

export const GroupsPanel: React.FC<GroupsPanelProps> = ({
  conversations,
  selectedConversationId,
  onSelectConversation,
  onCreateGroupClick,
  language,
}) => {
  const [search, setSearch] = useState('');

  const groups = conversations.filter((c) => c.type === 'group');
  const filteredGroups = groups.filter((g) =>
    g.name.toLowerCase().includes(search.toLowerCase()) ||
    g.description?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <section
      id="groups-list-panel"
      className="flex flex-col w-full md:w-[340px] md:min-w-[340px] md:max-w-[340px] h-screen bg-white dark:bg-[#1C1F27] border-r border-[#E8EAF0] dark:border-[#232630] z-20 flex-shrink-0 transition-colors"
      aria-label="Groups Panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <h1 className="text-2xl font-bold text-[#1E2230] dark:text-[#F5F6FA] tracking-tight">
          {tx(language, 'Groups')}
        </h1>
        <button
          onClick={onCreateGroupClick}
          className="p-2 rounded-xl bg-[#2563EB] text-white hover:bg-[#1D4ED8] transition-all"
        >
          <Plus className="w-5 h-5" />
        </button>
      </div>

      {/* Search */}
      <div className="px-4 py-1.5">
        <div className="relative flex items-center w-full">
          <Search className="absolute left-3.5 w-4 h-4 text-[#8A8F9E]" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={interactionText(language, 'Search study groups...')}
            className="w-full h-10 pl-9.5 pr-4 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] rounded-xl border border-transparent focus:border-[#2563EB]/30 focus:bg-white dark:focus:bg-[#1C1F27] focus:outline-none"
          />
        </div>
      </div>

      {/* Groups list */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {filteredGroups.map((group) => {
          const isSelected = group.id === selectedConversationId;
          return (
            <div
              key={group.id}
              onClick={() => onSelectConversation(group)}
              className={`p-3.5 rounded-2xl cursor-pointer transition-all border ${
                isSelected
                  ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/15 border-[#2563EB]/40'
                  : 'bg-[#F7F8FC] dark:bg-[#232630]/60 hover:bg-[#EFF6FF]/40 border-[#E8EAF0] dark:border-[#2A2E3D]'
              }`}
            >
              <div className="flex items-center gap-3">
                <img
                  src={group.avatar}
                  alt={group.name}
                  className="w-12 h-12 rounded-2xl object-cover ring-1 ring-[#E8EAF0] dark:ring-[#2A2E3D]"
                  referrerPolicy="no-referrer"
                />
                <div className="min-w-0 flex-1">
                  <h4 className="text-sm font-bold text-[#1E2230] dark:text-[#F5F6FA] truncate">
                    {group.name}
                  </h4>
                  <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] truncate">
                    {group.memberCount || 8} {tx(language, 'multilingual members')}
                  </p>
                  <span className="inline-flex items-center gap-1 mt-1 text-[10px] font-semibold text-[#2563EB]">
                    <Globe className="w-2.5 h-2.5" />
                    {tx(language, 'Auto Live Translation Active')}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};
