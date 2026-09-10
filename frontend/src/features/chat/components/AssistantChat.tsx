"use client";

import React, { useEffect, useRef, useState } from "react";
import { CalendarPlus, Check, Clock3, Paperclip, SendHorizontal, ShieldCheck, Sparkles, X, XCircle } from "lucide-react";
import { ASSISTANT_AVATAR_URL } from "../api/chat-api";
import type { User } from "../types";

const CALENDAR_STORAGE_KEY = "linguachat.personal-calendar.tasks";

interface AssistantChatProps { currentUser: User; onBack?: () => void; onPreviewChange?: (message: string, time: string) => void; }
interface ChatLine { id: string; author: "assistant" | "user"; text: string; proposal?: { title: string; dueAt: string }; }

export const AssistantChat: React.FC<AssistantChatProps> = ({ currentUser, onBack, onPreviewChange }) => {
  const [text, setText] = useState("");
  const [lines, setLines] = useState<ChatLine[]>([{ id: "intro", author: "assistant", text: "Chào bạn! Tôi có thể tóm tắt cuộc trò chuyện, đề xuất việc cần làm hoặc nhắc lịch. Mọi đề xuất đều cần bạn xác nhận trước khi được đưa vào Lịch cá nhân." }]);
  const [proposal, setProposal] = useState<{ title: string; dueAt: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const latest = lines.at(-1);
    if (latest) onPreviewChange?.(latest.text, 'Bây giờ');
  }, [lines, onPreviewChange]);

  const send = () => {
    const request = text.trim();
    if (!request) return;
    const dueAt = new Date(Date.now() + 86_400_000).toISOString();
    const nextProposal = { title: request.replace(/^@assistant\s*/i, "") || "Việc mới", dueAt };
    setLines((items) => [...items, { id: `user-${Date.now()}`, author: "user", text: request }, { id: `assistant-${Date.now()}`, author: "assistant", text: "Tôi đã tạo một đề xuất. Bạn hãy xem lại và xác nhận hành động trước khi đưa vào lịch.", proposal: nextProposal }]);
    setText("");
  };
  const attachFile = (file?: File) => {
    if (!file) return;
    setLines((items) => [...items, { id: `file-${Date.now()}`, author: "user", text: `Đã gửi tệp: ${file.name}` }, { id: `file-reply-${Date.now()}`, author: "assistant", text: "Đã nhận tệp. Bạn muốn tôi tóm tắt, trích xuất việc cần làm hay chuẩn bị một đề xuất?" }]);
  };

  return <div className="flex-1 flex h-screen min-w-0 overflow-hidden bg-[#F7F8FC] dark:bg-[#14161C]"><main className="flex-1 flex h-screen min-w-0 flex-col bg-[#F7F8FC] dark:bg-[#14161C] transition-colors">
    <header id="active-chat-header" className="flex h-[68px] w-full shrink-0 items-center justify-between border-b border-[#E8EAF0] bg-white/95 px-4 dark:border-[#232630] dark:bg-[#1C1F27]/95 md:px-6">
      <div className="flex min-w-0 items-center gap-3">
      {onBack && <button onClick={onBack} className="rounded-lg p-1 text-[#74798C] md:hidden"><X className="h-5 w-5" /></button>}
      <img src={ASSISTANT_AVATAR_URL} alt="Trợ lý thông minh" className="h-10 w-10 rounded-full object-cover ring-1 ring-violet-200 dark:ring-violet-400/30" referrerPolicy="no-referrer" />
      <div className="min-w-0"><h1 className="truncate text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">Trợ lý thông minh</h1><p className="text-xs text-violet-600 dark:text-violet-300">Không gian riêng tư của bạn</p></div>
      </div>
    </header>
    <div id="chat-messages-container" className="flex-1 w-full space-y-1.5 overflow-y-auto px-4 py-6 transition-colors sm:px-6 md:px-8 lg:px-10" role="log" aria-label="Chat messages">
      <div className="flex items-center justify-center my-6"><div className="flex w-full max-w-sm items-center gap-3"><div className="h-px flex-1 bg-[#E8EAF0] dark:bg-[#2A2E3D]" /><span className="px-1 text-[11px] font-bold uppercase tracking-wider text-[#8A8F9E] dark:text-[#74798C]">Hôm nay</span><div className="h-px flex-1 bg-[#E8EAF0] dark:bg-[#2A2E3D]" /></div></div>
      {lines.map((line) => <div key={line.id} className={`flex gap-2.5 my-1 ${line.author === "user" ? "justify-end" : "justify-start"}`}>
        {line.author === "assistant" && <img src={ASSISTANT_AVATAR_URL} alt="Trợ lý thông minh" className="mt-1 h-8 w-8 shrink-0 rounded-full object-cover ring-1 ring-violet-200 dark:ring-violet-400/30" referrerPolicy="no-referrer" />}
        <div className={`max-w-[85%] sm:max-w-[72%] md:max-w-[62%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${line.author === "user" ? "rounded-br-sm bg-[#2563EB] text-white shadow-sm" : "rounded-bl-sm border border-[#E8EAF0] bg-white text-[#1E2230] shadow-sm dark:border-[#2E3342] dark:bg-[#232630] dark:text-[#F5F6FA]"}`}><p>{line.text}</p>{line.proposal && <button onClick={() => setProposal(line.proposal!)} className="mt-3 flex w-full items-center justify-between rounded-xl border border-violet-200 bg-violet-50 p-3 text-left text-violet-800 hover:bg-violet-100 dark:border-violet-400/25 dark:bg-violet-500/10 dark:text-violet-200"><span><span className="block text-[10px] font-bold uppercase tracking-wide">Đề xuất hành động</span><span className="mt-0.5 block text-xs font-bold">{line.proposal.title}</span><span className="mt-1 flex items-center gap-1 text-[10px]"><Clock3 className="h-3 w-3" />Cần xác nhận trước khi lên lịch</span></span><CalendarPlus className="h-5 w-5" /></button>}<div className={`mt-1 text-[11px] ${line.author === "user" ? "text-right text-white/70" : "text-[#8A8F9E] dark:text-[#74798C]"}`}>Bây giờ</div></div>
      </div>)}
    </div>
    <div id="message-composer-container" className="relative w-full shrink-0 px-4 pb-4 pt-1 transition-colors sm:px-6 md:px-8"><div className={`relative flex items-end gap-2 rounded-2xl border bg-white p-2 shadow-sm transition-all sm:p-2.5 dark:bg-[#1C1F27] ${text.trim() ? "border-[#2563EB]/40 shadow-[#2563EB]/5" : "border-[#E8EAF0] dark:border-[#2A2E3D]"}`}><button type="button" onClick={() => fileInputRef.current?.click()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-[#74798C] hover:bg-[#EFF6FF] hover:text-[#2563EB] dark:hover:bg-[#2563EB]/15" aria-label="Đính kèm tệp"><Paperclip className="h-5 w-5" /></button><input ref={fileInputRef} type="file" className="hidden" onChange={(event) => { attachFile(event.target.files?.[0]); event.target.value = ""; }} /><div className="min-w-0 flex-1 py-1"><textarea value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} rows={1} placeholder="Nhờ trợ lý giúp bạn..." className="max-h-32 w-full resize-none bg-transparent px-1 py-0.5 text-sm leading-relaxed text-[#1E2230] outline-none dark:text-[#F5F6FA]" /></div><button id="composer-send-btn" type="button" onClick={send} disabled={!text.trim()} aria-label="Gửi tin nhắn" className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-all ${text.trim() ? "bg-[#2563EB] text-white shadow-md shadow-[#2563EB]/20 hover:scale-105 hover:bg-[#1D4ED8] active:scale-95" : "cursor-not-allowed bg-[#F4F5F8] text-[#8A8F9E] opacity-60 dark:bg-[#232630] dark:text-[#74798C]"}`}><SendHorizontal className="h-5 w-5" /></button></div></div>
    {proposal && <ActionConfirmDialog proposal={proposal} onClose={() => setProposal(null)} onApproved={() => { setLines((items) => [...items, { id: `confirmed-${Date.now()}`, author: "assistant", text: "Đã xác nhận. Công việc đã được thêm vào Lịch cá nhân." }]); setProposal(null); }} />}
  </main></div>;
};

const ActionConfirmDialog: React.FC<{ proposal: { title: string; dueAt: string }; onClose: () => void; onApproved: () => void }> = ({ proposal, onClose, onApproved }) => {
  const [title, setTitle] = useState(proposal.title); const [dueAt, setDueAt] = useState(proposal.dueAt.slice(0, 16));
  const approve = () => { const task = { id: `assistant-${Date.now()}`, title: title.trim() || "Việc mới", dueAt: new Date(dueAt).toISOString(), duration: 60, status: "approved", source: "assistant", priority: "medium" }; try { const saved = JSON.parse(window.localStorage.getItem(CALENDAR_STORAGE_KEY) || "[]"); window.localStorage.setItem(CALENDAR_STORAGE_KEY, JSON.stringify([...saved, task])); } catch { window.localStorage.setItem(CALENDAR_STORAGE_KEY, JSON.stringify([task])); } onApproved(); };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#111827]/45 p-4" role="dialog" aria-modal="true" aria-label="Xác nhận hành động"><div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl dark:bg-[#232630]"><div className="flex items-start justify-between gap-4"><div><span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-1 text-[10px] font-bold text-violet-700 dark:bg-violet-500/20 dark:text-violet-200"><Sparkles className="h-3 w-3" />Đề xuất từ trợ lý</span><h2 className="mt-2 text-lg font-bold text-[#1E2230] dark:text-[#F5F6FA]">Xác nhận hành động</h2><p className="mt-1 text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">Bạn có thể sửa đề xuất trước khi duyệt. Chỉ sau khi duyệt, công việc mới được thêm vào lịch.</p></div><button onClick={onClose} className="rounded-lg p-1 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]"><X className="h-5 w-5" /></button></div><div className="mt-5 space-y-3"><label className="block text-xs font-semibold text-[#4E5568] dark:text-[#C6CAD6]">Tên công việc<input value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1.5 w-full rounded-lg border border-[#DDE1EA] bg-white px-3 py-2 text-sm font-normal text-[#1E2230] outline-none focus:border-[#2563EB] dark:border-[#3A4050] dark:bg-[#1C1F27] dark:text-[#F5F6FA]" /></label><label className="block text-xs font-semibold text-[#4E5568] dark:text-[#C6CAD6]">Thời gian<input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} className="mt-1.5 w-full rounded-lg border border-[#DDE1EA] bg-white px-3 py-2 text-sm font-normal text-[#1E2230] outline-none focus:border-[#2563EB] dark:border-[#3A4050] dark:bg-[#1C1F27] dark:text-[#F5F6FA]" /></label></div><div className="mt-5 flex flex-wrap justify-between gap-2 border-t border-[#E8EAF0] pt-4 dark:border-[#2E3342]"><button onClick={onClose} className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10"><XCircle className="h-4 w-4" />Từ chối</button><button onClick={approve} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700"><Check className="h-4 w-4" />Duyệt và thêm vào lịch</button></div></div></div>;
};
