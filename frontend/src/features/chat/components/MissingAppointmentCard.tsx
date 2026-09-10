import React, { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CalendarDays, X } from 'lucide-react';
import { listActionProposals, recoverMessageProposals, type ApiActionProposal } from '../api/chat-api';

/** A recovery affordance for old replies without a persisted proposal, not a
 * synthetic event. Creating an actual calendar entry still requires approval. */
export function MissingAppointmentCard({ token, conversationId, sourceMessageId, onReview }: {
  token: string;
  conversationId: string;
  sourceMessageId: string;
  onReview: (proposals: ApiActionProposal[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);

  async function recover() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    try {
      // A realtime event may have been missed. Check durable data before
      // asking the backend to extract the original message again.
      const existing = (await listActionProposals(token)).filter((proposal) =>
        proposal.conversation_id === conversationId && proposal.source_message_id === sourceMessageId);
      const proposals = existing.length ? existing
        : await recoverMessageProposals(token, conversationId, sourceMessageId);
      const pending = proposals.filter((proposal) =>
        proposal.status === 'pending_confirmation' || proposal.status === 'needs_clarification');
      if (!pending.length) {
        setError(existing.length
          ? 'Đề xuất này đã được xử lý. Bạn có thể kiểm tra trong mục Lịch.'
          : 'Chưa lấy được đề xuất lịch. Hãy gửi lại yêu cầu với ngày, giờ sáng/chiều và nội dung cuộc hẹn.');
        return;
      }
      setOpen(false);
      onReview(pending);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không tải được lịch hẹn. Vui lòng thử lại.');
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return <>
    <div className="-mx-4 -mb-2.5 mt-3 flex items-center gap-2.5 border-t border-[#E8EAF0] px-4 py-3 dark:border-[#363B49]">
      <CalendarDays className="h-5 w-5 flex-none text-[#2563EB]" aria-hidden="true" />
      <span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-[#1E2230] dark:text-[#F5F6FA]">Xem lịch hẹn</span>
        <span className="mt-0.5 block truncate text-[11px] text-amber-700 dark:text-amber-300">Cần xác nhận</span></span>
      <button type="button" onClick={() => setOpen(true)} className="shrink-0 rounded-lg bg-[#2563EB] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[#1D4ED8]">Xem</button>
    </div>
    {open && createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => { if (!busy) setOpen(false); }}>
      <section role="dialog" aria-modal="true" aria-label="Kiểm tra lịch hẹn" className="w-full max-w-md rounded-2xl bg-white p-5 text-[#1E2230] shadow-xl dark:bg-[#232630] dark:text-white" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between"><h2 className="font-semibold">Kiểm tra lịch hẹn</h2><button type="button" aria-label="Đóng" disabled={busy} onClick={() => setOpen(false)}><X className="h-5 w-5" /></button></div>
        <p className="my-4 text-sm">Tin nhắn này chưa có dữ liệu lịch hẹn đi kèm. Bạn có thể lấy lại đề xuất từ tin nhắn gốc để xem và chỉnh sửa. Lịch chỉ được thêm sau khi bạn duyệt.</p>
        {error && <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-300">{error}</p>}
        <button type="button" disabled={busy} onClick={() => void recover()} className="w-full rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{busy ? 'Đang lấy đề xuất…' : 'Lấy lại đề xuất lịch'}</button>
      </section>
    </div>, document.body)}
  </>;
}
