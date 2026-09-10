import React from 'react';
import { Pencil, ThumbsDown, ThumbsUp } from 'lucide-react';
import type { FeedbackOverview, FeedbackReviewEntry } from '../../types';

interface FeedbackViewProps {
  overview: FeedbackOverview | null;
  isLoading: boolean;
  interfaceLanguage: 'vi' | 'en';
}

function voteLabel(entry: FeedbackReviewEntry, isVietnamese: boolean) {
  if (entry.entry_type === 'edit') {
    return {
      label: isVietnamese ? 'Chỉnh sửa' : 'Edited translation',
      className: 'bg-violet-50 text-violet-700 ring-violet-200',
      Icon: Pencil,
    };
  }
  if (entry.vote === 'up') {
    return {
      label: isVietnamese ? 'Vote up' : 'Up vote',
      className: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
      Icon: ThumbsUp,
    };
  }
  if (entry.vote === 'down') {
    return {
      label: isVietnamese ? 'Vote down' : 'Down vote',
      className: 'bg-rose-50 text-rose-700 ring-rose-200',
      Icon: ThumbsDown,
    };
  }
  return {
    label: `${isVietnamese ? 'Đánh giá' : 'Rating'} ${entry.rating ?? '—'}/5`,
    className: 'bg-slate-100 text-slate-700 ring-slate-200',
    Icon: ThumbsUp,
  };
}

