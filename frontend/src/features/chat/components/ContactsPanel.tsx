import React, { useState } from 'react';
import { User, LanguageCode } from '../types';
import { interactionText, tx } from '../i18n';
import { Search, MessageSquare, Phone, Video, Globe, UserPlus } from 'lucide-react';

interface ContactsPanelProps {
  onStartChatWithUser: (user: User) => void;
  onOpenNewChat: () => void;
  users: User[];
  onSearchUsers: (query: string) => void;
  language: LanguageCode;
}

export const ContactsPanel: React.FC<ContactsPanelProps> = ({
  onStartChatWithUser,
  onOpenNewChat,
  users,
  onSearchUsers,
  language,
}) => {
  const [search, setSearch] = useState('');
  const contacts = users;

  const filteredContacts = contacts.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase()) ||
    c.email.toLowerCase().includes(search.toLowerCase()) ||
    c.username.toLowerCase().includes(search.toLowerCase()) ||
    c.bio?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <section
      id="contacts-list-panel"
      className="flex flex-col w-full md:w-[340px] md:min-w-[340px] md:max-w-[340px] h-screen bg-white dark:bg-[#1C1F27] border-r border-[#E8EAF0] dark:border-[#232630] z-20 flex-shrink-0 transition-colors"
      aria-label="Contacts Panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <h1 className="text-2xl font-bold text-[#1E2230] dark:text-[#F5F6FA] tracking-tight">
          {tx(language, 'Contacts')}
        </h1>
        <button
          onClick={onOpenNewChat}
          className="p-2 rounded-xl bg-[#2563EB] text-white hover:bg-[#1D4ED8] transition-all"
        >
          <UserPlus className="w-5 h-5" />
        </button>
      </div>

      {/* Search */}
      <div className="px-4 py-1.5">
        <div className="relative flex items-center w-full">
          <Search className="absolute left-3.5 w-4 h-4 text-[#8A8F9E]" />
          <input
            type="text"
            value={search}
            onChange={(e) => { setSearch(e.target.value); onSearchUsers(e.target.value); }}
            placeholder={interactionText(language, 'Search contacts...')}
            className="w-full h-10 pl-9.5 pr-4 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] rounded-xl border border-transparent focus:border-[#2563EB]/30 focus:bg-white dark:focus:bg-[#1C1F27] focus:outline-none"
          />
        </div>
      </div>

      {/* Contacts List */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        {filteredContacts.map((contact) => (
          <div
            key={contact.id}
            className="flex items-center justify-between p-3 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 hover:bg-[#EFF6FF]/60 dark:hover:bg-[#2563EB]/10 border border-[#E8EAF0] dark:border-[#2A2E3D] transition-colors"
          >
            <div className="flex items-center gap-3 min-w-0">
              <div className="relative flex-shrink-0">
                <img
                  src={contact.avatar}
                  alt={contact.name}
                  className="w-11 h-11 rounded-full object-cover"
                  referrerPolicy="no-referrer"
                />
                {contact.onlineStatus === 'online' && (
                  <span className="absolute bottom-0 right-0 w-3 h-3 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
                )}
              </div>
              <div className="min-w-0">
                <h4 className="text-sm font-bold text-[#1E2230] dark:text-[#F5F6FA] truncate">
                  {contact.name}
                </h4>
                <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] truncate">
                  {contact.email}
                </p>
                <span className="inline-flex items-center gap-1 mt-1 text-[10px] font-medium text-[#2563EB]">
                  <Globe className="w-2.5 h-2.5" />
                  {contact.nativeLanguage.toUpperCase()}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1">
              <button
                onClick={() => onStartChatWithUser(contact)}
                aria-label={`Message ${contact.name}`}
                className="p-2 rounded-xl text-[#2563EB] hover:bg-white dark:hover:bg-[#2A2E3D] transition-colors shadow-2xs"
              >
                <MessageSquare className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};
