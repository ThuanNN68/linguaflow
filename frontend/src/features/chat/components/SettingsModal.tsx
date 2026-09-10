import React, { useEffect, useRef, useState } from 'react';
import { AppSettings, User, LanguageCode } from '../types';
import type { AgentConsentScope } from '../api/chat-api';
import { AssistantConsentDialog } from './AssistantConsentDialog';
import { CHAT_LANGUAGES } from '../constants';
import { settingText, t } from '../i18n';
import { API_BASE } from '@/config/env';
import {
  X,
  Languages,
  User as UserIcon,

  Bell,
  Shield,
  Bot,
  Check,
  Globe2,
  Volume2,
  Eye,
  Camera,
} from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: AppSettings;
  onUpdateSettings: (newSettings: Partial<AppSettings>) => void;
  currentUser: User;
  onUpdateUser: (newUser: Partial<User>) => void;
  agentConsents?: Partial<Record<AgentConsentScope, boolean>>;
  /** False while any scope has never been answered — the cue to ask. Distinct
   *  from "nothing granted": a refusal is an answer and must not re-prompt. */
  agentConsentsAnswered?: boolean;
  onUpdateAgentConsents?: (changes: Partial<Record<AgentConsentScope, boolean>>) => void;
}

type SettingsSection = 'language' | 'profile' | 'notifications' | 'privacy' | 'ai';

const copy = settingText;

/** The permission list the user is asked to agree to, in the order shown.
 *
 *  Each line says what the assistant does with the data, in one sentence and in
 *  plain words. The order runs from the least to the most exposing, so someone
 *  who stops reading part way has still seen the mildest ones first. Kept beside
 *  the component rather than in a locale file because the wording *is* the
 *  consent — a translation that drifts changes what was agreed to.
 */
