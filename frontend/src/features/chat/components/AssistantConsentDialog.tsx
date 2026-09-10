import React, { useState } from 'react';
import { Bot, ShieldCheck, X } from 'lucide-react';
import type { AgentConsentScope } from '../api/chat-api';

export interface ConsentItem {
  scope: AgentConsentScope;
  title: string;
  detail: string;
}

interface AssistantConsentDialogProps {
  isOpen: boolean;
  items: ConsentItem[];
  /** Scopes already granted; those start ticked so the dialog reflects reality. */
  granted: Partial<Record<AgentConsentScope, boolean>>;
  onCancel: () => void;
  onConfirm: (changes: Partial<Record<AgentConsentScope, boolean>>) => void;
}

/** Asked once when the assistant is switched on: what may it do with your data.
 *
 *  Nothing is ticked on the user's behalf. Pre-ticking a permission box collects
 *  a click, not a decision, and the whole point of this screen is that the
 *  decision is real. The dialog says plainly that declining the first item
 *  leaves the assistant unable to do anything, so an empty answer is an informed
 *  one rather than a confusing one.
 *
 *  Confirming sends the full picture — every listed scope with its chosen value,
 *  including the unticked ones as `false` — so "I did not tick that" is recorded
 *  as a refusal instead of being left as an unanswered question that some later
 *  screen might ask again.
 */
export const AssistantConsentDialog: React.FC<AssistantConsentDialogProps> = ({
  isOpen,
  items,
  granted,
  onCancel,
  onConfirm,
}) => {
  const [selected, setSelected] = useState<Partial<Record<AgentConsentScope, boolean>>>(granted);

  if (!isOpen) return null;

  const toggle = (scope: AgentConsentScope) =>
    setSelected((value) => ({ ...value, [scope]: !value[scope] }));

  const confirm = () =>
    onConfirm(
      Object.fromEntries(items.map((item) => [item.scope, selected[item.scope] === true])),
    );

  const chosen = items.filter((item) => selected[item.scope] === true).length;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-3xl bg-white shadow-2xl dark:bg-[#1C1F27]">
        <header className="flex items-start gap-3 border-b border-[#E8EAF0] p-5 dark:border-[#2A2E3D]">
          <div className="flex h-11 w-11 flex-none items-center justify-center rounded-2xl bg-gradient-to-br from-[#2563EB] to-violet-600 text-white">
            <Bot className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-bold text-[#1E2230] dark:text-[#F5F6FA]">
              Bạn cho phép trợ lý làm gì?
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">
              Trợ lý chỉ làm được những việc bạn tích chọn dưới đây. Bạn đổi lại hoặc rút
              hẳn bất cứ lúc nào trong phần Cài đặt.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Đóng"
            className="rounded-lg p-1 text-[#74798C] hover:bg-[#F7F8FC] dark:hover:bg-[#232630]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-5">
          {items.map((item, index) => {
            const ticked = selected[item.scope] === true;
            return (
              <label
                key={item.scope}
                className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-3.5 transition-colors ${
                  ticked
                    ? 'border-[#2563EB]/40 bg-[#EFF6FF] dark:border-[#2563EB]/40 dark:bg-[#2563EB]/10'
                    : 'border-[#E8EAF0] bg-[#F7F8FC] dark:border-[#2A2E3D] dark:bg-[#232630]/60'
                }`}
              >
                <input
                  type="checkbox"
                  checked={ticked}
                  onChange={() => toggle(item.scope)}
                  className="mt-0.5 h-4 w-4 flex-none accent-[#2563EB]"
                />
                <span className="min-w-0">
                  <span className="block text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">
                    {item.title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-[#74798C] dark:text-[#9DA3B4]">
                    {item.detail}
                  </span>
                  {index === 0 && (
                    <span className="mt-1 block text-xs font-medium text-[#2563EB] dark:text-[#93C5FD]">
                      Không có quyền này thì trợ lý không làm được gì cả.
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-[#E8EAF0] p-5 dark:border-[#2A2E3D]">
          <p className="flex items-center gap-1.5 text-xs text-[#74798C] dark:text-[#9DA3B4]">
            <ShieldCheck className="h-3.5 w-3.5 flex-none" />
            Đã chọn {chosen}/{items.length}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-xl border border-[#E8EAF0] px-4 py-2 text-xs font-semibold text-[#4E5568] hover:bg-[#F7F8FC] dark:border-[#2A2E3D] dark:text-[#C6CAD6] dark:hover:bg-[#232630]"
            >
              Để sau
            </button>
            <button
              type="button"
              onClick={confirm}
              className="rounded-xl bg-[#2563EB] px-4 py-2 text-xs font-bold text-white hover:bg-[#1D4FD7]"
            >
              Xác nhận
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
};
