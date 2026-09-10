import React, { useEffect, useRef, useState } from "react";
import { Loader2, Search, X } from "lucide-react";

export interface ConversationSearchResult {
  messageId: string;
  snippet: string;
  matchedIn: "original" | "translation";
}

interface MessageSearchPanelProps {
  onClose: () => void;
  onSearch: (query: string) => Promise<ConversationSearchResult[]>;
  onSelect: (messageId: string) => void;
}

export const MessageSearchPanel: React.FC<MessageSearchPanelProps> = ({ onClose, onSearch, onSelect }) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ConversationSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = query.trim();
    if (!value) return;
    setIsSearching(true);
    setHasSearched(true);
    setError(null);
    try {
      setResults(await onSearch(value));
    } catch (reason) {
      setResults([]);
      setError(reason instanceof Error ? reason.message : "Could not search messages.");
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <section
      aria-label="Search messages"
      className="absolute inset-x-3 top-2 z-20 mx-auto max-w-xl overflow-hidden rounded-2xl border border-[#E8EAF0] bg-white shadow-xl dark:border-[#2E3342] dark:bg-[#232630]"
    >
      <form onSubmit={submit} className="flex items-center gap-2 border-b border-[#E8EAF0] p-2 dark:border-[#2E3342]">
        <Search className="h-4 w-4 flex-none text-[#74798C]" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search messages"
          className="min-w-0 flex-1 bg-transparent px-1 py-1.5 text-sm text-[#1E2230] outline-none placeholder:text-[#9DA3B4] dark:text-[#F5F6FA]"
        />
        {isSearching && <Loader2 className="h-4 w-4 animate-spin text-[#2563EB]" />}
        <button type="button" onClick={onClose} aria-label="Close search" className="rounded-lg p-1 text-[#74798C] hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]">
          <X className="h-4 w-4" />
        </button>
      </form>
      {(error || results.length > 0) && (
        <div className="max-h-72 overflow-y-auto p-1.5">
          {error ? <p className="p-2 text-xs text-rose-600">{error}</p> : results.map((result) => (
            <button
              key={result.messageId}
              type="button"
              onClick={() => onSelect(result.messageId)}
              className="w-full rounded-xl px-3 py-2 text-left hover:bg-[#F4F5F8] dark:hover:bg-[#2E3342]"
            >
              <span className="mb-0.5 block text-[10px] font-semibold uppercase tracking-wide text-[#2563EB]">
                {result.matchedIn === "translation" ? "Translated text" : "Original text"}
              </span>
              <span className="block truncate text-xs text-[#1E2230] dark:text-[#E2E5F0]">{result.snippet}</span>
            </button>
          ))}
        </div>
      )}
      {!isSearching && query.trim() && !error && results.length === 0 && (
        <p className="p-3 text-center text-xs text-[#74798C]">{hasSearched ? "No messages found." : "Press Enter to search."}</p>
      )}
    </section>
  );
};
