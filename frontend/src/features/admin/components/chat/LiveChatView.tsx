import React, { useState, useRef, useEffect } from 'react';
import {
  Send,
  Bot,
  ArrowRightLeft,
  Copy,
  Check,
  Lightbulb,
  ArrowLeft,
  BookOpen,
} from 'lucide-react';
import type { LanguageCode } from '../../types';

interface Message {
  id: string;
  sender: 'user' | 'bot';
  sourceText?: string;
  text: string;
  sourceLang: LanguageCode;
  targetLang: LanguageCode;
  model: string;
  latencyMs: number;
  inputTokens: number;
  outputTokens: number;
  matchedGlossaryTerms?: string[];
  isFallback?: boolean;
  fallbackReason?: string;
  timestamp: string;
}

export interface AgentTranslationResult {
  original_text: string;
  translated_text: string;
  source_language: string;
  target_language: string;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  is_fallback: boolean;
  fallback_reason: string;
  matched_glossary_terms: string[];
}

interface LiveChatViewProps {
  onTranslate: (input: {
    originalText: string;
    sourceLang: LanguageCode;
    targetLang: LanguageCode;
  }) => Promise<AgentTranslationResult>;
  onBackToAdmin: () => void;
  onSubmitSuggestion: (sug: {
    sourceText: string;
    currentAiTranslation: string;
    suggestedTranslation: string;
    sourceLang: LanguageCode;
    targetLang: LanguageCode;
    user: string;
    userEmail: string;
    reason: string;
  }) => Promise<void>;
  onNotify: (message: string, type?: 'success' | 'info' | 'error') => void;
}

