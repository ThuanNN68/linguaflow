"use client";

import React from "react";
import { AlertCircle, ArrowRight, CalendarDays, Clock3, FileText, MapPin } from "lucide-react";
import type { ApiActionProposal } from "../api/chat-api";
import {
  ApprovalOptions,
  ProposalDraft,
  REMINDER_CHOICES,
  missingFields,
  toLocalInputValue,
} from "../proposal-approval";

interface ProposalDecisionFormProps {
  proposal: ApiActionProposal;
  draft: ProposalDraft;
  chosen: ApprovalOptions;
  onDraftChange: (patch: Partial<ProposalDraft>) => void;
  onOptionsChange: (patch: Partial<ApprovalOptions>) => void;
  /** Slightly tighter styling for the card that sits inside a conversation. */
  compact?: boolean;
  /** Show proposal information only; editing is handled by the full event form. */
  readOnly?: boolean;
}

const MISSING_LABELS: Record<string, string> = {
  time: "thời gian",
  timezone: "múi giờ",
  location: "địa điểm",
  title: "tiêu đề",
  details: "nội dung",
};

/** The editable half of a proposal decision, shared by both places it happens.
 *
 *  One component rather than two, for the reason `proposal-approval.ts` gives:
 *  the task inbox and the in-chat card are the same decision reached from two
 *  places, and a proposal must not land on the calendar differently depending
 *  on which one the person happened to be looking at.
 *
 *  Everything here is editable on purpose. The assistant extracts from a
 *  sentence somebody typed in a hurry; a wrong title or a time it read as next
 *  Tuesday are obvious to the owner and tedious to fix by rejecting and asking
 *  again. Editing is also the only route to approving a proposal the extractor
 *  left incomplete — the server clears a field out of `missing_fields` when a
 *  correction supplies it.
 */
