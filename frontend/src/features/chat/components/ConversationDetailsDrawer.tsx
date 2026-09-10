import React, { useEffect, useState } from 'react';
import { dismissReminder, listReminders, type ApiReminder } from '../api/chat-api';
import { Conversation, LanguageCode, MessageAttachment, User } from '../types';
import { interactionText, settingText, tx } from '../i18n';
import {
  X,
  Bell,
  BellOff,
  ShieldAlert,
  LogOut,
  UsersRound,
  Globe,
  Pin,
  Download,
  FileText,
  UserPlus,
  Trash2,
  ShieldCheck,
  Crown,
  Pencil,
  Check,
  Volume2,
} from 'lucide-react';

interface ConversationDetailsDrawerProps {
  conversation: Conversation;
  isOpen: boolean;
  onClose: () => void;
  onToggleMute: (conversationId: string) => void;
  onTogglePin?: (conversationId: string) => void;
  onLeaveGroup?: (conversationId: string) => void;
  onDeleteGroup?: (conversationId: string) => void;
  onBlockContact: (conversationId: string) => void;
  language: LanguageCode;
  attachments: MessageAttachment[];
  onDownloadAttachment: (attachment: MessageAttachment) => void;
  currentUserId: string;
  availableUsers: User[];
  onAddMembers?: (conversationId: string, userIds: string[]) => void;
  onRemoveMember?: (conversationId: string, userId: string) => void;
  onChangeMemberRole?: (conversationId: string, userId: string, role: 'admin' | 'member') => void;
  onSearchUsers?: (query: string) => void;
  onTransferOwnership?: (conversationId: string, userId: string) => void;
  onUpdateGroup?: (conversationId: string, title: string, description: string) => void;
  onStartDirectChat?: (userId: string) => void;
  calendarToken?: string;
}

