import { describe, expect, it } from "vitest";

import { errorBoundaryText, shellChromeText, shellText } from "./shell";

describe("shared application-shell translations", () => {
  it("translates navigation outside the chat feature", () => {
    expect(shellText("vi", "settings")).toBe("Cài đặt");
    expect(shellChromeText("ja", "personalCalendar")).toBe("個人カレンダー");
  });

  it("provides localized recovery UI", () => {
    expect(errorBoundaryText("vi", "route").action).toBe("Thử lại");
    expect(errorBoundaryText("ar", "global").title).not.toBe("");
  });
});