export const FeedbackView: React.FC<FeedbackViewProps> = ({ overview, isLoading, interfaceLanguage }) => {
  const isVietnamese = interfaceLanguage === 'vi';
  const entries = overview?.review_entries ?? [];
  const editedTranslations = overview?.edited_translations ?? 0;
  const totalSignals = (overview?.votes.total ?? 0) + editedTranslations;
  const locale = isVietnamese ? 'vi-VN' : 'en-US';

  return (
    <section className="space-y-6" aria-labelledby="feedback-review-title">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="feedback-review-title" className="text-xl font-bold tracking-tight text-gray-900">
            {isVietnamese ? 'Phản hồi bản dịch' : 'Translation feedback'}
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            {isVietnamese
              ? 'Theo dõi vote và bản người dùng sửa để kiểm tra chất lượng dịch. Dữ liệu chỉ dành cho quản trị, không hiển thị người dùng hoặc cuộc trò chuyện.'
              : 'Review votes and reader edits to verify translation quality. The admin view omits user and conversation identities.'}
          </p>
        </div>
        <span className="inline-flex w-fit rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700 ring-1 ring-inset ring-blue-200">
          {isVietnamese ? `${entries.length} nhóm phản hồi gần nhất` : `${entries.length} latest feedback groups`}
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-5">
          <div className="flex items-center justify-between"><span className="text-sm font-medium text-emerald-800">{isVietnamese ? 'Vote up' : 'Up votes'}</span><ThumbsUp className="h-5 w-5 text-emerald-600" /></div>
          <p className="mt-3 text-3xl font-bold text-emerald-950">{overview?.votes.up ?? 0}</p>
        </div>
        <div className="rounded-xl border border-rose-100 bg-rose-50/60 p-5">
          <div className="flex items-center justify-between"><span className="text-sm font-medium text-rose-800">{isVietnamese ? 'Vote down' : 'Down votes'}</span><ThumbsDown className="h-5 w-5 text-rose-600" /></div>
          <p className="mt-3 text-3xl font-bold text-rose-950">{overview?.votes.down ?? 0}</p>
        </div>
        <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-5">
          <div className="flex items-center justify-between"><span className="text-sm font-medium text-blue-800">{isVietnamese ? 'Tỷ lệ hữu ích' : 'Helpful rate'}</span><span className="text-lg font-bold text-blue-600">%</span></div>
          <p className="mt-3 text-3xl font-bold text-blue-950">{((overview?.votes.up_rate ?? 0) * 100).toFixed(1)}%</p>
        </div>
        <div className="rounded-xl border border-violet-100 bg-violet-50/60 p-5">
          <div className="flex items-center justify-between"><span className="text-sm font-medium text-violet-800">{isVietnamese ? 'Bản dịch đã sửa' : 'Edited translations'}</span><Pencil className="h-5 w-5 text-violet-600" /></div>
          <p className="mt-3 text-3xl font-bold text-violet-950">{editedTranslations}</p>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xs">
        <div className="flex flex-col gap-1 border-b border-gray-100 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="font-semibold text-gray-900">{isVietnamese ? 'Chi tiết phản hồi' : 'Feedback details'}</h3>
            <p className="mt-0.5 text-xs text-gray-500">{isVietnamese ? 'Bản gốc và bản dịch máy được giữ cạnh vote hoặc phần người dùng chỉnh sửa.' : 'Original and machine translation stay next to each vote or reader edit.'}</p>
          </div>
          <span className="text-xs font-medium text-gray-500">{isVietnamese ? `${entries.length} nhóm / ${totalSignals} tín hiệu` : `${entries.length} groups / ${totalSignals} signals`}</span>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-[1080px] w-full text-left">
            <thead className="border-b border-gray-100 bg-gray-50 text-[11px] font-bold uppercase tracking-wider text-gray-400">
              <tr>
                <th className="px-5 py-3">{isVietnamese ? 'Thời gian' : 'Time'}</th>
                <th className="px-5 py-3">{isVietnamese ? 'Phản hồi' : 'Signal'}</th>
                <th className="px-5 py-3">{isVietnamese ? 'Bản gốc' : 'Original'}</th>
                <th className="px-5 py-3">{isVietnamese ? 'Bản dịch máy' : 'Machine translation'}</th>
                <th className="px-5 py-3">{isVietnamese ? 'Bản người dùng sửa' : 'Reader correction'}</th>
                <th className="px-5 py-3">Model</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-sm text-gray-700">
              {entries.map((entry, index) => {
                const signal = voteLabel(entry, isVietnamese);
                const SignalIcon = signal.Icon;
                return (
                  <tr key={`${entry.entry_type}-${entry.created_at}-${index}`} className="align-top transition-colors hover:bg-gray-50/60">
                    <td className="whitespace-nowrap px-5 py-4 text-xs text-gray-500">{new Date(entry.created_at).toLocaleString(locale)}</td>
                    <td className="px-5 py-4">
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${signal.className}`}><SignalIcon className="h-3.5 w-3.5" />{signal.label}{entry.occurrence_count > 1 && <span className="ml-0.5 border-l border-current/25 pl-1.5">×{entry.occurrence_count}</span>}</span>
                    </td>
                    <td className="max-w-xs px-5 py-4"><p className="whitespace-pre-wrap break-words text-gray-900">{entry.original_text}</p><span className="mt-2 inline-block text-[11px] font-semibold uppercase tracking-wide text-gray-400">{entry.source_language}</span></td>
                    <td className="max-w-xs px-5 py-4"><p className="whitespace-pre-wrap break-words text-gray-900">{entry.translated_text}</p><span className="mt-2 inline-block text-[11px] font-semibold uppercase tracking-wide text-gray-400">{entry.target_language}</span></td>
                    <td className="max-w-xs px-5 py-4"><p className={entry.user_correction ? 'whitespace-pre-wrap break-words text-violet-900' : 'text-gray-400'}>{entry.user_correction || '—'}</p></td>
                    <td className="max-w-[180px] px-5 py-4"><span className="break-all rounded bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-600">{entry.model || '—'}</span></td>
                  </tr>
                );
              })}
              {!isLoading && entries.length === 0 && <tr><td colSpan={6} className="px-6 py-12 text-center text-sm text-gray-500">{isVietnamese ? 'Chưa có vote hoặc bản dịch được người dùng sửa.' : 'No votes or reader edits yet.'}</td></tr>}
              {isLoading && <tr><td colSpan={6} className="px-6 py-12 text-center text-sm text-gray-500">{isVietnamese ? 'Đang tải phản hồi…' : 'Loading feedback…'}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};
