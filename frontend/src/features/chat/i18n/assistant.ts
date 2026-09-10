import type { LanguageCode } from "../types";

const assistantNames: Record<LanguageCode, string> = {
  en: "Smart assistant", vi: "Trợ lý thông minh", ja: "スマートアシスタント", ko: "스마트 어시스턴트", zh: "智能助手", es: "Asistente inteligente", fr: "Assistant intelligent", de: "Intelligenter Assistent", th: "ผู้ช่วยอัจฉริยะ", id: "Asisten cerdas", pt: "Assistente inteligente", ru: "Умный помощник", ar: "المساعد الذكي", hi: "स्मार्ट सहायक",
};
export const assistantName = (language: LanguageCode) => assistantNames[language];

const conversationSummaryCopy: Record<LanguageCode, { label: string; prompt: string }> = {
  en: { label: 'Summarize conversation', prompt: '@assistant Summarize this conversation.' },
  vi: { label: 'Tóm tắt hội thoại', prompt: '@assistant Tóm tắt hội thoại này.' },
  ja: { label: '会話を要約', prompt: '@assistant この会話を要約してください。' },
  ko: { label: '대화 요약', prompt: '@assistant 이 대화를 요약해 주세요.' },
  zh: { label: '总结对话', prompt: '@assistant 请总结此对话。' },
  es: { label: 'Resumir conversación', prompt: '@assistant Resume esta conversación.' },
  fr: { label: 'Résumer la conversation', prompt: '@assistant Résume cette conversation.' },
  de: { label: 'Unterhaltung zusammenfassen', prompt: '@assistant Fasse diese Unterhaltung zusammen.' },
  th: { label: 'สรุปการสนทนา', prompt: '@assistant สรุปการสนทนานี้' },
  id: { label: 'Ringkas percakapan', prompt: '@assistant Ringkas percakapan ini.' },
  pt: { label: 'Resumir conversa', prompt: '@assistant Resuma esta conversa.' },
  ru: { label: 'Суммировать разговор', prompt: '@assistant Кратко изложи этот разговор.' },
  ar: { label: 'تلخيص المحادثة', prompt: '@assistant لخّص هذه المحادثة.' },
  hi: { label: 'बातचीत का सारांश', prompt: '@assistant इस बातचीत का सारांश दें।' },
};
export const conversationSummaryText = (language: LanguageCode) => conversationSummaryCopy[language];