export const ConversationDetailsDrawer: React.FC<ConversationDetailsDrawerProps> = ({
  conversation,
  isOpen,
  onClose,
  onToggleMute,
  onTogglePin,
  onLeaveGroup,
  onDeleteGroup,
  onBlockContact,
  language,
  attachments,
  onDownloadAttachment,
  currentUserId,
  availableUsers,
  onAddMembers,
  onRemoveMember,
  onChangeMemberRole,
  onSearchUsers,
  onTransferOwnership,
  onUpdateGroup,
  onStartDirectChat,
  calendarToken,
}) => {
  const [showAddMembers, setShowAddMembers] = useState(false);
  const [memberSearch, setMemberSearch] = useState('');
  const [editingGroup, setEditingGroup] = useState(false);
  const [groupName, setGroupName] = useState(conversation.name);
  const [groupBio, setGroupBio] = useState(conversation.description || '');
  const [reminders, setReminders] = useState<ApiReminder[]>([]);
  useEffect(() => { if (isOpen && calendarToken) void listReminders(calendarToken).then(setReminders).catch(() => setReminders([])); }, [calendarToken, isOpen]);
  if (!isOpen) return null;

  const isGroup = conversation.type === 'group';
  const reminderCopy = language === 'vi'
    ? { title: 'Danh sách nhắc lịch', empty: 'Không có nhắc lịch đang chờ.', at: 'Nhắc lúc', dismiss: 'Đã xem' }
    : { title: 'Reminders', empty: 'No pending reminders.', at: 'Reminder at', dismiss: 'Dismiss' };
  const groupBioText = conversation.description || (language === 'vi' ? 'Chưa có mô tả nhóm' : 'No group description yet');
  const canManageMembers = conversation.currentUserRole === 'owner' || conversation.currentUserRole === 'admin';
  const candidates = availableUsers.filter((user) =>
    !conversation.members?.some((member) => member.id === user.id)
    && (user.name.toLowerCase().includes(memberSearch.toLowerCase()) || user.email.toLowerCase().includes(memberSearch.toLowerCase()))
  );
  const sharedFiles = attachments.filter((attachment) => attachment.type !== 'audio');

  return (
    <>
    <aside
      id="conversation-details-drawer"
      className="w-80 sm:w-88 h-screen bg-white dark:bg-[#1C1F27] border-l border-[#E8EAF0] dark:border-[#232630] flex flex-col z-30 shadow-xl overflow-y-auto flex-shrink-0 animate-in slide-in-from-right duration-200"
      aria-label="Conversation Details"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-[#E8EAF0] dark:border-[#232630]">
        <h3 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">
          {tx(language, isGroup ? 'Group Information' : 'Contact Details')}
        </h3>
        <button
          id="close-details-drawer-btn"
          onClick={onClose}
          aria-label="Close details"
          className="p-1.5 rounded-xl text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Main Profile Info */}
      <div className="flex flex-col items-center p-6 text-center border-b border-[#E8EAF0] dark:border-[#232630]">
        <div className="relative mb-3">
          <img
            src={conversation.avatar}
            alt={conversation.name}
            className="w-20 h-20 rounded-full object-cover ring-4 ring-[#EFF6FF] dark:ring-[#2563EB]/20"
            referrerPolicy="no-referrer"
          />
          {conversation.isOnline && !isGroup && (
            <span className="absolute bottom-1 right-1 w-4 h-4 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
          )}
        </div>

        {editingGroup ? <div className="w-full space-y-2 text-left">
          <label className="block text-[11px] font-semibold text-[#74798C]">Group name<input autoFocus value={groupName} onChange={(event) => setGroupName(event.target.value)} maxLength={255} className="mt-1 h-9 w-full rounded-xl border border-[#E8EAF0] bg-[#F4F5F8] px-3 text-sm text-[#1E2230] outline-none focus:border-[#2563EB] dark:border-[#2E3342] dark:bg-[#232630] dark:text-[#F5F6FA]" /></label>
          <label className="block text-[11px] font-semibold text-[#74798C]">Group bio<textarea value={groupBio} onChange={(event) => setGroupBio(event.target.value)} maxLength={500} rows={3} placeholder="What is this group about?" className="mt-1 w-full resize-none rounded-xl border border-[#E8EAF0] bg-[#F4F5F8] px-3 py-2 text-sm text-[#1E2230] placeholder:text-[#8A8F9E] outline-none focus:border-[#2563EB] dark:border-[#2E3342] dark:bg-[#232630] dark:text-[#F5F6FA]" /></label>
          <div className="flex justify-end gap-2"><button type="button" onClick={() => { setEditingGroup(false); setGroupName(conversation.name); setGroupBio(conversation.description || ''); }} className="rounded-lg px-3 py-1.5 text-xs font-semibold text-[#74798C]">Cancel</button><button type="button" disabled={!groupName.trim()} onClick={() => { onUpdateGroup?.(conversation.id, groupName.trim(), groupBio); setEditingGroup(false); }} className="inline-flex items-center gap-1 rounded-lg bg-[#2563EB] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"><Check className="h-3.5 w-3.5" /> Save</button></div>
        </div> : <div className="flex items-center gap-2"><h4 className="text-lg font-bold text-[#1E2230] dark:text-[#F5F6FA]">{conversation.name}</h4>{isGroup && canManageMembers && <button type="button" aria-label="Edit group information" onClick={() => setEditingGroup(true)} className="rounded-lg p-1 text-[#2563EB] hover:bg-[#EFF6FF]"><Pencil className="h-4 w-4" /></button>}</div>}

        {!isGroup && conversation.recipient && (
          <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] mt-0.5">
            @{conversation.recipient.username}
          </p>
        )}

        {!editingGroup && isGroup && (
          <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] mt-2 max-w-xs leading-relaxed">
            {groupBioText}
          </p>
        )}

        {!editingGroup && !isGroup && conversation.description && (
          <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] mt-2 max-w-xs leading-relaxed">
            {conversation.description}
          </p>
        )}

        {/* Language Badge */}
        {!isGroup && conversation.recipient && (
          <div className="inline-flex items-center gap-1.5 mt-3 px-3 py-1 bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB] text-xs font-semibold rounded-full">
            <Globe className="w-3.5 h-3.5" />
            <span>{settingText(language, 'Preferred Language')}: {conversation.recipient.nativeLanguage.toUpperCase()}</span>
          </div>
        )}

        {isGroup && (
          <div className="inline-flex items-center gap-1.5 mt-3 px-3 py-1 bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB] text-xs font-semibold rounded-full">
            <UsersRound className="w-3.5 h-3.5" />
            <span>{conversation.memberCount || 0} {tx(language, 'Members').toLowerCase()}</span>
          </div>
        )}
      </div>

      {/* Quick Action Toggles */}
      <div className="p-4 space-y-1 border-b border-[#E8EAF0] dark:border-[#232630]">
        <button
          onClick={() => onToggleMute(conversation.id)}
          className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left text-xs text-[#1E2230] dark:text-[#E2E5F0]"
        >
          <div className="flex items-center gap-3">
            {conversation.isMuted ? (
              <BellOff className="w-4 h-4 text-amber-500" />
            ) : (
              <Bell className="w-4 h-4 text-[#74798C]" />
            )}
            <span>{tx(language, 'Mute Notifications')}</span>
          </div>
          <span className="font-medium text-[#74798C]">
            {tx(language, conversation.isMuted ? 'Muted' : 'Off')}
          </span>
        </button>

        {onTogglePin && (
          <button
            onClick={() => onTogglePin(conversation.id)}
            className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left text-xs text-[#1E2230] dark:text-[#E2E5F0]"
          >
            <div className="flex items-center gap-3">
              <Pin className="w-4 h-4 text-[#74798C]" />
              <span>{tx(language, 'Pin Conversation')}</span>
            </div>
            <span className="font-medium text-[#74798C]">
              {tx(language, conversation.isPinned ? 'Pinned' : 'No')}
            </span>
          </button>
        )}
      </div>

      {/* Files that have been sent as part of a visible message. */}
      <div className="p-4 border-b border-[#E8EAF0] dark:border-[#232630]">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-bold uppercase tracking-wider text-[#74798C] dark:text-[#9DA3B4]">
            {interactionText(language, 'Shared files')}
          </span>
        </div>
        {sharedFiles.length === 0 ? (
          <p className="py-3 text-center text-xs text-[#9DA3B4]">
            {interactionText(language, 'No files shared yet')}
          </p>
        ) : (
          <div className="space-y-2">
            {sharedFiles.map((attachment) => (
              <button
                key={attachment.id}
                type="button"
                onClick={() => onDownloadAttachment(attachment)}
                aria-label={`${interactionText(language, 'Download file')}: ${attachment.name}`}
                className="group w-full flex items-center gap-3 rounded-xl p-2.5 text-left hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
              >
                <span className="flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]">
                  {attachment.type === 'audio'
                    ? <Volume2 className="w-4 h-4" />
                    : <FileText className="w-4 h-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">
                    {attachment.type === 'audio'
                      ? interactionText(language, 'Voice message')
                      : attachment.name}
                  </span>
                  <span className="block text-[10px] text-[#74798C] dark:text-[#9DA3B4]">
                    {attachment.size}
                  </span>
                </span>
                <Download className="w-4 h-4 flex-none text-[#9DA3B4] group-hover:text-[#2563EB]" />
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="p-4 border-b border-[#E8EAF0] dark:border-[#232630]">
        <span className="text-xs font-bold uppercase tracking-wider text-[#74798C] dark:text-[#9DA3B4]">{reminderCopy.title}</span>
        {reminders.length ? <div className="mt-3 space-y-2">{reminders.map((reminder) => <div key={reminder.id} className="flex items-center justify-between gap-2 rounded-lg bg-[#F7F8FC] p-2 text-xs dark:bg-[#232630]"><span>{reminderCopy.at} {new Date(reminder.remind_at).toLocaleString(language === 'vi' ? 'vi-VN' : 'en-US')}</span><button type="button" onClick={async () => { if (!calendarToken) return; await dismissReminder(calendarToken, reminder.id); setReminders((rows) => rows.filter((row) => row.id !== reminder.id)); }} className="font-semibold text-[#2563EB]">{reminderCopy.dismiss}</button></div>)}</div> : <p className="py-3 text-center text-xs text-[#9DA3B4]">{reminderCopy.empty}</p>}
      </div>

      {/* Group Members List (If Group) */}
      {isGroup && conversation.members && (
        <div className="p-4 border-b border-[#E8EAF0] dark:border-[#232630]">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold uppercase tracking-wider text-[#74798C] dark:text-[#9DA3B4]">
              {tx(language, 'Members')} ({conversation.members.length})
            </span>
            {canManageMembers && <button type="button" onClick={() => setShowAddMembers(true)} className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#2563EB]"><UserPlus className="w-3.5 h-3.5" /> Add</button>}
          </div>
          <div className="space-y-2">
            {conversation.members.map((member) => (
              <div key={member.id} className="flex items-center justify-between text-xs py-1">
                <div className="flex items-center gap-2.5 min-w-0">
                  {member.id !== currentUserId && onStartDirectChat ? (
                    <button
                      type="button"
                      onClick={() => onStartDirectChat(member.id)}
                      aria-label={`Open direct chat with ${member.name}`}
                      title={`Chat with ${member.name}`}
                      className="rounded-full focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:ring-offset-2 dark:focus:ring-offset-[#1C1F27]"
                    >
                      <img
                        src={member.avatar}
                        alt={member.name}
                        className="w-7 h-7 rounded-full object-cover"
                        referrerPolicy="no-referrer"
                      />
                    </button>
                  ) : (
                    <img
                      src={member.avatar}
                      alt={member.name}
                      className="w-7 h-7 rounded-full object-cover"
                      referrerPolicy="no-referrer"
                    />
                  )}
                  <div className="min-w-0">
                    <p className="font-semibold text-[#1E2230] dark:text-[#F5F6FA] truncate">
                      {member.name}
                    </p>
                    <p className="text-[10px] text-[#74798C] dark:text-[#9DA3B4] truncate">
                      {member.role || tx(language, 'Member')}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {conversation.currentUserRole === 'owner' && member.id !== currentUserId && member.role !== 'owner' && <button type="button" title={member.role === 'admin' ? 'Remove admin' : 'Make admin'} onClick={() => onChangeMemberRole?.(conversation.id, member.id, member.role === 'admin' ? 'member' : 'admin')} className="rounded p-1 text-[#2563EB] hover:bg-[#EFF6FF]"><ShieldCheck className="w-3.5 h-3.5" /></button>}
                  {conversation.currentUserRole === 'owner' && member.id !== currentUserId && <button type="button" title="Transfer ownership" onClick={() => window.confirm(`Transfer group ownership to ${member.name}?`) && onTransferOwnership?.(conversation.id, member.id)} className="rounded p-1 text-amber-600 hover:bg-amber-50"><Crown className="w-3.5 h-3.5" /></button>}
                  {canManageMembers && member.id !== currentUserId && member.role !== 'owner' && !(conversation.currentUserRole === 'admin' && member.role === 'admin') && <button type="button" title="Remove member" onClick={() => window.confirm(`Remove ${member.name} from this group?`) && onRemoveMember?.(conversation.id, member.id)} className="rounded p-1 text-rose-600 hover:bg-rose-50"><Trash2 className="w-3.5 h-3.5" /></button>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Danger Zone */}
      <div className="p-4 border-t border-[#E8EAF0] dark:border-[#232630] mt-auto">
        {isGroup && conversation.currentUserRole === 'owner' ? (
          <button onClick={() => window.confirm('Delete this group for everyone?') && onDeleteGroup?.(conversation.id)} className="w-full flex items-center justify-center gap-2 py-2.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 rounded-xl transition-colors">
            <ShieldAlert className="w-4 h-4" />
            <span>Delete group</span>
          </button>
        ) : isGroup ? (
          <button onClick={() => window.confirm('Leave this group?') && onLeaveGroup?.(conversation.id)} className="w-full flex items-center justify-center gap-2 py-2.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 rounded-xl transition-colors">
            <LogOut className="w-4 h-4" />
            <span>{tx(language, 'Leave Group')}</span>
          </button>
        ) : null}
      </div>
    </aside>
    {showAddMembers && (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-4 backdrop-blur-[2px]" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowAddMembers(false); }}>
        <section role="dialog" aria-modal="true" aria-label="Add group members" className="w-full max-w-md overflow-hidden rounded-2xl border border-[#E8EAF0] bg-white shadow-2xl dark:border-[#2E3342] dark:bg-[#1C1F27]">
          <header className="flex items-center justify-between border-b border-[#E8EAF0] px-5 py-4 dark:border-[#2E3342]">
            <div><h3 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">Add members</h3><p className="mt-0.5 text-xs text-[#74798C]">Add people to {conversation.name}</p></div>
            <button type="button" aria-label="Close add members" onClick={() => setShowAddMembers(false)} className="rounded-lg p-1.5 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#232630]"><X className="h-5 w-5" /></button>
          </header>
          <div className="p-5">
            <input autoFocus value={memberSearch} onChange={(event) => { setMemberSearch(event.target.value); if (event.target.value.trim().length >= 2) onSearchUsers?.(event.target.value); }} placeholder="Search by name or email..." className="h-10 w-full rounded-xl border border-[#E8EAF0] bg-[#F4F5F8] px-3 text-sm text-[#1E2230] placeholder:text-[#8A8F9E] outline-none focus:border-[#2563EB] dark:border-[#2E3342] dark:bg-[#232630] dark:text-[#F5F6FA] dark:placeholder:text-[#74798C]" />
            <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
              {candidates.length ? candidates.map((user) => <button key={user.id} type="button" onClick={() => { onAddMembers?.(conversation.id, [user.id]); setShowAddMembers(false); setMemberSearch(''); }} className="flex w-full items-center gap-3 rounded-xl p-2.5 text-left hover:bg-[#F4F5F8] dark:hover:bg-[#232630]"><img src={user.avatar} alt="" className="h-9 w-9 rounded-full" /><span className="min-w-0"><span className="block truncate text-sm font-semibold text-[#1E2230] dark:text-[#F5F6FA]">{user.name}</span><span className="block truncate text-xs text-[#74798C]">{user.email}</span></span><UserPlus className="ml-auto h-4 w-4 text-[#2563EB]" /></button>) : <p className="py-8 text-center text-sm text-[#74798C]">No users found</p>}
            </div>
          </div>
        </section>
      </div>
    )}
    </>
  );
};
