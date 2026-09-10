import React, { useState } from 'react';
import { User, LanguageCode } from '../types';
import { interactionText } from '../i18n';
import { X, Search, Check, Users, ArrowRight, ArrowLeft, Globe } from 'lucide-react';

interface CreateGroupModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreateGroup: (name: string, memberIds: string[]) => void;
  users: User[];
  onSearchUsers: (query: string) => void;
  language: LanguageCode;
}

export const CreateGroupModal: React.FC<CreateGroupModalProps> = ({
  isOpen,
  onClose,
  onCreateGroup,
  users,
  onSearchUsers,
  language,
}) => {
  const [step, setStep] = useState<1 | 2>(1);
  const [search, setSearch] = useState('');
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [groupName, setGroupName] = useState('');
  const [groupDesc, setGroupDesc] = useState('');

  if (!isOpen) return null;

  const usersList = users;
  const filteredUsers = usersList.filter((u) =>
    u.name.toLowerCase().includes(search.toLowerCase())
  );

  const toggleSelectUser = (id: string) => {
    setSelectedUserIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const handleFinishCreate = () => {
    if (!groupName.trim()) return;

    onCreateGroup(groupName.trim(), selectedUserIds);
    onClose();
    // Reset state
    setStep(1);
    setSelectedUserIds([]);
    setGroupName('');
    setGroupDesc('');
  };

  return (
    <div
      id="create-group-modal-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs animate-in fade-in duration-150"
    >
      <div
        id="create-group-modal"
        className="w-full max-w-md bg-white dark:bg-[#1C1F27] rounded-3xl shadow-2xl border border-[#E8EAF0] dark:border-[#2A2E3D] overflow-hidden flex flex-col max-h-[85vh] animate-in zoom-in-95 duration-150"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#E8EAF0] dark:border-[#2A2E3D]">
          <div className="flex items-center gap-2">
            {step === 2 && (
              <button
                onClick={() => setStep(1)}
                className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
            )}
            <h3 className="text-lg font-bold text-[#1E2230] dark:text-[#F5F6FA]">
              {interactionText(language, step === 1 ? 'Select Group Members' : 'Group Details')}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-xl text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#232630]"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* STEP 1: Select Members */}
        {step === 1 && (
          <>
            {/* Selected Chips */}
            {selectedUserIds.length > 0 && (
              <div className="flex items-center gap-1.5 p-3 border-b border-[#E8EAF0] dark:border-[#2A2E3D] overflow-x-auto no-scrollbar">
                {selectedUserIds.map((id) => {
                  const u = usersList.find((user) => user.id === id);
                  if (!u) return null;
                  return (
                    <div
                      key={id}
                      className="flex items-center gap-1.5 pl-1 pr-2 py-1 bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB] rounded-full text-xs font-semibold whitespace-nowrap"
                    >
                      <img
                        src={u.avatar}
                        alt={u.name}
                        className="w-5 h-5 rounded-full object-cover"
                        referrerPolicy="no-referrer"
                      />
                      <span>{u.name}</span>
                      <button
                        onClick={() => toggleSelectUser(id)}
                        className="hover:text-rose-500 rounded-full"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Search */}
            <div className="p-4 border-b border-[#E8EAF0] dark:border-[#2A2E3D]">
              <div className="relative flex items-center">
                <Search className="absolute left-3.5 w-4 h-4 text-[#8A8F9E]" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => { setSearch(e.target.value); onSearchUsers(e.target.value); }}
                  placeholder={interactionText(language, 'Search people to add...')}
                  className="w-full h-10 pl-10 pr-4 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
                />
              </div>
            </div>

            {/* User List */}
            <div className="flex-1 overflow-y-auto px-4 py-2 space-y-1">
              {filteredUsers.map((user) => {
                const isSelected = selectedUserIds.includes(user.id);
                return (
                  <div
                    key={user.id}
                    onClick={() => toggleSelectUser(user.id)}
                    className={`flex items-center justify-between p-2.5 rounded-2xl cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/15'
                        : 'hover:bg-[#F7F8FC] dark:hover:bg-[#232630]'
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <img
                        src={user.avatar}
                        alt={user.name}
                        className="w-10 h-10 rounded-full object-cover"
                        referrerPolicy="no-referrer"
                      />
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-[#1E2230] dark:text-[#F5F6FA] truncate">
                          {user.name}
                        </p>
                        <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] truncate">
                          @{user.username}
                        </p>
                      </div>
                    </div>

                    <div
                      className={`w-6 h-6 rounded-full flex items-center justify-center border transition-all ${
                        isSelected
                          ? 'bg-[#2563EB] border-[#2563EB] text-white'
                          : 'border-[#CED2DE] dark:border-[#3A3F50]'
                      }`}
                    >
                      {isSelected && <Check className="w-3.5 h-3.5" />}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Next Button */}
            <div className="p-4 border-t border-[#E8EAF0] dark:border-[#2A2E3D]">
              <button
                disabled={selectedUserIds.length === 0}
                onClick={() => setStep(2)}
                className={`w-full flex items-center justify-center gap-2 py-3 rounded-xl font-semibold text-sm transition-all ${
                  selectedUserIds.length > 0
                    ? 'bg-[#2563EB] text-white hover:bg-[#1D4ED8] shadow-md shadow-[#2563EB]/20'
                    : 'bg-[#F4F5F8] dark:bg-[#232630] text-[#8A8F9E] cursor-not-allowed opacity-60'
                }`}
              >
                <span>Next ({selectedUserIds.length} selected)</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </>
        )}

        {/* STEP 2: Group Details */}
        {step === 2 && (
          <div className="p-6 space-y-5">
            {/* Avatar picker preview */}
            <div className="flex flex-col items-center gap-2">
              <div className="flex items-center justify-center w-20 h-20 rounded-full bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB] ring-4 ring-[#EFF6FF] dark:ring-[#2563EB]/20">
                <Users className="w-8 h-8" />
              </div>
              <p className="text-xs text-[#74798C]">{interactionText(language, 'Group icon')}</p>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                {interactionText(language, 'Group Name')} *
              </label>
              <input
                type="text"
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
                placeholder="e.g. AI Research Cohort 2026 🌍"
                className="w-full h-11 px-4 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                {interactionText(language, 'Description (Optional)')}
              </label>
              <textarea
                rows={2}
                value={groupDesc}
                onChange={(e) => setGroupDesc(e.target.value)}
                placeholder={interactionText(language, 'What is this group for?')}
                className="w-full p-3 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none resize-none"
              />
            </div>

            <div className="p-3 bg-[#EFF6FF]/50 dark:bg-[#2563EB]/10 rounded-2xl text-xs text-[#2563EB]">
              ✨ {interactionText(language, 'Group translations notice')}
            </div>

            <button
              disabled={!groupName.trim()}
              onClick={handleFinishCreate}
              className={`w-full py-3 rounded-xl font-semibold text-sm transition-all ${
                groupName.trim()
                  ? 'bg-[#2563EB] text-white hover:bg-[#1D4ED8] shadow-md shadow-[#2563EB]/20'
                  : 'bg-[#F4F5F8] dark:bg-[#232630] text-[#8A8F9E] cursor-not-allowed opacity-60'
              }`}
            >
              {interactionText(language, 'Create Group')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
