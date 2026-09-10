import type {
  LanguageCode,
  LanguageOption as SharedLanguageOption,
} from "@/shared/types/language";

export type AuthScreen = 'signin' | 'signup' | 'forgot-password' | 'language-onboarding';

export interface LanguageOption extends SharedLanguageOption {
  id: string;
  code: LanguageCode;
  sampleGreeting: string;
}

export interface UserProfile {
  name: string;
  email: string;
  preferredLanguage: string; // language id
  avatar?: string;
  role?: string;
}
