"use client";

import React, { useState } from "react";
import { CalendarPlus, Check, X } from "lucide-react";
import type { ApiActionProposal } from "../api/chat-api";
import {
  ApprovalOptions,
  approvalOptionsFromProposal,
  ProposalDraft,
  canApprove,
  decisionCorrections,
  draftFromProposal,
} from "../proposal-approval";
import { ProposalDecisionForm } from "./ProposalDecisionForm";

interface InlineProposalCardProps {
  proposal: ApiActionProposal;
  busy: boolean;
  onApprove: (proposal: ApiActionProposal, corrections: Record<string, unknown>) => void;
  onReject: (proposal: ApiActionProposal) => void;
}

/** The assistant's proposal, offered where the conversation is happening.
 *
 *  Routing every approval through the task inbox made the assistant's most
 *  common answer a detour: it replied in the chat that it had prepared
 *  something, and the person then had to leave the conversation to act on it.
 *  Deciding costs two clicks, so it belongs next to the sentence that prompted
 *  it — the inbox stays the place for proposals nobody dealt with at the time.
 *
 *  It is still a proposal, not a booking. Nothing reaches the calendar until
 *  this card is approved (ADR-30), and the same duration and reminder choices
 *  the inbox offers are here, because a proposal approved from the chat must
 *  not quietly land with different defaults.
 */
export const InlineProposalCard: React.FC<InlineProposalCardProps> = ({
  proposal,
  busy,
  onApprove,
  onReject,
}) => {
  const [chosen, setChosen] = useState<ApprovalOptions>(() => approvalOptionsFromProposal(proposal));
  const [draft, setDraft] = useState<ProposalDraft>(() => draftFromProposal(proposal));
  const ready = canApprove(proposal, draft);
  const approvalHelp = proposal.action_type === "appointment" && !draft.startsAtLocal
    ? "Chọn ngày và giờ trước khi duyệt"
    : "Điền nốt thông tin còn thiếu ở trên";

  return (
    <div className="mx-auto my-3 w-full max-w-md rounded-2xl border border-violet-200 bg-violet-50/70 p-4 dark:border-violet-400/25 dark:bg-violet-500/10">
      <p className="flex items-center gap-2 text-xs font-semibold text-violet-800 dark:text-violet-200">
        <CalendarPlus className="h-4 w-4 flex-none" />
        Trợ lý đề xuất thêm vào lịch
      </p>

      <ProposalDecisionForm
        proposal={proposal}
        draft={draft}
        chosen={chosen}
        compact
        onDraftChange={(patch) => setDraft((current) => ({ ...current, ...patch }))}
        onOptionsChange={(patch) => setChosen((current) => ({ ...current, ...patch }))}
      />

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={busy || !ready}
          title={ready ? undefined : approvalHelp}
          onClick={() => onApprove(proposal, decisionCorrections(proposal, draft, chosen))}
          className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-[#2563EB] px-3.5 py-2 text-xs font-bold text-white hover:bg-[#1D4ED8] disabled:opacity-50"
        >
          <Check className="h-3.5 w-3.5" /> Duyệt và thêm vào lịch
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onReject(proposal)}
          className="inline-flex items-center gap-1 rounded-lg border border-[#D8DCE7] px-3 py-2 text-xs font-semibold text-[#62687B] hover:bg-white disabled:opacity-50 dark:border-[#3A3F50] dark:text-[#C6CAD6]"
        >
          <X className="h-3.5 w-3.5" /> Từ chối
        </button>
      </div>
    </div>
  );
};
