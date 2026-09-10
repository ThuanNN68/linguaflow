export type LanguageCode = 'EN' | 'VI' | 'JA' | 'ZH' | 'KO' | 'FR' | 'DE' | 'ES' | 'TH' | 'ID' | 'PT' | 'RU' | 'AR' | 'HI';

export type AdminInterfaceLanguage = 'vi' | 'en' | 'ja' | 'zh' | 'ko' | 'fr' | 'de' | 'es';

export type LanguagePair = 'EN → VI' | 'VI → EN' | 'EN → EN' | 'VI → VI' | 'EN → JA' | 'JA → VI';

export interface MetricCardData {
  id: string;
  title: string;
  value: string | number;
  unit?: string;
  change?: string;
  changeType?: 'positive' | 'negative' | 'neutral';
  description: string;
  iconName: string;
  badge?: string;
  badgeColor?: 'blue' | 'emerald' | 'amber' | 'rose' | 'indigo' | 'purple';
}

export interface LanguagePairStat {
  pair: LanguagePair;
  count: number;
  percentage: number;
  p50: number; // in ms
  p95: number; // in ms
  avgInputTokens: number;
  avgOutputTokens: number;
  successRate: number; // 0-100%
  fallbackRate: number; // 0-100%
}

export interface ModelUsageStat {
  id: string;
  name: string;
  provider: string;
  servedCount: number;
  percentage: number;
  avgLatency: number;
  status: 'active' | 'fallback' | 'standby' | 'degraded';
  costPer1kTokens: number;
  color: string;
}

export interface TimeSeriesDataPoint {
  time: string;
  translations: number;
  inputTokens: number;
  outputTokens: number;
  p50Latency: number;
  p95Latency: number;
  fallbackCount: number;
}

export interface RequestLog {
  id: string;
  timestamp: string;
  pair: LanguagePair;
  model: string;
  isFallback: boolean;
  status: 'success' | 'warning' | 'error';
  latency: number;
  inputTokens: number;
  outputTokens: number;
  sourcePreview: string;
  targetPreview: string;
  matchedGlossaryTerms?: string[];
}

export interface TermItem {
  id: string;
  sourceTerm: string;
  targetTerm: string;
  sourceLang: string;
  targetLang: string;
  domain: string;
  audience: string;
  keepVerbatim: boolean;
  status: 'active' | 'retired';
  createdAt: string;
  updatedAt: string;
}

export type SuggestionStatus = 'pending' | 'approved' | 'rejected';

export interface TranslationSuggestion {
  id: string;
  sourceText: string;
  currentAiTranslation: string;
  suggestedTranslation: string;
  sourceLang: LanguageCode;
  targetLang: LanguageCode;
  user: string;
  userEmail: string;
  reason: string;
  status: SuggestionStatus;
  submittedAt: string;
  reviewedAt?: string;
  reviewedBy?: string;
  reviewNotes?: string;
  domain?: string;
  autoAddToGlossary?: boolean;
}

export type AdminTab = 'analytics' | 'feedback' | 'glossary' | 'suggestions' | 'users' | 'chat';

export type TimeRangeFilter = '24h' | '7d' | '30d' | 'all';

export interface FeedbackVoteSummary {
  up: number;
  down: number;
  total: number;
  up_rate: number;
  ratings: Record<string, number>;
}

export interface FeedbackReviewEntry {
  entry_type: 'vote' | 'edit';
  original_text: string;
  translated_text: string;
  source_language: string;
  target_language: string;
  model: string;
  vote: 'up' | 'down' | 'other' | null;
  rating: number | null;
  user_correction: string | null;
  created_at: string;
  occurrence_count: number;
}

export interface FeedbackOverview {
  votes: FeedbackVoteSummary;
  edited_translations: number;
  review_entries: FeedbackReviewEntry[];
  shared_total: number;
  withheld_total: number;
}
