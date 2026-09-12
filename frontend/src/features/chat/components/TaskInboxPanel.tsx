"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CalendarDays, Check, Clock3, ListTodo, Trash2, X } from "lucide-react";
import type { ApiActionProposal } from "../api/chat-api";
import type { LanguageCode } from "../types";
import { tx } from "../i18n";
import {
  ApprovalOptions,
  approvalOptionsFromProposal,
  ProposalDraft,
  canApprove,
  decisionCorrections,
  draftFromProposal,
  formatProposalWhen,
  toLocalInputValue,
} from "../proposal-approval";
import { ProposalDecisionForm } from "./ProposalDecisionForm";
import { FullEventModal } from "./PersonalCalendar";
import {
  confirmActionProposal,
  dismissActionProposal,
  dismissDecidedActionProposals,
  listActionProposals,
  rejectActionProposal,
} from "../api/chat-api";

interface TaskInboxPanelProps {
  token: string;
  language: LanguageCode;
  /** Proposals arriving live over the socket, newest first. */
  incoming?: ApiActionProposal[];
  onCountChange?: (pending: number) => void;
  /** Report a decision back, so the card for it in the chat stops asking.
   *
   *  Without this the sync ran one way only: approving in the chat updated this
   *  list through `incoming`, but approving here left the in-chat card offering
   *  the same choice for a proposal that had already been decided. */
  onProposalChanged?: (proposal: ApiActionProposal, removed?: boolean) => void;
  onNotify?: (title: string, detail?: string, tone?: "success" | "warning") => void;
}

/** Rank one proposal. Lower sorts first.
 *
 *  "Priority" is an acceptance criterion rather than a flourish, so the order is
 *  stated here with its reasoning rather than left to whatever the API returned.
 *
 *  0. Waiting on the user to answer a question. The agent has stopped and cannot
 *     continue until they reply, so this is the only category where nothing at
 *     all happens without them.
 *  1. Awaiting approval and dated. These expire: approve it after the meeting
 *     and the approval was pointless.
 *  2. Awaiting approval, undated. Still needs a decision, but nothing is lost by
 *     deciding tomorrow.
 *  3. Already approved. Shown for confirmation that it landed, not for action.
 *
 *  Within a band, sooner first, and undated items by recency — a proposal made
 *  five minutes ago is the one the user still remembers saying.
 */
function rank(proposal: ApiActionProposal): number {
  if (proposal.status === "needs_clarification") return 0;
  if (proposal.status === "pending_confirmation") {
    return proposal.scheduled_start_at || proposal.due_at ? 1 : 2;
  }
  return 3;
}

function whenOf(proposal: ApiActionProposal): number {
  const at = proposal.scheduled_start_at || proposal.due_at;
  return at ? new Date(at).getTime() : Number.POSITIVE_INFINITY;
}

