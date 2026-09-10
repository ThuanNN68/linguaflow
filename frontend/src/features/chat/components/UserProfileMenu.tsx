import React, { useRef, useEffect } from 'react';
import { User, AppSettings } from '../types';
import { LogOut, Settings, User as UserIcon, Globe, Shield, Sparkles, Moon, Sun } from 'lucide-react';
import { tx } from '../i18n';

const LANGUAGE_LABELS: Record<User['nativeLanguage'], string> = {
  en: 'English (US)', vi: 'Tiếng Việt', ja: '日本語', ko: '한국어', zh: '中文',
  es: 'Español', fr: 'Français', de: 'Deutsch', th: 'ไทย', id: 'Bahasa Indonesia',
  pt: 'Português', ru: 'Русский', ar: 'العربية', hi: 'हिन्दी',
};

interface UserProfileMenuProps {
  user: User;
  settings: AppSettings;
  isOpen: boolean;
  onClose: () => void;
  onOpenSettings: () => void;
  onToggleTheme: () => void;
  onLogout: () => void;
  isLoggingOut?: boolean;
}

export const UserProfileMenu: React.FC<UserProfileMenuProps> = ({
  user,
  settings,
  isOpen,
  onClose,
  onOpenSettings,
  onToggleTheme,
  onLogout,
  isLoggingOut = false,
}) => {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      ref={menuRef}
      id="user-profile-popover"
      className="absolute bottom-4 left-18 z-50 w-72 bg-white dark:bg-[#1C1F27] rounded-2xl shadow-xl border border-[#E8EAF0] dark:border-[#2A2E3D] p-3 animate-in fade-in zoom-in-95 duration-150"
    >
      {/* Header Info */}
      <div className="flex items-center gap-3 p-2.5 rounded-xl bg-[#F7F8FC] dark:bg-[#232630]/60 mb-2">
        <div className="relative">
          <img
            src={user.avatar}
            alt={user.name}
            className="w-11 h-11 rounded-full object-cover ring-2 ring-white dark:ring-[#1C1F27]"
            referrerPolicy="no-referrer"
          />
          <span className="absolute bottom-0 right-0 w-3 h-3 bg-emerald-500 rounded-full ring-2 ring-white dark:ring-[#1C1F27]" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold text-[#1E2230] dark:text-[#F5F6FA] truncate">
            {user.name}
          </h4>
          <p className="text-xs text-[#74798C] dark:text-[#9DA3B4] truncate">
            {user.email}
          </p>
          <div className="flex items-center gap-1.5 mt-1">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-[#2563EB]/10 text-[#2563EB] dark:bg-[#2563EB]/20">
              <Globe className="w-2.5 h-2.5" />
              {LANGUAGE_LABELS[settings.interfaceLanguage]}
            </span>
          </div>
        </div>
      </div>

      {/* Quick Action Links */}
      <div className="space-y-0.5 text-xs text-[#1E2230] dark:text-[#E2E5F0]">
        <button
          id="profile-menu-profile-btn"
          onClick={() => {
            onOpenSettings();
            onClose();
          }}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left"
        >
          <UserIcon className="w-4 h-4 text-[#74798C]" />
          <span>{tx(settings.interfaceLanguage, 'My Profile')}</span>
        </button>

        <button
          id="profile-menu-theme-btn"
          onClick={onToggleTheme}
          className="w-full flex items-center justify-between px-3 py-2 rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left"
        >
          <div className="flex items-center gap-2.5">
            {settings.theme === 'dark' ? (
              <Sun className="w-4 h-4 text-amber-500" />
            ) : (
              <Moon className="w-4 h-4 text-[#74798C]" />
            )}
            <span>{tx(settings.interfaceLanguage, 'Appearance')}</span>
          </div>
          <span className="text-[11px] text-[#74798C] uppercase font-medium">
            {tx(settings.interfaceLanguage, settings.theme === 'dark' ? 'Dark' : 'Light')}
          </span>
        </button>

        <button
          id="profile-menu-settings-btn"
          onClick={() => {
            onOpenSettings();
            onClose();
          }}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors text-left"
        >
          <Settings className="w-4 h-4 text-[#74798C]" />
          <span>{tx(settings.interfaceLanguage, 'Language & Settings')}</span>
        </button>
      </div>

      <div className="my-2 border-t border-[#E8EAF0] dark:border-[#2A2E3D]" />

      <button
        id="profile-menu-logout-btn"
        onClick={onLogout}
        disabled={isLoggingOut}
        className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 disabled:opacity-60 disabled:cursor-wait transition-colors text-left"
      >
        <LogOut className="w-4 h-4" />
        <span>{tx(settings.interfaceLanguage, isLoggingOut ? 'Logging out…' : 'Log out')}</span>
      </button>
    </div>
  );
};