export const ProposalDecisionForm: React.FC<ProposalDecisionFormProps> = ({
  proposal,
  draft,
  chosen,
  onDraftChange,
  onOptionsChange,
  compact = false,
  readOnly = false,
}) => {
  const missing = missingFields(proposal);
  const field = `w-full rounded-lg border bg-white px-2.5 py-1.5 text-xs outline-none focus:border-[#2563EB] dark:bg-[#1B1D25] ${
    compact
      ? "border-violet-200 dark:border-violet-400/30"
      : "border-[#D8DCE7] dark:border-[#3A3F50]"
  }`;
  const label = "mb-1 block text-[11px] font-semibold text-[#62687B] dark:text-[#9DA3B4]";
  const endsAtLocal = draft.startsAtLocal
    ? toLocalInputValue(
        new Date(
          new Date(draft.startsAtLocal).getTime() + chosen.durationMinutes * 60_000,
        ).toISOString(),
      )
    : "";
  const dateFromLocal = (value: string) => (value ? new Date(value) : null);
  const formattedDate = dateFromLocal(draft.startsAtLocal)?.toLocaleDateString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }) ?? "Chưa xác định";
  const formatTime = (value: string) => dateFromLocal(value)?.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }) ?? "Chưa xác định";

  return (
    <div className="mt-3 space-y-3">
      {!readOnly && missing.length > 0 && (
        <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          Trợ lý chưa xác định được {missing.map((item) => MISSING_LABELS[item] ?? item).join(", ")}.
          {readOnly ? "Mở đề xuất để bổ sung trước khi duyệt." : "Bạn điền giúp rồi bấm duyệt."}
        </p>
      )}

      {readOnly ? (
        <>
          <div className="grid grid-cols-1 gap-y-2 text-sm text-[#31394B] dark:text-[#E2E8F0] sm:grid-cols-[1.15fr_1fr_1fr] sm:gap-y-0 sm:divide-x sm:divide-[#E6EAF1] dark:sm:divide-[#34394A]">
            <p className="flex min-w-0 items-center gap-1.5 sm:pr-4">
                <CalendarDays className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                <span className="font-semibold text-[#62687B] dark:text-[#9DA3B4]">Ngày diễn ra:</span>
                <span className="font-medium">{formattedDate}</span>
            </p>
            <p className="flex min-w-0 items-center gap-1.5 sm:px-4">
                <Clock3 className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                <span className="font-semibold text-[#62687B] dark:text-[#9DA3B4]">Bắt đầu:</span>
                <span className="font-medium">{formatTime(draft.startsAtLocal)}</span>
            </p>
            <p className="flex min-w-0 items-center gap-1.5 sm:pl-4">
                <Clock3 className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                <span className="font-semibold text-[#62687B] dark:text-[#9DA3B4]">Kết thúc:</span>
                <span className="font-medium">{formatTime(endsAtLocal)}</span>
            </p>
          </div>
          <p className="flex items-center gap-1.5 text-sm text-[#31394B] dark:text-[#E2E8F0]">
            <MapPin className="h-4 w-4 flex-none text-[#2563EB]" aria-hidden="true" />
            <span className="font-semibold text-[#62687B] dark:text-[#9DA3B4]">Địa điểm:</span>
            <span className="font-medium">{draft.location || "Chưa xác định"}</span>
          </p>
          <p className="flex items-start gap-1.5 text-sm text-[#31394B] dark:text-[#E2E8F0]">
            <FileText className="mt-0.5 h-4 w-4 flex-none text-[#2563EB]" aria-hidden="true" />
            <span className="font-semibold text-[#62687B] dark:text-[#9DA3B4]">Ghi chú:</span>
            <span className="font-medium">{proposal.details || "Chưa có ghi chú"}</span>
          </p>
        </>
      ) : (
        <>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(190px,1fr)]">
        <div className="rounded-xl border border-[#E4E8F1] bg-[#F8FAFF] p-3 dark:border-[#34394A] dark:bg-[#20242F]">
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-[#526079] dark:text-[#B8C0D1]">
            <Clock3 className="h-3.5 w-3.5 text-[#2563EB]" />
            Thời gian
          </p>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-end">
            <div>
              <label className={label}>Bắt đầu</label>
              <input
                type="datetime-local"
                value={draft.startsAtLocal}
                onChange={(event) => onDraftChange({ startsAtLocal: event.target.value })}
                className={`${field} py-2`}
              />
            </div>
            <ArrowRight className="mb-2 hidden h-4 w-4 text-[#94A3B8] sm:block" aria-hidden="true" />
            <div>
              <label className={label}>Kết thúc</label>
              <input
                type="datetime-local"
                value={endsAtLocal}
                disabled={!draft.startsAtLocal}
                onChange={(event) => {
                  const duration = Math.round(
                    (new Date(event.target.value).getTime() - new Date(draft.startsAtLocal).getTime()) / 60_000,
                  );
                  if (duration >= 15) onOptionsChange({ durationMinutes: duration });
                }}
                className={`${field} py-2 disabled:cursor-not-allowed disabled:bg-[#F1F5F9] disabled:text-[#94A3B8] dark:disabled:bg-[#171A22]`}
              />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-[#E4E8F1] bg-white p-3 dark:border-[#34394A] dark:bg-[#1B1D25]">
          <label className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-[#526079] dark:text-[#B8C0D1]">
            <MapPin className="h-3.5 w-3.5 text-[#2563EB]" />
            Địa điểm
          </label>
          <input
            value={draft.location}
            onChange={(event) => onDraftChange({ location: event.target.value })}
            className={`${field} py-2`}
            placeholder="Không bắt buộc"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <label className="flex items-center gap-2 text-xs text-[#62687B] dark:text-[#C6CAD6]">
          <span className="font-semibold">Nhắc trước</span>
          <select
            value={String(chosen.reminderMinutesBefore)}
            onChange={(event) =>
              onOptionsChange({
                reminderMinutesBefore:
                  event.target.value === "null" ? null : Number(event.target.value),
              })
            }
            className={field.replace("w-full ", "")}
          >
            {REMINDER_CHOICES.map((choice) => (
              <option key={String(choice.value)} value={String(choice.value)}>{choice.label}</option>
            ))}
          </select>
        </label>
      </div>
        </>
      )}
    </div>
  );
};