export const LiveChatView: React.FC<LiveChatViewProps> = ({
  onTranslate,
  onBackToAdmin,
  onSubmitSuggestion,
  onNotify,
}) => {
  const [inputText, setInputText] = useState('');
  const [sourceLang, setSourceLang] = useState<LanguageCode>('EN');
  const [targetLang, setTargetLang] = useState<LanguageCode>('VI');
  const [isTranslating, setIsTranslating] = useState(false);
  const [copiedMsgId, setCopiedMsgId] = useState<string | null>(null);

  // Suggestion modal state from chat
  const [suggestingMessage, setSuggestingMessage] = useState<Message | null>(null);
  const [userSuggestionText, setUserSuggestionText] = useState('');
  const [userReason, setUserReason] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [messages, setMessages] = useState<Message[]>([]);

  const handleSwapLanguages = () => {
    const temp = sourceLang;
    setSourceLang(targetLang);
    setTargetLang(temp);
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTranslating]);

  const handleSend = async () => {
    if (!inputText.trim() || isTranslating) return;

    const userMessage: Message = {
      id: `m-${Date.now()}-u`,
      sender: 'user',
      text: inputText.trim(),
      sourceLang,
      targetLang,
      model: 'LinguaFlow Agent',
      latencyMs: 0,
      inputTokens: 0,
      outputTokens: 0,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMessage]);
    const originalText = inputText.trim();
    setInputText('');
    setIsTranslating(true);

    try {
      const result = await onTranslate({ originalText, sourceLang, targetLang });

      const botMessage: Message = {
        id: `m-${Date.now()}-b`,
        sender: 'bot',
        sourceText: originalText,
        text: result.translated_text,
        sourceLang: result.source_language.toUpperCase() as LanguageCode,
        targetLang: result.target_language.toUpperCase() as LanguageCode,
        model: result.model,
        latencyMs: result.latency_ms,
        inputTokens: result.input_tokens,
        outputTokens: result.output_tokens,
        matchedGlossaryTerms: result.matched_glossary_terms,
        isFallback: result.is_fallback,
        fallbackReason: result.fallback_reason,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, botMessage]);
      if (result.is_fallback) {
        onNotify('Agent đã dùng đường dịch dự phòng. Hãy kiểm tra lại kết quả.', 'info');
      }
    } catch (error) {
      setMessages((prev) => prev.filter((message) => message.id !== userMessage.id));
      setInputText(originalText);
      onNotify(
        error instanceof Error ? error.message : 'Không thể kết nối tới translation agent.',
        'error',
      );
    } finally {
      setIsTranslating(false);
    }
  };

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedMsgId(id);
    setTimeout(() => setCopiedMsgId(null), 2000);
  };

  const handleOpenSuggestModal = (msg: Message) => {
    setSuggestingMessage(msg);
    setUserSuggestionText(msg.text);
    setUserReason('');
  };

  const handleSubmitSuggestionForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!suggestingMessage || !userSuggestionText.trim()) return;

    try {
      await onSubmitSuggestion({
        sourceText: suggestingMessage.sourceText || suggestingMessage.text,
        currentAiTranslation: suggestingMessage.text,
        suggestedTranslation: userSuggestionText.trim(),
        sourceLang: suggestingMessage.sourceLang,
        targetLang: suggestingMessage.targetLang,
        user: 'Tester Quản trị viên',
        userEmail: 'admin.tester@linguaflow.ai',
        reason: userReason || 'Đề xuất từ phiên thử nghiệm trực tiếp trên giao diện Chat.',
      });
      onNotify('Đã gửi đề xuất chỉnh sửa đến Trung tâm Quản trị!', 'success');
      setSuggestingMessage(null);
    } catch {
      onNotify('Không thể lưu đề xuất vào máy chủ.', 'error');
    }
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-xs flex flex-col h-[calc(100vh-140px)] min-h-[580px] overflow-hidden">
      {/* Chat Top Bar */}
      <div className="p-4 border-b border-gray-200 bg-gray-50/50 flex flex-wrap items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-3">
          <button
            id="btn-back-from-chat"
            onClick={onBackToAdmin}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white hover:bg-gray-50 text-gray-700 text-xs font-medium border border-gray-300 shadow-xs transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Quay lại Quản trị</span>
          </button>

          <div className="hidden sm:block h-5 w-px bg-gray-200" />

          {/* Language Pair Selector */}
          <div className="flex items-center gap-1.5 bg-white p-1 rounded-lg border border-gray-200 shadow-xs">
            <select
              value={sourceLang}
              onChange={(e) => setSourceLang(e.target.value as LanguageCode)}
              className="py-1 px-2 text-xs font-semibold text-gray-900 bg-transparent outline-hidden cursor-pointer"
            >
              <option value="EN">English (EN)</option>
              <option value="VI">Tiếng Việt (VI)</option>
              <option value="JA">日本語 (JA)</option>
              <option value="ZH">中文 (ZH)</option>
              <option value="KO">한국어 (KO)</option>
              <option value="FR">Français (FR)</option>
              <option value="DE">Deutsch (DE)</option>
              <option value="ES">Español (ES)</option>
              <option value="TH">ไทย (TH)</option>
              <option value="ID">Bahasa Indonesia (ID)</option>
              <option value="PT">Português (PT)</option>
              <option value="RU">Русский (RU)</option>
              <option value="AR">العربية (AR)</option>
              <option value="HI">हिन्दी (HI)</option>
            </select>

            <button
              onClick={handleSwapLanguages}
              className="p-1 text-gray-400 hover:text-blue-600 rounded-md hover:bg-gray-50 transition-colors"
              title="Đảo ngược cặp ngôn ngữ"
            >
              <ArrowRightLeft className="w-3.5 h-3.5" />
            </button>

            <select
              value={targetLang}
              onChange={(e) => setTargetLang(e.target.value as LanguageCode)}
              className="py-1 px-2 text-xs font-semibold text-blue-600 bg-transparent outline-hidden cursor-pointer"
            >
              <option value="VI">Tiếng Việt (VI)</option>
              <option value="EN">English (EN)</option>
              <option value="JA">日本語 (JA)</option>
              <option value="ZH">中文 (ZH)</option>
              <option value="KO">한국어 (KO)</option>
              <option value="FR">Français (FR)</option>
              <option value="DE">Deutsch (DE)</option>
              <option value="ES">Español (ES)</option>
              <option value="TH">ไทย (TH)</option>
              <option value="ID">Bahasa Indonesia (ID)</option>
              <option value="PT">Português (PT)</option>
              <option value="RU">Русский (RU)</option>
              <option value="AR">العربية (AR)</option>
              <option value="HI">हिन्दी (HI)</option>
            </select>
          </div>
        </div>

      </div>

      {/* Messages Stream Area */}
      <div className="flex-1 overflow-y-auto space-y-4 bg-gradient-to-b from-slate-50/80 to-white p-4 sm:p-6">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-3 max-w-2xl ${
              msg.sender === 'user' ? 'ml-auto flex-row-reverse' : 'mr-auto'
            }`}
          >
            {msg.sender === 'bot' && (
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-900 text-xs font-bold text-white">
                <Bot className="h-3.5 w-3.5 text-blue-400" />
              </div>
            )}

            {/* Message Bubble */}
            <div
              className={`space-y-1.5 ${
                msg.sender === 'user' ? 'items-end' : 'items-start'
              }`}
            >
              <div
                className={`p-3.5 rounded-xl text-xs sm:text-sm leading-relaxed shadow-xs ${
                  msg.sender === 'user'
                    ? 'bg-blue-600 text-white rounded-tr-none'
                    : 'bg-white text-gray-900 border border-gray-200 rounded-tl-none'
                }`}
              >
                <p>{msg.text}</p>

                {msg.sender === 'bot' && msg.isFallback && (
                  <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700">
                    Đường dịch dự phòng{msg.fallbackReason ? ` · ${msg.fallbackReason}` : ''}
                  </div>
                )}

                {/* Matched Glossary Terms badge if applicable */}
                {msg.matchedGlossaryTerms && msg.matchedGlossaryTerms.length > 0 && (
                  <div className="mt-2.5 pt-2 border-t border-gray-100 flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] font-semibold text-blue-600 flex items-center gap-1">
                      <BookOpen className="w-3 h-3" /> Thuật ngữ áp dụng:
                    </span>
                    {msg.matchedGlossaryTerms.map((term) => (
                      <span
                        key={term}
                        className="text-[10px] bg-blue-50 text-blue-700 font-medium px-2 py-0.5 rounded border border-blue-100"
                      >
                        {term}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Message Metadata & Quick Actions */}
              <div
                className={`flex items-center gap-2 px-1 text-[10px] text-gray-400 ${
                  msg.sender === 'user' ? 'justify-end' : 'justify-start'
                }`}
              >
                <span>{msg.timestamp}</span>

                {msg.sender === 'bot' && (
                  <>
                    <span>•</span>
                    <span className="font-medium text-gray-500">{msg.model}</span>
                    <span>•</span>
                    <span className="font-mono text-gray-500">{msg.latencyMs}ms</span>
                    <span>•</span>
                    <span className="font-mono text-gray-500">{msg.inputTokens}in / {msg.outputTokens}out</span>
                    <span>•</span>

                    {/* Copy text */}
                    <button
                      onClick={() => handleCopy(msg.id, msg.text)}
                      className="hover:text-blue-600 inline-flex items-center gap-0.5"
                    >
                      {copiedMsgId === msg.id ? (
                        <Check className="w-3 h-3 text-green-600" />
                      ) : (
                        <Copy className="w-3 h-3" />
                      )}
                    </button>

                    <span>•</span>

                    {/* Submit Correction Suggestion */}
                    <button
                      onClick={() => handleOpenSuggestModal(msg)}
                      className="hover:text-amber-700 font-medium inline-flex items-center gap-1 text-amber-600"
                    >
                      <Lightbulb className="w-3 h-3" />
                      <span>Đề xuất sửa</span>
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        ))}

        {isTranslating && (
          <div className="flex gap-3 max-w-md mr-auto">
            <div className="w-7 h-7 rounded-full bg-gray-900 text-white flex items-center justify-center shrink-0">
              <Bot className="w-3.5 h-3.5 text-blue-400 animate-spin" />
            </div>
            <div className="p-3 bg-white border border-gray-200 rounded-xl rounded-tl-none shadow-xs flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-blue-600 animate-ping" />
              <span className="text-xs font-medium text-gray-600">
                LinguaFlow AI đang dịch và áp dụng quy tắc thuật ngữ...
              </span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Box */}
      <div className="shrink-0 border-t border-gray-200 bg-white p-3 sm:p-4">
        <div className="flex items-end gap-3 rounded-2xl border border-gray-200 bg-white p-2 shadow-sm transition-all focus-within:border-blue-400 focus-within:shadow-md focus-within:shadow-blue-100/70">
          <textarea
            id="chat-input-field"
            rows={1}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void handleSend(); } }}
            placeholder={`Nhập văn bản cần dịch (${sourceLang} → ${targetLang})...`}
            className="max-h-28 min-h-10 flex-1 resize-none bg-transparent px-3 py-2 text-xs leading-5 text-gray-900 outline-hidden placeholder:text-gray-400 sm:text-sm"
          />
          <button
            id="btn-send-translation"
            type="button"
            onClick={() => void handleSend()}
            disabled={!inputText.trim() || isTranslating}
            aria-label="Gửi nội dung để dịch"
            title="Gửi bản dịch"
            className="group flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-transparent bg-gradient-to-br from-[#3B82F6] to-[#2563EB] text-white shadow-md shadow-blue-600/25 transition-all duration-200 hover:-translate-y-0.5 hover:from-[#2563EB] hover:to-[#1D4ED8] hover:shadow-lg hover:shadow-blue-600/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#93C5FD] focus-visible:ring-offset-2 disabled:translate-y-0 disabled:cursor-not-allowed disabled:border-[#D8E2F1] disabled:bg-[#F3F6FB] disabled:bg-none disabled:text-[#94A3B8] disabled:shadow-none"
          >
            <Send className="h-[18px] w-[18px] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" strokeWidth={2.2} />
          </button>
        </div>
      </div>

      {/* Suggestion Submission Modal from Chat */}
      {suggestingMessage && (
        <div
          id="chat-suggestion-modal"
          className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4"
          onClick={() => setSuggestingMessage(null)}
        >
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 shadow-lg border border-gray-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 pb-3 border-b border-gray-100">
              <div className="w-8 h-8 rounded-full bg-amber-50 text-amber-600 flex items-center justify-center">
                <Lightbulb className="w-4 h-4" />
              </div>
              <div>
                <h3 className="font-semibold text-gray-900 text-sm">Gửi đề xuất sửa bản dịch</h3>
                <p className="text-xs text-gray-400">Đóng góp cải thiện chất lượng AI</p>
              </div>
            </div>

            <form onSubmit={handleSubmitSuggestionForm} className="space-y-3 text-xs">
              <div>
                <label className="font-medium text-gray-700 block mb-1">Văn bản gốc:</label>
                <div className="p-2.5 bg-gray-50 rounded-lg border border-gray-200 text-gray-800">
                  {suggestingMessage.sourceText || suggestingMessage.text}
                </div>
              </div>

              <div>
                <label className="font-medium text-gray-700 block mb-1">
                  Bản dịch đề xuất của bạn: <span className="text-rose-500">*</span>
                </label>
                <textarea
                  rows={2}
                  required
                  value={userSuggestionText}
                  onChange={(e) => setUserSuggestionText(e.target.value)}
                  className="w-full p-2 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900"
                />
              </div>

              <div>
                <label className="font-medium text-gray-700 block mb-1">Lý do điều chỉnh:</label>
                <input
                  type="text"
                  value={userReason}
                  onChange={(e) => setUserReason(e.target.value)}
                  placeholder="Ví dụ: Thuật ngữ chuyên ngành, diễn đạt tự nhiên hơn..."
                  className="w-full p-2 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900"
                />
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setSuggestingMessage(null)}
                  className="px-4 py-2 font-medium text-gray-700 hover:bg-gray-100 rounded-md"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md shadow-xs"
                >
                  Gửi duyệt
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
