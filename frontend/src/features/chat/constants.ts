import type { AppSettings, LanguageOption } from "./types";

export const CHAT_LANGUAGES: LanguageOption[] = [
  { code: "en", name: "English", nativeName: "English (US)", flag: "🇺🇸" },
  { code: "vi", name: "Vietnamese", nativeName: "Tiếng Việt", flag: "🇻🇳" },
  { code: "ja", name: "Japanese", nativeName: "日本語", flag: "🇯🇵" },
  { code: "ko", name: "Korean", nativeName: "한국어", flag: "🇰🇷" },
  { code: "zh", name: "Chinese", nativeName: "中文", flag: "🇨🇳" },
  { code: "es", name: "Spanish", nativeName: "Español", flag: "🇪🇸" },
  { code: "fr", name: "French", nativeName: "Français", flag: "🇫🇷" },
  { code: "de", name: "German", nativeName: "Deutsch", flag: "🇩🇪" },
  { code: "th", name: "Thai", nativeName: "ภาษาไทย", flag: "🇹🇭" },
  { code: "id", name: "Indonesian", nativeName: "Bahasa Indonesia", flag: "🇮🇩" },
  { code: "pt", name: "Portuguese", nativeName: "Português", flag: "🇵🇹" },
  { code: "ru", name: "Russian", nativeName: "Русский", flag: "🇷🇺" },
  { code: "ar", name: "Arabic", nativeName: "العربية", flag: "🇸🇦" },
  { code: "hi", name: "Hindi", nativeName: "हिन्दी", flag: "🇮🇳" },
];

export const DEFAULT_CHAT_SETTINGS: AppSettings = {
  preferredLanguage: "en",
  interfaceLanguage: "en",
  autoTranslate: true,
  showOriginalByDefault: false,
  translationTone: "natural",
  theme: "light",
  soundEnabled: true,
  readReceipts: true,
  aiSmartAssistance: true,
  offlineModeSimulation: false,
};

export const DEFAULT_CALL_RING_TIMEOUT_SECONDS = 45;
