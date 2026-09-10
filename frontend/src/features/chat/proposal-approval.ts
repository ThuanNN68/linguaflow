/** What an approver still decides at the moment they approve a proposal.
 *
 *  Shared by the task inbox and the card the assistant shows inline in the
 *  chat. One definition on purpose: the two are the same decision reached from
 *  two places, and if they disagreed about what a default is, the same proposal
 *  would land on the calendar differently depending on where it was approved.
 *
 *  None of this can be extracted from the message. Nobody writes how much
 *  warning they want in a chat message, and a message rarely states a duration
 *  either — `ProposeCalendarEventArguments` says as much where it declines to
 *  accept them, and leaves both to the approval step.
 */
import type { ApiActionProposal } from "./api/chat-api";

export interface ApprovalOptions {
  /** Event length in minutes. */
  durationMinutes: number;
  /** Minutes of warning, or null for "do not remind me". */
  reminderMinutesBefore: number | null;
}

export const DEFAULT_APPROVAL: ApprovalOptions = {
  durationMinutes: 30,
  reminderMinutesBefore: 15,
};

/** Preserve an end time that was explicitly present in the source message. */
export function approvalOptionsFromProposal(proposal: ApiActionProposal): ApprovalOptions {
  const start = proposal.scheduled_start_at || proposal.due_at;
  const end = proposal.scheduled_end_at;
  if (!start || !end) return DEFAULT_APPROVAL;

  const duration = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60_000);
  return Number.isFinite(duration) && duration >= 15 && duration <= 24 * 60
    ? { ...DEFAULT_APPROVAL, durationMinutes: duration }
    : DEFAULT_APPROVAL;
}

export const DURATION_CHOICES = [15, 30, 45, 60, 90, 120];

export const REMINDER_CHOICES: Array<{ value: number | null; label: string }> = [
  { value: 0, label: "Đúng giờ" },
  { value: 5, label: "5 phút" },
  { value: 15, label: "15 phút" },
  { value: 30, label: "30 phút" },
  { value: 60, label: "1 giờ" },
  { value: 1440, label: "1 ngày" },
  { value: null, label: "Không nhắc" },
];

export function durationLabel(minutes: number): string {
  return minutes < 60 ? `${minutes} phút` : `${minutes / 60} giờ`;
}

/** Whether this proposal is still waiting on a decision. */
export function isAwaitingDecision(proposal: ApiActionProposal): boolean {
  return proposal.status === "pending_confirmation" || proposal.status === "needs_clarification";
}

/** Turn the approver's choices into a `ConfirmProposalRequest` body.
 *
 *  The timezone always travels, the way the clarify call already sends it: the
 *  server stores UTC and has no other way to learn which wall clock the person
 *  was reading. The end time is only sent for a proposal that has a start — a
 *  task with a deadline and no start has no duration to speak of.
 */
export function approvalCorrections(
  proposal: ApiActionProposal,
  chosen: ApprovalOptions,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    reminder_minutes_before: chosen.reminderMinutesBefore,
    resolved_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  const proposalStart = proposal.scheduled_start_at || proposal.due_at;
  if (proposalStart) {
    const start = new Date(proposalStart);
    body.scheduled_end_at = new Date(
      start.getTime() + chosen.durationMinutes * 60_000,
    ).toISOString();
  }
  return body;
}

/** Render a proposal's time the way both surfaces show it. */
export function formatProposalWhen(proposal: ApiActionProposal): string {
  const at = proposal.scheduled_start_at || proposal.due_at;
  if (!at) return "Chưa có thời gian";
  return new Date(at).toLocaleString("vi-VN", {
    weekday: "short",
    day: "numeric",
    month: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The fields a person may correct before approving.
 *
 *  Human-in-the-loop is not only a confirm button: the assistant reads a
 *  sentence someone typed in a hurry, and the title it lifts, the time it
 *  resolves and the place it guesses are all things the owner can see are wrong
 *  at a glance. Letting them fix it here is faster than rejecting and asking
 *  again, and it is the only way to approve a proposal the extractor left
 *  incomplete: `confirm_proposal` clears a field out of `missing_fields` when a
 *  correction supplies it, so a proposal stuck on `needs_clarification` becomes
 *  confirmable the moment the missing value is typed in.
 */
export interface ProposalDraft {
  title: string;
  /** `datetime-local` value, empty when the proposal carries no start. */
  startsAtLocal: string;
  location: string;
}

/** Seed the form from what the assistant proposed. */
export function draftFromProposal(proposal: ApiActionProposal): ProposalDraft {
  return {
    title: proposal.title ?? "",
    startsAtLocal: toLocalInputValue(proposal.scheduled_start_at),
    location: proposal.location ?? "",
  };
}

/** ISO instant -> the `YYYY-MM-DDTHH:mm` a `datetime-local` input wants.
 *
 *  Built from the local parts rather than by slicing `toISOString()`, which
 *  would render the UTC clock and silently shift the time the person sees.
 */
export function toLocalInputValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** Which required fields the extractor could not fill.
 *
 *  `scheduled_time` and `time` are the same gap under two names — the extractor
 *  writes the first, everything that resolves one speaks of the second — so
 *  they are folded together here the way the server folds them.
 */
const MISSING_ALIASES: Record<string, string> = {
  scheduled_time: "time",
  scheduled_start_at: "time",
};

export function missingFields(proposal: ApiActionProposal): string[] {
  try {
    const parsed = JSON.parse(proposal.missing_fields || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is string => typeof item === "string")
      .map((item) => MISSING_ALIASES[item] ?? item);
  } catch {
    return [];
  }
}

/** Whether this proposal can be approved as it stands.
 *
 *  The interface used to offer "Duyệt" on anything undecided, including
 *  proposals the server would refuse with "still has unresolved required
 *  fields" — the button looked available and answered with an error.
 */
export function canApprove(proposal: ApiActionProposal, draft: ProposalDraft): boolean {
  const missing = missingFields(proposal);
  if (missing.includes("time") && !draft.startsAtLocal) return false;
  if (missing.includes("location") && !draft.location.trim()) return false;
  if (missing.includes("title") && !draft.title.trim()) return false;
  return Boolean(draft.title.trim());
}

/** The full `ConfirmProposalRequest` body: the approver's edits and their choices. */
export function decisionCorrections(
  proposal: ApiActionProposal,
  draft: ProposalDraft,
  chosen: ApprovalOptions,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    reminder_minutes_before: chosen.reminderMinutesBefore,
    resolved_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  if (draft.title.trim()) body.title = draft.title.trim();
  if (draft.location.trim()) body.location = draft.location.trim();

  const start = draft.startsAtLocal ? new Date(draft.startsAtLocal) : null;
  if (start && !Number.isNaN(start.getTime())) {
    body.scheduled_start_at = start.toISOString();
    body.scheduled_end_at = new Date(
      start.getTime() + chosen.durationMinutes * 60_000,
    ).toISOString();
  }
  return body;
}
