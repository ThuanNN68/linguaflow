import React, { useState } from 'react';
import { User, LanguageCode } from '../types';
import { tx } from '../i18n';
import { Search, X, MessageSquare, Users, Globe, Plus } from 'lucide-react';

interface NewConversationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectUser: (user: User) => void;
  onCreateGroupClick: () => void;
  users: User[];
  /** Máy chủ trả về cho truy vấn hiện tại; null nghĩa là chưa tìm kiếm. */
  searchResults: User[] | null;
  onSearchUsers: (query: string) => void;
  language: LanguageCode;
}

export const NewConversationModal: React.FC<NewConversationModalProps> = ({
  isOpen,
  onClose,
  onSelectUser,
  onCreateGroupClick,
  users,
  searchResults,
  onSearchUsers,
  language,
}) => {
  const [search, setSearch] = useState('');

  if (!isOpen) return null;

  // Khi máy chủ đã trả kết quả thì hiển thị đúng kết quả đó, không lọc lại ở
  // máy khách: máy chủ có thể khớp trên trường mà bộ lọc dưới đây không đọc
  // (email chẳng hạn), và lọc thêm một lần nữa sẽ giấu mất người vừa tìm thấy.
  const filteredUsers = searchResults ?? users.filter(
    (u) =>
      u.name.toLowerCase().includes(search.toLowerCase()) ||
      u.username.toLowerCase().includes(search.toLowerCase()) ||
      u.bio?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div
      id="new-conversation-modal-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs animate-in fade-in duration-150"
    >
      <div
        id="new-conversation-modal"
        className="w-full max-w-md bg-white dark:bg-[#1C1F27] rounded-3xl shadow-2xl border border-[#E8EAF0] dark:border-[#2A2E3D] overflow-hidden flex flex-col max-h-[85vh] animate-in zoom-in-95 duration-150"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#E8EAF0] dark:border-[#2A2E3D]">
          <h3 className="text-lg font-bold text-[#1E2230] dark:text-[#F5F6FA]">
            {tx(language, 'New Conversation')}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-xl text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#232630]"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search */}
        <div className="p-4 border-b border-[#E8EAF0] dark:border-[#2A2E3D]">
          <div className="relative flex items-center">
            <Search className="absolute left-3.5 w-4 h-4 text-[#8A8F9E]" />
            <input
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); onSearchUsers(e.target.value); }}
              placeholder="Search people by name or username..."
              className="w-full h-10 pl-10 pr-4 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
            />
          </div>
        </div>

        {/* Quick Action: Create Group */}
        <div className="px-4 py-2">
          <button
            onClick={() => {
              onClose();
              onCreateGroupClick();
            }}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-2xl bg-[#EFF6FF] dark:bg-[#2563EB]/15 text-[#2563EB] font-semibold text-xs hover:bg-[#DBEAFE] dark:hover:bg-[#2563EB]/25 transition-all text-left"
          >
            <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-[#2563EB] text-white">
              <Users className="w-4 h-4" />
            </div>
            <div>
              <p className="font-bold">{tx(language, 'Create a New Group')}</p>
              <p className="text-[11px] font-normal text-[#2563EB]/80">
                Chat with up to 250 international peers
              </p>
            </div>
          </button>
        </div>

        {/* Users List */}
        <div className="flex-1 overflow-y-auto px-4 py-2 space-y-1">
          <p className="text-[11px] font-bold uppercase tracking-wider text-[#74798C] px-2 mb-1">
            {tx(language, 'Suggested Contacts')}
          </p>

          {filteredUsers.map((user) => (
            <button
              key={user.id}
              onClick={() => {
                onSelectUser(user);
                onClose();
              }}
              className="w-full flex items-center justify-between p-2.5 rounded-2xl hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left group"
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="relative flex-shrink-0">
                  <img
                    src={user.avatar}
                    alt={user.name}
                    className="w-10 h-10 rounded-full object-cover"
                    referrerPolicy="no-referrer"
                  />
                  {user.onlineStatus === 'online' && (
                    <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[#1E2230] dark:text-[#F5F6FA] truncate group-hover:text-[#2563EB] transition-colors">
                    {user.name}
                  </p>
                  <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] truncate">
                    @{user.username} • {user.role}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1.5 flex-shrink-0">
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium bg-[#F4F5F8] dark:bg-[#2A2E3D] text-[#2563EB]">
                  <Globe className="w-2.5 h-2.5" />
                  {user.nativeLanguage.toUpperCase()}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
