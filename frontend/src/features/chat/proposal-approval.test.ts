import { describe, expect, it } from "vitest";

import type { ApiActionProposal } from "./api/chat-api";
import { canApprove, draftFromProposal } from "./proposal-approval";

const appointmentWithoutTime: ApiActionProposal = {
  id: "proposal-1",
  conversation_id: "conversation-1",
  source_message_id: "message-1",
  owner_user_id: "user-1",
  action_type: "appointment",
  status: "pending_confirmation",
  title: "Họp dự án",
  details: null,
  location: null,
  scheduled_start_at: null,
  scheduled_end_at: null,
  due_at: null,
  clarification_prompt: null,
  clarification_question: null,
  confidence_score: 0.9,
  source_mode: "on_demand",
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
  confirmed_at: null,
  rejected_at: null,
  stale_at: null,
  missing_fields: "[]",
};

describe("canApprove", () => {
  it("requires a start time for an appointment even when legacy missing_fields is empty", () => {
    expect(canApprove(appointmentWithoutTime, draftFromProposal(appointmentWithoutTime))).toBe(false);
  });

  it("allows approval after the person supplies the appointment time", () => {
    expect(canApprove(appointmentWithoutTime, {
      ...draftFromProposal(appointmentWithoutTime),
      startsAtLocal: "2026-09-12T22:00",
    })).toBe(true);
  });
});
