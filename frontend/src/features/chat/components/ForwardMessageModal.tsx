import React from "react";
import { Forward, X } from "lucide-react";
import type { Conversation, LanguageCode, Message } from "../types";
import { tx } from "../i18n";

interface Props {
  message: Message | null;
  conversations: Conversation[];
  onClose: () => void;
  onSelect: (conversation: Conversation) => void;
  onStartNewChat: () => void;
  language: LanguageCode;
}

export const ForwardMessageModal: React.FC<Props> = ({ message, conversations, onClose, onSelect, onStartNewChat, language }) => {
  if (!message) return null;
  const destinations = conversations.filter((conversation) => conversation.id !== message.conversationId);
  return <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true" aria-label="Forward message">
    <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl dark:bg-[#232630]">
      <div className="mb-3 flex items-center justify-between"><h2 className="text-base font-semibold text-[#1E2230] dark:text-white">{tx(language, 'Forward message')}</h2><button onClick={onClose} aria-label="Close" className="rounded p-1 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]"><X className="h-4 w-4" /></button></div>
      <p className="mb-4 line-clamp-2 rounded-lg bg-[#F4F5F8] p-2.5 text-sm text-[#4B5263] dark:bg-[#1C1F27] dark:text-[#D8DCE7]">{message.content}</p>
      {destinations.length ? <div className="max-h-72 space-y-1 overflow-y-auto">{destinations.map((conversation) => <button key={conversation.id} onClick={() => onSelect(conversation)} className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-[#EFF6FF] dark:hover:bg-[#2563EB]/20"><img src={conversation.avatar} alt="" className="h-9 w-9 rounded-full" /><span className="min-w-0 flex-1 truncate text-sm font-medium text-[#1E2230] dark:text-white">{conversation.name}</span><Forward className="h-4 w-4 text-[#2563EB]" /></button>)}</div> : <div className="py-4 text-center"><p className="text-sm text-[#74798C]">{tx(language, 'No other conversation to forward to.')}</p><button onClick={onStartNewChat} className="mt-3 rounded-lg bg-[#2563EB] px-3 py-2 text-sm font-medium text-white">{tx(language, 'Start a new chat')}</button></div>}
    </div>
  </div>;
};