const CONSENT_COPY: { scope: AgentConsentScope; title: string; detail: string; englishTitle: string; englishDetail: string }[] = [
  {
    scope: 'read_conversations',
    title: 'Đọc nội dung hội thoại',
    detail: 'Trợ lý đọc tin nhắn trong hội thoại bạn mở để tóm tắt và tìm việc cần làm.',
    englishTitle: 'Read conversations',
    englishDetail: 'Let the assistant read open conversations to summarize them and identify follow-up tasks.',
  },
  {
    scope: 'proactive_scan',
    title: 'Tự phát hiện việc khi bạn nhắn',
    detail: 'Mỗi tin nhắn bạn gửi được quét để tìm cam kết và lịch hẹn, kể cả khi bạn không hỏi.',
    englishTitle: 'Proactively detect tasks',
    englishDetail: 'Scan your messages for commitments and appointments, even when you do not ask.',
  },
  {
    scope: 'store_memory',
    title: 'Ghi nhớ hội thoại lâu dài',
    detail: 'Tin nhắn của bạn được lưu thêm dạng vector để trợ lý nhớ được chuyện đã nói từ lâu.',
    englishTitle: 'Remember conversations',
    englishDetail: 'Store conversation context so the assistant can remember what you discussed over time.',
  },
  {
    scope: 'calendar_read',
    title: 'Đọc lịch cá nhân',
    detail: 'Cho phép trợ lý xem lịch nội bộ để hỗ trợ lập kế hoạch.',
    englishTitle: 'Read your personal calendar',
    englishDetail: 'Let the assistant read your in-app calendar to help plan events.',
  },
  {
    scope: 'calendar_write',
    title: 'Tạo sự kiện trong lịch',
    detail: 'Cho phép trợ lý tạo sự kiện nội bộ sau khi bạn duyệt.',
    englishTitle: 'Create calendar events',
    englishDetail: 'Create approved events in your in-app calendar.',
  },
];

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  currentUser,
  onUpdateUser,
  agentConsents = {},
  agentConsentsAnswered = true,
  onUpdateAgentConsents,
}) => {
  // `preferredLanguage` controls message translation; labels must follow the
  // separately persisted interface language selected by this account.
  const language = settings.interfaceLanguage;
  const assistantText = (english: string, vietnamese: string) =>
    language === 'vi' ? vietnamese : english;
  const [activeSection, setActiveSection] = useState<SettingsSection>('language');
  const [name, setName] = useState(currentUser.name);
  const [bio, setBio] = useState(currentUser.bio || '');
  const [isConsentDialogOpen, setIsConsentDialogOpen] = useState(false);
  const [avatar, setAvatar] = useState(currentUser.avatar);
  const [pushEnabled, setPushEnabled] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushError, setPushError] = useState('');
  const avatarInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setName(currentUser.name);
    setBio(currentUser.bio || '');
    setAvatar(currentUser.avatar);
  }, [currentUser]);

  useEffect(() => {
    if (!isOpen || !('serviceWorker' in navigator)) return;
    void navigator.serviceWorker.getRegistration().then(async (registration) => {
      setPushEnabled(Boolean(await registration?.pushManager.getSubscription()));
    });
  }, [isOpen]);

  const togglePush = async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    const token = localStorage.getItem('access_token') ?? sessionStorage.getItem('access_token');
    if (!token) return;
    setPushBusy(true);
    setPushError('');
    try {
      const registration = await navigator.serviceWorker.register('/sw.js');
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        await fetch(`${API_BASE}/api/v1/push/subscriptions?endpoint=${encodeURIComponent(existing.endpoint)}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
        await existing.unsubscribe();
        setPushEnabled(false);
      } else {
        if (Notification.permission === 'denied') throw new Error('notification_permission_denied');
        const keyResponse = await fetch(`${API_BASE}/api/v1/push/vapid-public-key`, { headers: { Authorization: `Bearer ${token}` } });
        if (!keyResponse.ok) throw new Error('push_unavailable');
        const key = await keyResponse.json() as { enabled: boolean; public_key: string };
        if (!key.enabled) throw new Error('push_unavailable');
        const padding = '='.repeat((4 - key.public_key.length % 4) % 4);
        const bytes = Uint8Array.from(atob((key.public_key + padding).replace(/-/g, '+').replace(/_/g, '/')), (char) => char.charCodeAt(0));
        const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
        const saved = await fetch(`${API_BASE}/api/v1/push/subscriptions`, { method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(subscription.toJSON()) });
        if (!saved.ok) { await subscription.unsubscribe(); throw new Error('push_save_failed'); }
        setPushEnabled(true);
      }
    } catch (error) {
      setPushError(error instanceof Error && error.message === 'notification_permission_denied'
        ? 'Browser notifications are blocked. Enable them in site settings.'
        : 'Push notifications are not configured or could not be enabled.');
    } finally { setPushBusy(false); }
  };

  if (!isOpen) return null;

  const sections = [
    { id: 'language' as SettingsSection, label: t(language, 'language'), icon: Languages },
    { id: 'profile' as SettingsSection, label: t(language, 'profile'), icon: UserIcon },

    { id: 'notifications' as SettingsSection, label: t(language, 'notifications'), icon: Bell },
    { id: 'privacy' as SettingsSection, label: t(language, 'privacy'), icon: Shield },
    { id: 'ai' as SettingsSection, label: t(language, 'aiTools'), icon: Bot },
  ];

  const handleSaveProfile = () => {
    onUpdateUser({ name, bio, avatar });
  };

  const handleAvatarChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || !file.type.startsWith('image/')) return;
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const size = 256;
      const scale = Math.max(size / image.naturalWidth, size / image.naturalHeight);
      const width = image.naturalWidth * scale;
      const height = image.naturalHeight * scale;
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const context = canvas.getContext('2d');
      context?.drawImage(image, (size - width) / 2, (size - height) / 2, width, height);
      setAvatar(canvas.toDataURL('image/webp', 0.82));
      URL.revokeObjectURL(objectUrl);
    };
    image.onerror = () => URL.revokeObjectURL(objectUrl);
    image.src = objectUrl;
  };

  return (
    <div
      id="settings-modal-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs animate-in fade-in duration-150"
    >
      <div
        id="settings-modal"
        className="w-full max-w-2xl h-[560px] bg-white dark:bg-[#1C1F27] rounded-3xl shadow-2xl border border-[#E8EAF0] dark:border-[#2A2E3D] overflow-hidden flex flex-col md:flex-row animate-in zoom-in-95 duration-150"
      >
        {/* Left Settings Navigation Rail */}
        <aside className="w-full md:w-56 bg-[#F7F8FC] dark:bg-[#14161C] border-r border-[#E8EAF0] dark:border-[#2A2E3D] p-3 flex md:flex-col justify-between flex-shrink-0">
          <div>
            <div className="flex items-center gap-2 px-3 py-2 mb-2">
              <h3 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                {t(language, 'settings')}
              </h3>
            </div>

            <nav className="space-y-1">
              {sections.map((sec) => {
                const Icon = sec.icon;
                const isActive = activeSection === sec.id;
                return (
                  <button
                    key={sec.id}
                    onClick={() => setActiveSection(sec.id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-colors text-left ${
                      isActive
                        ? 'bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]'
                        : 'text-[#74798C] dark:text-[#9DA3B4] hover:bg-[#F4F5F8] dark:hover:bg-[#232630] hover:text-[#1E2230]'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    <span>{sec.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>
        </aside>

        {/* Right Content Area */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-white dark:bg-[#1C1F27]">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-[#E8EAF0] dark:border-[#2A2E3D]">
            <h4 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">
              {sections.find((s) => s.id === activeSection)?.label}
            </h4>
            <button
              onClick={onClose}
              className="p-1.5 rounded-xl text-[#74798C] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#232630]"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Body content */}
          <div className="flex-1 overflow-y-auto p-6 space-y-5 text-sm">
            {/* 1. Language & Translation Section */}
            {activeSection === 'language' && (
              <div className="space-y-5">
                {/* Preferred Language */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {copy(language, 'Preferred Language')}
                  </label>
                  <select
                    id="settings-language-select"
                    value={settings.preferredLanguage}
                    onChange={(e) =>
                      onUpdateSettings({ preferredLanguage: e.target.value as LanguageCode })
                    }
                    className="w-full h-11 px-3 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
                  >
                    {CHAT_LANGUAGES.map((lang) => (
                      <option key={lang.code} value={lang.code}>
                        {lang.flag} {lang.name} — {lang.nativeName}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] pt-0.5">
                    {copy(language, 'Messages in other languages will automatically appear in your preferred language.')}
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {copy(language, 'Interface Language')}
                  </label>
                  <select
                    id="settings-interface-language-select"
                    value={settings.interfaceLanguage}
                    onChange={(e) => onUpdateSettings({ interfaceLanguage: e.target.value as LanguageCode })}
                    className="w-full h-11 px-3 bg-[#F4F5F8] dark:bg-[#232630] text-sm text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
                  >
                    {CHAT_LANGUAGES.map((lang) => (
                      <option key={lang.code} value={lang.code}>
                        {lang.flag} {lang.name} — {lang.nativeName}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] pt-0.5">
                    {copy(language, 'Controls, menus, and system messages use this language.')}
                  </p>
                </div>

                {/* Automatic Translation Toggle */}
                <div className="flex items-center justify-between p-3.5 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 border border-[#E8EAF0] dark:border-[#2A2E3D]">
                  <div>
                    <h5 className="font-semibold text-xs text-[#1E2230] dark:text-[#F5F6FA]">
                      {copy(language, 'Automatic Translation')}
                    </h5>
                    <p className="text-xs text-[#74798C] dark:text-[#9DA3B4]">
                      {copy(language, 'Translate foreign incoming messages instantly')}
                    </p>
                  </div>
                  <button
                    onClick={() =>
                      onUpdateSettings({ autoTranslate: !settings.autoTranslate })
                    }
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      settings.autoTranslate ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        settings.autoTranslate ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>

                {/* Translation Tone */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {copy(language, 'Translation Tone')}
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    {(['natural', 'formal', 'friendly'] as const).map((tone) => (
                      <button
                        key={tone}
                        onClick={() => onUpdateSettings({ translationTone: tone })}
                        className={`p-2.5 rounded-xl border text-xs font-semibold capitalize transition-all ${
                          settings.translationTone === tone
                            ? 'border-[#2563EB] bg-[#EFF6FF] dark:bg-[#2563EB]/20 text-[#2563EB]'
                            : 'border-[#E8EAF0] dark:border-[#2A2E3D] text-[#74798C] hover:bg-[#F7F8FC]'
                        }`}
                      >
                        {copy(language, tone)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* 2. Profile Section */}
            {activeSection === 'profile' && (
              <div className="space-y-4">
                <div className="flex items-center gap-4">
                  <div className="relative">
                    <img
                      src={avatar}
                      alt={currentUser.name}
                      className="w-16 h-16 rounded-full object-cover ring-2 ring-[#2563EB]"
                      referrerPolicy="no-referrer"
                    />
                    <button type="button" onClick={() => avatarInputRef.current?.click()} aria-label="Change avatar" className="absolute -right-1 -bottom-1 flex h-7 w-7 items-center justify-center rounded-full border-2 border-white bg-[#2563EB] text-white shadow-sm hover:bg-[#1D4ED8] dark:border-[#1C1F27]">
                      <Camera className="h-3.5 w-3.5" />
                    </button>
                    <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={handleAvatarChange} />
                  </div>
                  <div>
                    <h5 className="font-bold text-sm text-[#1E2230] dark:text-[#F5F6FA]">
                      {currentUser.name}
                    </h5>
                    <p className="text-xs text-[#74798C]">{currentUser.email}</p>
                    <button type="button" onClick={() => avatarInputRef.current?.click()} className="mt-1 text-xs font-semibold text-[#2563EB] hover:underline">{language === 'vi' ? 'Thay ảnh đại diện' : 'Change profile photo'}</button>
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {copy(language, 'Display Name')}
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full h-10 px-3 bg-[#F4F5F8] dark:bg-[#232630] text-xs text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {copy(language, 'Bio')}
                  </label>
                  <textarea
                    rows={2}
                    value={bio}
                    onChange={(e) => setBio(e.target.value)}
                    className="w-full p-2.5 bg-[#F4F5F8] dark:bg-[#232630] text-xs text-[#1E2230] dark:text-[#F5F6FA] rounded-xl border border-transparent focus:border-[#2563EB]/40 focus:outline-none resize-none"
                  />
                </div>

                <button
                  onClick={handleSaveProfile}
                  className="px-4 py-2 bg-[#2563EB] text-white text-xs font-semibold rounded-xl hover:bg-[#1D4ED8] shadow-sm transition-all"
                >
                  {copy(language, 'Save Profile Changes')}
                </button>
              </div>
            )}

            {/* 3. Notifications */}
            {activeSection === 'notifications' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between p-3.5 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 border border-[#E8EAF0] dark:border-[#2A2E3D]">
                  <div>
                    <h5 className="font-semibold text-xs text-[#1E2230] dark:text-[#F5F6FA]">
                      {copy(language, 'Sound Notifications')}
                    </h5>
                    <p className="text-xs text-[#74798C]">{copy(language, 'Play audio chime on incoming messages')}</p>
                  </div>
                  <button
                    onClick={() =>
                      onUpdateSettings({ soundEnabled: !settings.soundEnabled })
                    }
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      settings.soundEnabled ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        settings.soundEnabled ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
                <div className="flex items-center justify-between p-3.5 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 border border-[#E8EAF0] dark:border-[#2A2E3D]">
                  <div><h5 className="font-semibold text-xs text-[#1E2230] dark:text-[#F5F6FA]">Push notifications</h5><p className="text-xs text-[#74798C]">Receive new-message alerts while LinguaFlow is in the background.</p></div>
                  <button disabled={pushBusy} onClick={() => void togglePush()} aria-pressed={pushEnabled} className={`relative w-11 h-6 rounded-full transition-colors disabled:opacity-50 ${pushEnabled ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'}`}><span className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${pushEnabled ? 'translate-x-5' : ''}`}/></button>
                </div>
                {pushError && <p role="alert" className="px-1 text-xs text-rose-600">{pushError}</p>}
              </div>
            )}

            {/* 4. Privacy */}
            {activeSection === 'privacy' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between p-3.5 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 border border-[#E8EAF0] dark:border-[#2A2E3D]">
                  <div>
                    <h5 className="font-semibold text-xs text-[#1E2230] dark:text-[#F5F6FA]">
                      {copy(language, 'Read Receipts')}
                    </h5>
                    <p className="text-xs text-[#74798C]">{copy(language, 'Let contacts see when you read their messages')}</p>
                  </div>
                  <button
                    onClick={() =>
                      onUpdateSettings({ readReceipts: !settings.readReceipts })
                    }
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      settings.readReceipts ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        settings.readReceipts ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* 6. Personal assistant chatbot */}
            {activeSection === 'ai' && (
              <div className="space-y-3">
                <div className="flex items-center gap-3 rounded-2xl bg-gradient-to-r from-[#EFF6FF] to-violet-50 p-4 dark:from-[#2563EB]/20 dark:to-violet-500/15">
                  <div className="flex h-12 w-12 flex-none items-center justify-center rounded-2xl bg-white p-2 shadow-md shadow-[#2563EB]/20 dark:bg-[#1C1F27]">
                    <img src="/brand/brand-mark.svg" alt="LinguaFlow" className="h-full w-full object-contain" />
                  </div>
                  <div className="min-w-0">
                    <h5 className="text-sm font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                      {copy(language, 'AI Smart Assistance')}
                    </h5>
                    <p className="mt-0.5 text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">
                      {copy(language, 'Enable quick rewrites, tone polish & auto grammar')}
                    </p>
                  </div>
                </div>
                <div className="flex items-center justify-between p-3.5 rounded-2xl bg-[#F7F8FC] dark:bg-[#232630]/60 border border-[#E8EAF0] dark:border-[#2A2E3D]">
                  <div>
                    <h5 className="font-semibold text-xs text-[#1E2230] dark:text-[#F5F6FA]">
                      {copy(language, 'Enable personal assistant')}
                    </h5>
                    <p className="text-xs text-[#74798C]">{copy(language, 'Show the assistant in your conversations')}</p>
                  </div>
                  <button
                    onClick={() => {
                      const enabling = !settings.aiSmartAssistance;
                      onUpdateSettings({ aiSmartAssistance: enabling });
                      // Ask on the way in, and only while something is still
                      // unanswered. Re-prompting someone who already decided
                      // trains them to dismiss the dialog without reading it.
                      if (enabling && onUpdateAgentConsents && !agentConsentsAnswered) {
                        setIsConsentDialogOpen(true);
                      }
                    }}
                    className={`relative w-11 h-6 rounded-full transition-colors ${
                      settings.aiSmartAssistance ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        settings.aiSmartAssistance ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>

                {/* Permissions. Deliberately below the switch above and visually
                    separated: that switch only decides whether the assistant is
                    shown, while these decide what it is allowed to do with the
                    account's data. Rendered even when the assistant is hidden so
                    a permission can be withdrawn without turning it back on. */}
                <div className="pt-2">
                  <h5 className="px-1 text-xs font-bold text-[#1E2230] dark:text-[#F5F6FA]">
                    {assistantText('Assistant permissions', 'Quyền bạn cấp cho trợ lý')}
                  </h5>
                  <p className="mt-1 px-1 text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">
                    {assistantText(
                      'Each permission is independent, off by default, and can be withdrawn at any time.',
                      'Mỗi quyền độc lập với nhau, mặc định đều tắt, và rút lại được bất cứ lúc nào.',
                    )}
                  </p>
                  <div className="mt-2 space-y-2">
                    {CONSENT_COPY.map(({ scope, title, detail, englishTitle, englishDetail }) => {
                      const granted = agentConsents[scope] === true;
                      const localizedTitle = assistantText(englishTitle, title);
                      const localizedDetail = assistantText(englishDetail, detail);
                      return (
                        <div
                          key={scope}
                          className="flex items-start justify-between gap-3 rounded-2xl border border-[#E8EAF0] bg-[#F7F8FC] p-3.5 dark:border-[#2A2E3D] dark:bg-[#232630]/60"
                        >
                          <div className="min-w-0">
                            <h6 className="text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">
                              {localizedTitle}
                            </h6>
                            <p className="mt-0.5 text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">
                              {localizedDetail}
                            </p>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={granted}
                            aria-label={localizedTitle}
                            disabled={!onUpdateAgentConsents}
                            onClick={() => onUpdateAgentConsents?.({ [scope]: !granted })}
                            className={`relative mt-0.5 w-11 h-6 flex-none rounded-full transition-colors disabled:opacity-50 ${
                              granted ? 'bg-[#2563EB]' : 'bg-[#CED2DE] dark:bg-[#3A3F50]'
                            }`}
                          >
                            <span
                              className={`absolute top-1 left-1 h-4 w-4 rounded-full bg-white transition-transform ${
                                granted ? 'translate-x-5' : ''
                              }`}
                            />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>

      <AssistantConsentDialog
        isOpen={isConsentDialogOpen}
        items={CONSENT_COPY.map(({ scope, title, detail, englishTitle, englishDetail }) => ({
          scope,
          title: assistantText(englishTitle, title),
          detail: assistantText(englishDetail, detail),
        }))}
        granted={agentConsents}
        onCancel={() => setIsConsentDialogOpen(false)}
        onConfirm={(changes) => {
          onUpdateAgentConsents?.(changes);
          setIsConsentDialogOpen(false);
        }}
      />
    </div>
  );
};
