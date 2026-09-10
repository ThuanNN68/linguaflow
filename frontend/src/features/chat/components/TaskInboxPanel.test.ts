import { describe, expect, it } from "vitest";

import type { ApiActionProposal } from "../api/chat-api";
import { proposalSource } from "./TaskInboxPanel";

const proposal = (name: string, type: "direct" | "group" = "group") => ({
  source_mode: "on_demand",
  source_sender_name: "member@test.com",
  source_conversation_name: name,
  source_conversation_type: type,
} as ApiActionProposal);

describe("proposalSource", () => {
  it("never exposes the assistant conversation's internal key", () => {
    expect(proposalSource(proposal("__linguachat_assistant__")))
      .toBe("Yêu cầu từ member@test.com · Trợ lý thông minh");
  });

  it("keeps ordinary group names labelled as groups", () => {
    expect(proposalSource(proposal("Nhóm thiết kế")))
      .toBe("Yêu cầu từ member@test.com · Nhóm Nhóm thiết kế");
  });
});