function formatProposalTimestamp(value: string | null | undefined, fallback: string): string {
  if (!value) return fallback;
  return new Date(value).toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function processedAt(proposal: ApiActionProposal): string | null {
  if (proposal.status === "confirmed") return proposal.confirmed_at;
  if (proposal.status === "rejected") return proposal.rejected_at;
  if (proposal.status === "stale") return proposal.stale_at;
  return null;
}

const ASSISTANT_CONVERSATION_TITLE = "__linguachat_assistant__";

/** The private assistant thread is stored with a stable internal key so it can
 * be found across clients. That key is not a user-facing group name. */
function sourceConversationLabel(name: string | undefined, isGroup: boolean): string | null {
  if (!name) return null;
  if (name === ASSISTANT_CONVERSATION_TITLE) return "Trợ lý thông minh";
  return isGroup ? `Nhóm ${name}` : name;
}

export function proposalSource(proposal: ApiActionProposal): string {
  const sender = proposal.source_sender_name?.trim();
  const isGroup = proposal.source_conversation_type === "group";
  const group = proposal.source_conversation_name?.trim();
  const conversation = sourceConversationLabel(group, isGroup);
  const prefix = proposal.source_mode === "proactive"
    ? "Trợ lý nhận diện yêu cầu"
    : "Yêu cầu";

  if (sender) {
    return `${prefix} từ ${sender}${conversation ? ` · ${conversation}` : ""}`;
  }
  if (conversation) return `${prefix} từ ${conversation}`;
  return proposal.source_mode === "proactive"
    ? "Trợ lý nhận diện yêu cầu từ hội thoại"
    : "Yêu cầu từ hội thoại";
}

function orderProposals(items: ApiActionProposal[]): ApiActionProposal[] {
  return [...items].sort((a, b) => {
    const byBand = rank(a) - rank(b);
    if (byBand !== 0) return byBand;
    const byWhen = whenOf(a) - whenOf(b);
    if (byWhen !== 0 && Number.isFinite(byWhen)) return byWhen;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}

export const TaskInboxPanel: React.FC<TaskInboxPanelProps> = ({
  token,
  language,
  incoming,
  onCountChange,
  onNotify,
  onProposalChanged,
}) => {
  const [proposals, setProposals] = useState<ApiActionProposal[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  // What the approver chose for this proposal, keyed by id. Empty until they
  // touch a control, so an untouched card still approves with the server's
  // defaults and the extra fields are genuinely optional.
  const [options, setOptions] = useState<Record<string, ApprovalOptions>>({});

  const [drafts, setDrafts] = useState<Record<string, ProposalDraft>>({});
  const [editingProposal, setEditingProposal] = useState<ApiActionProposal | null>(null);
  const [tab, setTab] = useState<"awaiting" | "decided">("awaiting");

  const optionsFor = (proposal: ApiActionProposal): ApprovalOptions =>
    options[proposal.id] ?? approvalOptionsFromProposal(proposal);

  const draftFor = (proposal: ApiActionProposal): ProposalDraft =>
    drafts[proposal.id] ?? draftFromProposal(proposal);

  const setDraft = (proposal: ApiActionProposal, patch: Partial<ProposalDraft>) =>
    setDrafts((current) => ({
      ...current,
      [proposal.id]: { ...(current[proposal.id] ?? draftFromProposal(proposal)), ...patch },
    }));

  const setOption = (proposal: ApiActionProposal, patch: Partial<ApprovalOptions>) =>
    setOptions((current) => ({
      ...current,
      [proposal.id]: { ...(current[proposal.id] ?? approvalOptionsFromProposal(proposal)), ...patch },
    }));

  const load = useCallback(async () => {
    try {
      setProposals(await listActionProposals(token));
    } catch (error) {
      onNotify?.(
        "Không tải được hộp nhiệm vụ",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    } finally {
      setIsLoading(false);
    }
  }, [token, onNotify]);

  useEffect(() => {
    void load();
  }, [load]);

  // Merge live arrivals by id rather than appending: the socket can deliver a
  // proposal the initial fetch already returned, and a duplicate row invites the
  // user to approve the same commitment twice.
  useEffect(() => {
    if (!incoming?.length) return;
    setProposals((current) => {
      const byId = new Map(current.map((item) => [item.id, item]));
      for (const item of incoming) byId.set(item.id, item);
      return [...byId.values()];
    });
  }, [incoming]);

  const ordered = useMemo(() => orderProposals(proposals), [proposals]);
  // Two lists, because they answer different questions. The awaiting tab is work
  // waiting on the person; the approved tab records what already happened, and
  // mixing them buries the first under the second as the second grows.
  const awaiting = useMemo(
    () => ordered.filter((item) =>
      item.status === "pending_confirmation" || item.status === "needs_clarification"),
    [ordered],
  );
  const decidedList = useMemo(
    () => ordered.filter((item) =>
      item.status !== "pending_confirmation" && item.status !== "needs_clarification"),
    [ordered],
  );

  /** Remove a decided proposal from this list only.
   *
   *  Nothing is cancelled. An approved proposal already put an event on the
   *  calendar, and that event still fires its reminder — clearing a finished
   *  list is not a request to un-book a meeting.
   */
  const dismissOne = async (proposal: ApiActionProposal) => {
    setBusyId(proposal.id);
    try {
      await dismissActionProposal(token, proposal.id);
      setProposals((current) => current.filter((item) => item.id !== proposal.id));
      onProposalChanged?.(proposal, true);
    } catch (error) {
      onNotify?.("Không xoá được", error instanceof Error ? error.message : undefined, "warning");
    } finally {
      setBusyId(null);
    }
  };

  const dismissAllDecided = async () => {
    try {
      await dismissDecidedActionProposals(token);
      setProposals((current) => current.filter(
        (item) => item.status === "pending_confirmation" || item.status === "needs_clarification"));
      onNotify?.("Đã xoá khỏi danh sách", "Lịch và nhắc hẹn giữ nguyên", "success");
    } catch (error) {
      onNotify?.("Không xoá được", error instanceof Error ? error.message : undefined, "warning");
    }
  };
  const pendingCount = useMemo(
    () =>
      proposals.filter(
        (item) => item.status === "pending_confirmation" || item.status === "needs_clarification",
      ).length,
    [proposals],
  );

  useEffect(() => {
    onCountChange?.(pendingCount);
  }, [pendingCount, onCountChange]);

  const replace = (updated: ApiActionProposal) => {
    setProposals((current) => current.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)));
    onProposalChanged?.(updated);
  };

  const remove = (proposalId: string) =>
    setProposals((current) => current.filter((item) => item.id !== proposalId));

  const act = async (
    proposal: ApiActionProposal,
    run: () => Promise<ApiActionProposal>,
    success: string,
  ) => {
    setBusyId(proposal.id);
    try {
      replace(await run());
      onNotify?.(success, proposal.title, "success");
    } catch (error) {
      onNotify?.(
        "Không thực hiện được",
        error instanceof Error ? error.message : undefined,
        "warning",
      );
    } finally {
      setBusyId(null);
    }
  };

  const renderProposal = (proposal: ApiActionProposal) => {
          const busy = busyId === proposal.id;
          const needsAnswer = proposal.status === "needs_clarification";
          const decided = proposal.status !== "pending_confirmation" && !needsAnswer;
          const proposalWhen = formatProposalWhen(proposal);
          const missingAppointmentTime = proposal.action_type === "appointment"
            && !draftFor(proposal).startsAtLocal;
          const approvalHelp = missingAppointmentTime
            ? "Chọn ngày và giờ trước khi duyệt"
            : "Điền nốt thông tin còn thiếu ở trên";

          return (
            <article
              key={proposal.id}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("button, input, textarea")) return;
                if (!decided) setEditingProposal(proposal);
              }}
              className="relative overflow-hidden rounded-2xl border border-[#E8EAF0] bg-white shadow-sm transition-shadow hover:shadow-md dark:border-[#2A2E3D] dark:bg-[#1C1F27]"
            >
              <div className={`absolute inset-y-0 left-0 w-1 ${proposal.status === "confirmed" ? "bg-emerald-500" : "bg-[#2563EB]"}`} />
              <div className={`p-4 pl-5 sm:p-5 sm:pl-6 ${!decided ? "cursor-pointer" : ""}`}>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-bold leading-snug text-[#1E2230] dark:text-[#F5F6FA]">
                        {proposal.title}
                      </h3>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    proposal.status === "confirmed"
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200"
                      : "bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/15 dark:text-[#93C5FD]"
                        }`}
                      >
                        {proposal.status === "confirmed"
                    ? tx(language, "Approved")
                    : proposal.status === "rejected"
                      ? tx(language, "Rejected")
                      : proposal.status === "stale"
                        ? "Đã lỗi thời"
                        : tx(language, "Awaiting approval")}
                      </span>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        proposal.action_type === "appointment"
                          ? "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200"
                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200"
                      }`}>
                        {proposal.action_type === "appointment" ? <CalendarDays className="h-3 w-3" /> : <ListTodo className="h-3 w-3" />}
                        {proposal.action_type === "appointment" ? tx(language, "Event") : tx(language, "Task")}
                      </span>
                    </div>
                    {(proposalWhen !== "Chưa có thời gian" || proposal.source_mode === "proactive") && (
                      <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[#74798C] dark:text-[#9DA3B4]">
                        {proposalWhen !== "Chưa có thời gian" && (
                          <span className="inline-flex items-center gap-1.5"><Clock3 className="h-3.5 w-3.5 flex-none" />{proposalWhen}</span>
                        )}
                        {proposal.source_mode === "proactive" && (
                          <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700 dark:bg-violet-500/20 dark:text-violet-200">tự phát hiện</span>
                        )}
                      </p>
                    )}
                  </div>

                  {!decided && (
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        disabled={busy || !canApprove(proposal, draftFor(proposal))}
                        title={canApprove(proposal, draftFor(proposal)) ? undefined : approvalHelp}
                        onClick={() => void act(proposal, () => confirmActionProposal(token, proposal.id, decisionCorrections(proposal, draftFor(proposal), optionsFor(proposal))), "Đã duyệt và thêm vào lịch")}
                        className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-[#2563EB] px-3.5 py-2 text-xs font-bold text-white hover:bg-[#1D4ED8] disabled:opacity-50"
                      >
                        <Check className="h-3.5 w-3.5" /> {tx(language, "Approve")}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void act(proposal, () => rejectActionProposal(token, proposal.id), "Đã từ chối")}
                        className="rounded-lg border border-[#D8DCE7] px-3.5 py-2 text-xs font-semibold text-[#62687B] hover:bg-[#F7F8FC] disabled:opacity-50 dark:border-[#3A3F50] dark:text-[#C6CAD6] dark:hover:bg-[#232630]"
                      >{tx(language, "Reject")}</button>
                    </div>
                  )}

                  {decided && (
                    <div className="flex shrink-0 items-center">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void dismissOne(proposal)}
                        title="Chỉ ẩn khỏi danh sách. Lịch và nhắc hẹn giữ nguyên."
                        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-[#74798C] hover:bg-[#F7F8FC] hover:text-[#1E2230] disabled:opacity-50 dark:text-[#9DA3B4] dark:hover:bg-[#232630]"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Xoá
                      </button>
                    </div>
                  )}
                </div>

                {missingAppointmentTime && !decided && (
                  <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
                    Chưa có ngày và giờ. Bấm vào đề xuất để bổ sung trước khi duyệt.
                  </p>
                )}

              {!decided && (
                <ProposalDecisionForm
                  proposal={proposal}
                  draft={draftFor(proposal)}
                  chosen={optionsFor(proposal)}
                  onDraftChange={(patch) => setDraft(proposal, patch)}
                  onOptionsChange={(patch) => setOption(proposal, patch)}
                  readOnly
                />
              )}

              {proposal.status === "stale" && (
                <p className="mt-3 flex items-center gap-1 text-[11px] text-[#74798C]">
                  <AlertCircle className="h-3 w-3 flex-none" />
                  Tin nhắn gốc đã bị sửa hoặc gỡ, nên đề xuất này không còn dùng được.
                </p>
              )}

              <dl className="mt-4 grid max-w-3xl grid-cols-1 gap-x-8 gap-y-2 border-t border-[#EEF0F5] pt-3 text-xs text-[#62687B] dark:border-[#2A2E3D] dark:text-[#C6CAD6] sm:grid-cols-2">
                <div className="space-y-1.5">
                  <div className="flex gap-2"><dt className="shrink-0 text-[#8A8F9E]">{tx(language, "Type")}</dt><dd className="font-medium">{proposal.action_type === 'appointment' ? tx(language, 'Event') : tx(language, 'Task')}</dd></div>
                  <div className="flex gap-2"><dt className="shrink-0 text-[#8A8F9E]">{tx(language, "Source")}</dt><dd className="font-medium">{proposalSource(proposal)}</dd></div>
                </div>
                <div className="space-y-1.5">
                  <div className="flex gap-2"><dt className="shrink-0 text-[#8A8F9E]">{tx(language, "Created at")}</dt><dd className="font-medium">{formatProposalTimestamp(proposal.created_at, "—")}</dd></div>
                  <div className="flex gap-2"><dt className="shrink-0 text-[#8A8F9E]">{tx(language, "Processed at")}</dt><dd className="font-medium">{formatProposalTimestamp(processedAt(proposal), tx(language, "Not processed"))}</dd></div>
                </div>
              </dl>
            </div>
            </article>
          );
  };

  return (
    <section className="flex h-full w-full flex-col bg-[#F7F8FC] dark:bg-[#14161C]">
      <header className="shrink-0 border-b border-[#E8EAF0] bg-white px-5 py-4 dark:border-[#2A2E3D] dark:bg-[#1C1F27] sm:px-7">
        <div className="mx-auto flex max-w-4xl items-center gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#2563EB] text-white shadow-sm shadow-blue-200 dark:shadow-none">
            <ListTodo className="h-5 w-5" />
          </span>
          <div>
            <h2 className="text-base font-bold tracking-tight text-[#1E2230] dark:text-[#F5F6FA]">{tx(language, "Task inbox")}</h2>
            <p className="mt-0.5 text-xs text-[#74798C] dark:text-[#9DA3B4]">{tx(language, "Review proposals from your assistant")}</p>
          </div>
        </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-7 sm:py-8">
        <div className="mx-auto max-w-4xl space-y-5">
        {isLoading && (
          <p className="px-1 py-6 text-center text-xs text-[#74798C]">Đang tải…</p>
        )}

        {!isLoading && ordered.length === 0 ? (
          <div className="rounded-3xl border border-[#E8EAF0] bg-white px-6 py-14 text-center shadow-sm dark:border-[#2A2E3D] dark:bg-[#1C1F27] sm:px-12">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/15 dark:text-[#93C5FD]">
              <ListTodo className="h-7 w-7" />
            </span>
            <h3 className="mt-5 text-sm font-bold text-[#1E2230] dark:text-[#F5F6FA]">Chưa có việc nào cần duyệt</h3>
          </div>
        ) : (
          <>
        {/* Two tabs, not one long list. They answer different questions —
            "what needs me" against "what already happened" — and the second
            grows without bound, so merged it buries the first. */}
        <div className="flex items-center gap-1 rounded-2xl border border-[#E8EAF0] bg-white p-1.5 shadow-sm dark:border-[#2A2E3D] dark:bg-[#1C1F27]">
          {([
            ["awaiting", tx(language, "Awaiting review"), awaiting.length],
            ["decided", tx(language, "Processed"), decidedList.length],
          ] as const).map(([key, caption, count]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={`flex-1 rounded-xl px-3 py-2.5 text-xs font-bold transition-colors ${
                tab === key
                  ? "bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/15 dark:text-[#93C5FD]"
                  : "text-[#74798C] hover:bg-[#F7F8FC] dark:text-[#9DA3B4] dark:hover:bg-[#232630]"
              }`}
            >
              {caption} ({count})
            </button>
          ))}
        </div>

        {tab === "decided" && decidedList.length > 0 && (
          <div className="flex justify-end px-1">
            <button
              type="button"
              onClick={() => void dismissAllDecided()}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-semibold text-[#74798C] hover:bg-white hover:text-[#1E2230] dark:text-[#9DA3B4] dark:hover:bg-[#232630]"
              title="Chỉ ẩn khỏi danh sách. Lịch và nhắc hẹn giữ nguyên."
            >
              <Trash2 className="h-3.5 w-3.5" /> Xoá tất cả
            </button>
          </div>
        )}

        {(() => {
          const visible = tab === "awaiting" ? awaiting : decidedList;
          const events = visible.filter((proposal) => proposal.action_type === "appointment");
          const tasks = visible.filter((proposal) => proposal.action_type !== "appointment");
          const hasBothTypes = events.length > 0 && tasks.length > 0;

          return <div className="space-y-5">
            {events.length > 0 && <section className="space-y-2.5">
              {hasBothTypes && <p className="px-1 text-xs font-bold uppercase tracking-wide text-sky-700 dark:text-sky-300">{tx(language, "Events")} ({events.length})</p>}
              {events.map(renderProposal)}
            </section>}
            {tasks.length > 0 && <section className="space-y-2.5">
              {hasBothTypes && <p className="px-1 text-xs font-bold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">{tx(language, "Tasks")} ({tasks.length})</p>}
              {tasks.map(renderProposal)}
            </section>}
          </div>;
        })()}

        {!isLoading && (tab === "awaiting" ? awaiting : decidedList).length === 0 && (
          <div className="rounded-2xl border border-dashed border-[#D8DCE7] bg-white px-5 py-12 text-center text-xs text-[#74798C] dark:border-[#3A3F50] dark:bg-[#1C1F27] dark:text-[#9DA3B4]">
            {tab === "awaiting" ? "Không có việc nào đang chờ bạn duyệt." : "Chưa có việc nào đã được xử lý."}
          </div>
        )}
          </>
        )}
        </div>
      </div>
      {editingProposal && (
        <FullEventModal
          token={token}
          initialKind={editingProposal.action_type === "appointment" ? "event" : "task"}
          initialTitle={editingProposal.title}
          initialDate={toLocalInputValue(editingProposal.scheduled_start_at || editingProposal.due_at).slice(0, 10) || undefined}
          initialStartTime={toLocalInputValue(editingProposal.scheduled_start_at || editingProposal.due_at).slice(11) || undefined}
          initialEndTime={toLocalInputValue(editingProposal.scheduled_end_at).slice(11) || undefined}
          initialLocation={editingProposal.location ?? ""}
          initialNote={editingProposal.details ?? ""}
          submitLabel="Duyệt"
          reviewWarning={editingProposal.status === "needs_clarification"
            ? "Chưa có đủ ngày hoặc giờ họp. Bạn có thể điều chỉnh trước khi duyệt."
            : !editingProposal.scheduled_start_at && !editingProposal.due_at
              ? "Chưa có thời gian cụ thể. Vui lòng chọn ngày và giờ trước khi tạo lịch."
              : undefined}
          onClose={() => setEditingProposal(null)}
          onSubmit={(calendarTask) => {
            const reminder = calendarTask.reminders?.find((item) => item.method === "popup");
            void act(
              editingProposal,
              () =>
                confirmActionProposal(token, editingProposal.id, {
                  title: calendarTask.title,
                  details: calendarTask.note ?? null,
                  location: calendarTask.location ?? null,
                  scheduled_start_at: calendarTask.dueAt,
                  scheduled_end_at: calendarTask.endAt ?? null,
                  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                  reminder_minutes_before: reminder?.minutes ?? null,
                }),
              "Đã duyệt và thêm vào lịch",
            );
            setEditingProposal(null);
          }}
        />
      )}
    </section>
  );
};
