import React, { useCallback, useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { AnalyticsView } from './components/analytics/AnalyticsView';
import { FeedbackView } from './components/feedback/FeedbackView';
import { GlossaryView } from './components/glossary/GlossaryView';
import { SuggestionsView } from './components/suggestions/SuggestionsView';
import { AgentTranslationResult, LiveChatView } from './components/chat/LiveChatView';
import { ToastContainer, ToastMessage } from './components/Toast';
import { UsersView } from './components/users/UsersView';
import { API_BASE } from '../../config/env';
import {
  AdminTab,
  TimeRangeFilter,
  TermItem,
  TranslationSuggestion,
  LanguagePair,
  RequestLog,
  LanguageCode,
  MetricCardData,
  LanguagePairStat,
  ModelUsageStat,
  TimeSeriesDataPoint,
  FeedbackOverview,
  AdminInterfaceLanguage,
} from './types';

type ApiPair = { count: number; p50_ms: number; p95_ms: number; avg_input_tokens: number; avg_output_tokens: number };
type ApiStats = { total_attempts: number; outcomes?: Record<string, number>; fallback_rate: number; input_tokens: number; output_tokens: number; estimated_cost_usd?: number; cost_coverage_rate?: number; total_ms_p50: number; total_ms_p95: number; models_served?: Record<string, number>; language_pairs?: Record<string, ApiPair> };
type ApiAttempt = { id:string; created_at:string; source_language:string; target_language:string; model:string; outcome:string; fallback_reason:string; total_ms:number; input_tokens:number; output_tokens:number };
type ApiTimeSeriesPoint = { time:string; translations:number; input_tokens:number; output_tokens:number; p50_latency:number; p95_latency:number; fallback_count:number };
type ApiGlossaryEntry = { id:string; source_term:string; target_term:string; source_language:string; target_language:string; domain:string; audience:string; keep_verbatim:boolean; status:string; created_at:string; updated_at:string };
type ApiProposal = { id:string; source_term:string; target_term:string; source_language:string; target_language:string; domain:string; audience:string; keep_verbatim:boolean; distinct_user_count:number; rationale:string; reject_reason:string; status:'pending'|'approved'|'rejected'; created_at:string };
type ApiUser = { email:string; display_name?:string | null; username?:string | null; role:string; interface_language?:string | null };
type AdminTheme = 'light' | 'dark';

const apiBase = () => API_BASE;

async function adminRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = window.localStorage.getItem('access_token') ?? window.sessionStorage.getItem('access_token');
  if (!token) throw new Error('admin_auth_required');

  const response = await fetch(`${apiBase()}/api/v1${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) throw new Error(`admin_api_${response.status}`);
  return response.json() as Promise<T>;
}

const METRIC_DEFINITIONS: MetricCardData[] = [
  { id: 'total_translations', title: 'Tổng số lần dịch', value: 0, unit: 'lượt', description: 'Tổng lượt yêu cầu dịch trong khoảng thời gian đã chọn', iconName: 'Languages' },
  { id: 'fallback_rate', title: 'Tỷ lệ fallback', value: 0, description: 'Tỷ lệ yêu cầu chuyển sang đường dịch dự phòng', iconName: 'ShieldAlert' },
  { id: 'input_tokens', title: 'Input Tokens', value: 0, unit: 'tokens', description: 'Tổng token đầu vào thực tế', iconName: 'ArrowDownToLine' },
  { id: 'output_tokens', title: 'Output Tokens', value: 0, unit: 'tokens', description: 'Tổng token đầu ra thực tế', iconName: 'ArrowUpFromLine' },
  { id: 'estimated_ai_cost', title: 'Chi phí AI ước tính', value: 0, unit: 'USD', description: 'Ước tính từ token của các model đã nhận diện', iconName: 'Gauge' },
  { id: 'success_rate', title: 'Tỷ lệ dịch thành công', value: 0, description: 'Tỷ lệ yêu cầu hoàn tất trên đường dịch chính', iconName: 'Timer' },
  { id: 'p50_latency', title: 'Độ trễ P50', value: 0, unit: 'ms', description: 'Độ trễ phân vị 50', iconName: 'Gauge' },
  { id: 'p95_latency', title: 'Độ trễ P95', value: 0, unit: 'ms', description: 'Độ trễ phân vị 95', iconName: 'Timer' },
];

function mapGlossaryEntries(glossary: ApiGlossaryEntry[]): TermItem[] {
  return glossary.map((entry) => ({
    id: entry.id,
    sourceTerm: entry.source_term,
    targetTerm: entry.target_term,
    sourceLang: entry.source_language.toLowerCase(),
    targetLang: entry.target_language.toLowerCase(),
    domain: entry.domain,
    audience: entry.audience,
    keepVerbatim: entry.keep_verbatim,
    status: entry.status === 'active' ? 'active' : 'retired',
    createdAt: entry.created_at,
    updatedAt: entry.updated_at,
  }));
}

export default function App() {
  const [currentTab, setCurrentTab] = useState<AdminTab>('analytics');
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>('7d');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [adminTheme, setAdminTheme] = useState<AdminTheme>('light');
  const [adminLanguage, setAdminLanguage] = useState<AdminInterfaceLanguage>('vi');
  const [adminProfile, setAdminProfile] = useState({ name: 'Quản trị viên', email: 'admin@test.com', role: 'admin' });

  // Core Data States
  const [metrics, setMetrics] = useState<MetricCardData[]>([]);
  const [languagePairs, setLanguagePairs] = useState<LanguagePairStat[]>([]);
  const [aiModels, setAiModels] = useState<ModelUsageStat[]>([]);
  const [timeSeriesData, setTimeSeriesData] = useState<TimeSeriesDataPoint[]>([]);
  const [glossaryLanguageCodes, setGlossaryLanguageCodes] = useState<string[]>([]);
  const [requestLogs, setRequestLogs] = useState<RequestLog[]>([]);
  const [feedbackOverview, setFeedbackOverview] = useState<FeedbackOverview | null>(null);
  const [terms, setTerms] = useState<TermItem[]>([]);
  const [suggestions, setSuggestions] = useState<TranslationSuggestion[]>([]);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const savedTheme = window.localStorage.getItem('linguaflow_admin_theme');
      const savedLanguage = window.localStorage.getItem('linguaflow_admin_language');
      if (savedTheme === 'light' || savedTheme === 'dark') setAdminTheme(savedTheme);
      if (savedLanguage === 'vi' || savedLanguage === 'en') setAdminLanguage(savedLanguage);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    window.localStorage.setItem('linguaflow_admin_theme', adminTheme);
    window.localStorage.setItem('linguaflow_admin_language', adminLanguage);
    document.documentElement.lang = adminLanguage;
    document.documentElement.classList.toggle('dark', adminTheme === 'dark');
  }, [adminLanguage, adminTheme]);

  const clearServerData = useCallback(() => {
    setMetrics([]);
    setLanguagePairs([]);
    setAiModels([]);
    setTimeSeriesData([]);
    setGlossaryLanguageCodes([]);
    setRequestLogs([]);
    setFeedbackOverview(null);
    setTerms([]);
    setSuggestions([]);
  }, []);

  const loadAdminData = useCallback(async () => {
    const token = window.localStorage.getItem('access_token') ?? window.sessionStorage.getItem('access_token');
    if (!token) {
      clearServerData();
      return;
    }

    setIsRefreshing(true);
    const days = timeRange === '24h' ? 1 : timeRange === '7d' ? 7 : timeRange === '30d' ? 30 : null;
    const statsPath = days ? `/stats?days=${days}` : '/stats';

    try {
      const [stats, attempts, timeSeries, glossaryLanguages, glossary, pending, approved, rejected, profile, feedback] = await Promise.all([
        adminRequest<ApiStats>(statsPath),
        adminRequest<ApiAttempt[]>(`/stats/attempts${days ? `?days=${days}&limit=20` : '?limit=20'}`),
        adminRequest<ApiTimeSeriesPoint[]>(`/stats/timeseries${days ? `?days=${days}` : ''}`),
        adminRequest<string[]>('/languages'),
        adminRequest<ApiGlossaryEntry[]>('/admin/glossary?include_retired=true'),
        adminRequest<ApiProposal[]>('/admin/glossary/proposals?status=pending'),
        adminRequest<ApiProposal[]>('/admin/glossary/proposals?status=approved'),
        adminRequest<ApiProposal[]>('/admin/glossary/proposals?status=rejected'),
        adminRequest<ApiUser>('/auth/me'),
        adminRequest<FeedbackOverview>('/admin/feedback?limit=100'),
      ]);

        setAdminProfile({
          name: profile.display_name || profile.username || (adminLanguage === 'vi' ? 'Quản trị viên' : 'Administrator'),
          email: profile.email,
          role: profile.role,
        });

        setMetrics(METRIC_DEFINITIONS.map((metric) => {
          const neutralAttempts = (stats.outcomes?.timeout ?? 0) + (stats.outcomes?.passthrough ?? 0);
          const completedAttempts = Math.max(stats.total_attempts - neutralAttempts, 0);
          const successfulAttempts = (stats.outcomes?.llm ?? 0) + (stats.outcomes?.primary ?? 0);
          const values: Record<string, string | number> = {
            total_translations: stats.total_attempts,
            fallback_rate: `${(stats.fallback_rate * 100).toFixed(2)}%`,
            input_tokens: stats.input_tokens,
            output_tokens: stats.output_tokens,
            estimated_ai_cost: (stats.estimated_cost_usd ?? 0).toFixed(4),
            success_rate: `${(completedAttempts ? (successfulAttempts / completedAttempts) * 100 : 0).toFixed(2)}%`,
            p50_latency: stats.total_ms_p50,
            p95_latency: stats.total_ms_p95,
          };
          return { ...metric, value: values[metric.id] ?? 0, change: undefined, badge: undefined };
        }));
        const total = Math.max(stats.total_attempts || 0, 1);
        const pairTotal = Math.max(
          Object.values(stats.language_pairs ?? {}).reduce((sum, pair) => sum + pair.count, 0),
          1,
        );
        const neutralAttempts = (stats.outcomes?.timeout ?? 0) + (stats.outcomes?.passthrough ?? 0);
        const completedAttempts = Math.max(stats.total_attempts - neutralAttempts, 0);
        const successfulAttempts = (stats.outcomes?.llm ?? 0) + (stats.outcomes?.primary ?? 0);
        const completedSuccessRate = completedAttempts ? (successfulAttempts / completedAttempts) * 100 : 0;
        setLanguagePairs(Object.entries(stats.language_pairs ?? {}).map(([pair, value]) => ({
          pair: pair as LanguagePair,
          count: value.count,
          percentage: (value.count / pairTotal) * 100,
          p50: value.p50_ms,
          p95: value.p95_ms,
          avgInputTokens: value.avg_input_tokens,
          avgOutputTokens: value.avg_output_tokens,
          successRate: completedSuccessRate,
          fallbackRate: 100 - completedSuccessRate,
        })));
        setAiModels(Object.entries(stats.models_served ?? {}).map(([name, servedCount], index) => ({
          id: `model-${index}`,
          name,
          provider: 'LinguaFlow',
          servedCount,
          percentage: (servedCount / total) * 100,
          avgLatency: stats.total_ms_p50,
          status: 'active' as const,
          costPer1kTokens: 0,
          color: ['#2563EB', '#10B981', '#8B5CF6', '#F59E0B'][index % 4],
        })));
        setTimeSeriesData(timeSeries.map((point) => ({
          time: point.time,
          translations: point.translations,
          inputTokens: point.input_tokens,
          outputTokens: point.output_tokens,
          p50Latency: point.p50_latency,
          p95Latency: point.p95_latency,
          fallbackCount: point.fallback_count,
        })));
        setGlossaryLanguageCodes(glossaryLanguages.map((code) => code.toLowerCase()));
        // The operational log keeps same-language passes for diagnostics, but
        // this admin table is a translation history: show only work that
        // actually crosses languages.
        setRequestLogs(attempts.filter((attempt) => (
          attempt.source_language.toLowerCase() !== attempt.target_language.toLowerCase()
        )).map((attempt) => {
          const source = attempt.source_language.toUpperCase();
          const target = attempt.target_language.toUpperCase();
          const isSameLanguage = source === target;
          const isFallback = attempt.outcome === 'secondary'
            || attempt.outcome === 'original'
            || Boolean(attempt.fallback_reason);
          return {
            id: attempt.id,
            timestamp: new Date(attempt.created_at).toLocaleString('vi-VN'),
            pair: `${source} → ${target}` as LanguagePair,
            // The configured primary model did not produce this result. Showing
            // its name here would make a fallback look like a successful model
            // response, so surface the operational outcome instead.
            model: isSameLanguage ? '—' : isFallback ? 'Lỗi · Fallback' : attempt.model,
            isFallback,
            status: isFallback ? 'warning' as const : ['llm', 'passthrough'].includes(attempt.outcome) ? 'success' as const : 'error' as const,
            latency: attempt.total_ms,
            inputTokens: attempt.input_tokens,
            outputTokens: attempt.output_tokens,
            sourcePreview: isSameLanguage ? `Không cần dịch · ${source}` : `Yêu cầu dịch ${source} → ${target}`,
            targetPreview: isSameLanguage
              ? 'Người gửi và người nhận cùng ngôn ngữ.'
              : attempt.fallback_reason || `Kết quả: ${attempt.outcome}`,
          };
        }));
        setTerms(mapGlossaryEntries(glossary));
        setSuggestions([...pending, ...approved, ...rejected].map((proposal) => ({
          id: proposal.id,
          sourceText: proposal.source_term,
          currentAiTranslation: proposal.target_term,
          suggestedTranslation: proposal.target_term,
          sourceLang: proposal.source_language.toUpperCase() as LanguageCode,
          targetLang: proposal.target_language.toUpperCase() as LanguageCode,
          user: `${proposal.distinct_user_count} người dùng`,
          userEmail: '',
          reason: proposal.rationale,
          status: proposal.status,
          submittedAt: proposal.created_at,
          domain: proposal.domain,
          autoAddToGlossary: !proposal.keep_verbatim,
          reviewNotes: proposal.status === 'rejected' ? proposal.reject_reason : undefined,
        })));
        setFeedbackOverview(feedback);
    } catch {
      clearServerData();
      // Glossary is an independent admin surface. A stats, feedback or
      // proposal failure must not erase valid glossary data from the screen.
      try {
        const glossary = await adminRequest<ApiGlossaryEntry[]>('/admin/glossary?include_retired=true');
        setTerms(mapGlossaryEntries(glossary));
      } catch {
        setTerms([]);
      }
      setToasts([{ id: 'admin-api-error', message: 'Không thể đồng bộ dữ liệu quản trị từ máy chủ', type: 'error' }]);
    } finally {
      setIsRefreshing(false);
    }
  }, [adminLanguage, clearServerData, timeRange]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void loadAdminData());
    return () => window.cancelAnimationFrame(frame);
  }, [loadAdminData]);

  // Toast Helper
  const addToast = useCallback((message: string, type: 'success' | 'error' | 'info' = 'success') => {
    const id = `toast-${Date.now()}-${Math.random()}`;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // Refresh handler
  const handleRefresh = () => {
    void loadAdminData();
  };

  const glossaryPayload = (term: Omit<TermItem, 'id' | 'createdAt' | 'updatedAt'> | TermItem) => ({
    source_term: term.sourceTerm.trim(),
    target_term: term.targetTerm.trim(),
    source_language: term.sourceLang.toLowerCase(),
    target_language: term.targetLang.toLowerCase(),
    domain: term.domain.trim().slice(0, 50),
    audience: term.audience.trim().slice(0, 50),
    keep_verbatim: term.keepVerbatim,
  });

  // Glossary mutations deliberately reload the server state. A glossary entry
  // must be the backend's canonical row before the translation pipeline may
  // use it; optimistic-only entries were lost on reload and never applied.
  const handleAddTerm = async (newTermData: Omit<TermItem, 'id' | 'createdAt' | 'updatedAt'>) => {
    const entry = await adminRequest<ApiGlossaryEntry>('/admin/glossary', {
      method: 'POST', body: JSON.stringify(glossaryPayload(newTermData)),
    });
    if (newTermData.status === 'retired') {
      await adminRequest<ApiGlossaryEntry>(`/admin/glossary/${entry.id}`, { method: 'DELETE' });
    }
    await loadAdminData();
  };

  const handleUpdateTerm = async (id: string, updatedData: Partial<TermItem>) => {
    const current = terms.find((term) => term.id === id);
    if (!current) throw new Error('glossary_entry_not_found');
    const next = { ...current, ...updatedData };
    const statusChanged = current.status !== next.status;
    if (statusChanged) {
      await adminRequest<ApiGlossaryEntry>(
        next.status === 'active' ? `/admin/glossary/${id}/restore` : `/admin/glossary/${id}`,
        { method: next.status === 'active' ? 'POST' : 'DELETE' },
      );
    }
    const contentChanged = current.sourceTerm !== next.sourceTerm
      || current.targetTerm !== next.targetTerm
      || current.domain !== next.domain
      || current.audience !== next.audience
      || current.keepVerbatim !== next.keepVerbatim;
    if (contentChanged) {
      await adminRequest<ApiGlossaryEntry>(`/admin/glossary/${id}`, {
        method: 'PATCH', body: JSON.stringify(glossaryPayload(next)),
      });
    }
    await loadAdminData();
  };

  const handleDeleteTerm = async (id: string) => {
    await adminRequest<ApiGlossaryEntry>(`/admin/glossary/${id}`, { method: 'DELETE' });
    await loadAdminData();
  };

  const handlePermanentDeleteTerm = async (id: string) => {
    await adminRequest<ApiGlossaryEntry>(`/admin/glossary/${id}/permanent`, { method: 'DELETE' });
    await loadAdminData();
  };

  // Approving a proposal is the one backend operation that atomically marks
  // the proposal reviewed and creates the active glossary entry.
  const handleApproveSuggestion = async (id: string) => {
    const targetSug = suggestions.find((suggestion) => suggestion.id === id);
    if (!targetSug) throw new Error('glossary_proposal_not_found');
    await adminRequest<ApiGlossaryEntry>(`/admin/glossary/proposals/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ target_term: targetSug.suggestedTranslation, domain: targetSug.domain || '' }),
    });
    await loadAdminData();
  };

  const handleRejectSuggestion = async (id: string, reason?: string) => {
    await adminRequest<ApiProposal>(`/admin/glossary/proposals/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason?.trim() || 'Chưa phù hợp với quy chuẩn dịch hiện tại.' }),
    });
    await loadAdminData();
  };

  const handleCreateSuggestionFromChat = async (sugData: {
    sourceText: string;
    currentAiTranslation: string;
    suggestedTranslation: string;
    sourceLang: LanguageCode;
    targetLang: LanguageCode;
    user: string;
    userEmail: string;
    reason: string;
  }) => {
    await adminRequest<ApiProposal>('/admin/glossary/proposals', {
      method: 'POST',
      body: JSON.stringify({
        source_term: sugData.sourceText,
        target_term: sugData.suggestedTranslation,
        source_language: sugData.sourceLang.toLowerCase(),
        target_language: sugData.targetLang.toLowerCase(),
        domain: '',
        audience: '',
        keep_verbatim: false,
        rationale: sugData.reason,
      }),
    });
    await loadAdminData();
  };

  const handleAgentTranslation = useCallback(async ({
    originalText,
    sourceLang,
    targetLang,
  }: {
    originalText: string;
    sourceLang: LanguageCode;
    targetLang: LanguageCode;
  }): Promise<AgentTranslationResult> => {
    try {
      return await adminRequest<AgentTranslationResult>('/admin/translate', {
        method: 'POST',
        body: JSON.stringify({
          original_text: originalText,
          source_language: sourceLang.toLowerCase(),
          target_language: targetLang.toLowerCase(),
          translation_tone: 'natural',
        }),
      });
    } catch (error) {
      if (error instanceof Error && error.message === 'admin_api_504') {
        throw new Error('Translation agent phản hồi quá thời gian. Vui lòng thử lại.');
      }
      if (error instanceof Error && error.message === 'admin_api_502') {
        throw new Error('Translation agent đang không khả dụng. Vui lòng kiểm tra cấu hình BE.');
      }
      throw new Error('Không thể gọi Translation Agent từ backend.');
    }
  }, []);

  return (
    <div id="admin-app-shell" data-theme={adminTheme} className="min-h-screen overflow-x-hidden bg-[#F9FAFB] font-sans text-gray-900 antialiased">
      {/* Sidebar Navigation */}
      <Sidebar
        currentTab={currentTab}
        onTabChange={setCurrentTab}
        interfaceLanguage={adminLanguage}
        theme={adminTheme}
        onInterfaceLanguageChange={setAdminLanguage}
        onThemeChange={setAdminTheme}
        adminName={adminProfile.name}
        adminEmail={adminProfile.email}
        adminRole={adminProfile.role}
      />

      {/* Main Content Area */}
      <div
        className="flex min-h-screen min-w-0 flex-col bg-[#F9FAFB]"
        style={{ marginLeft: 72, width: 'calc(100% - 72px)' }}
      >
        {/* Global Header */}
        <Header
          currentTab={currentTab}
          interfaceLanguage={adminLanguage}
          timeRange={timeRange}
          onTimeRangeChange={setTimeRange}
          onRefresh={handleRefresh}
          isRefreshing={isRefreshing}
        />

        {/* Dynamic Body Content */}
        <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-4 sm:p-5 lg:p-6 2xl:px-8">
          {currentTab === 'analytics' && (
            <AnalyticsView
              metrics={metrics}
              languagePairs={languagePairs}
              aiModels={aiModels}
              timeSeriesData={timeSeriesData}
              requestLogs={requestLogs}
              timeRange={timeRange}
              onOpenLiveChat={() => setCurrentTab('chat')}
            />
          )}

          {currentTab === 'feedback' && (
            <FeedbackView
              overview={feedbackOverview}
              isLoading={isRefreshing}
              interfaceLanguage={adminLanguage === 'vi' ? 'vi' : 'en'}
            />
          )}

          {currentTab === 'glossary' && (
            <GlossaryView
              terms={terms}
              languageCodes={glossaryLanguageCodes}
              onAddTerm={handleAddTerm}
              onUpdateTerm={handleUpdateTerm}
              onDeleteTerm={handleDeleteTerm}
              onPermanentDeleteTerm={handlePermanentDeleteTerm}
              onNotify={addToast}
            />
          )}

          {currentTab === 'suggestions' && (
            <SuggestionsView
              suggestions={suggestions}
              onApprove={handleApproveSuggestion}
              onReject={handleRejectSuggestion}
              onNotify={addToast}
            />
          )}

          {currentTab === 'users' && <UsersView onNotify={addToast} />}

          {currentTab === 'chat' && (
            <LiveChatView
              onTranslate={handleAgentTranslation}
              onBackToAdmin={() => setCurrentTab('analytics')}
              onSubmitSuggestion={handleCreateSuggestionFromChat}
              onNotify={addToast}
            />
          )}
        </main>
      </div>

      {/* Toast Notification Container */}
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  );
}
