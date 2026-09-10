import React, { useMemo, useState } from 'react';
import { Archive, BookOpen, Check, ChevronDown, Copy, Edit2, Plus, RotateCcw, Search, Trash2, X } from 'lucide-react';
import { TermItem } from '../../types';

type TermDraft = Omit<TermItem, 'id' | 'createdAt' | 'updatedAt'>;

interface GlossaryViewProps {
  terms: TermItem[];
  languageCodes: string[];
  onAddTerm: (term: TermDraft) => Promise<void>;
  onUpdateTerm: (id: string, updatedData: Partial<TermItem>) => Promise<void>;
  onDeleteTerm: (id: string) => Promise<void>;
  onPermanentDeleteTerm: (id: string) => Promise<void>;
  onNotify: (message: string, type?: 'success' | 'info' | 'error') => void;
}

const LANGUAGE_LABELS: Record<string, string> = {
  en: 'English', vi: 'Tiếng Việt', ja: '日本語', zh: '中文', ko: '한국어',
  fr: 'Français', de: 'Deutsch', es: 'Español', th: 'ไทย',
  id: 'Bahasa Indonesia', pt: 'Português', ru: 'Русский', ar: 'العربية', hi: 'हिन्दी',
};
const DOMAIN_OPTIONS = ['engineering', 'commercial', 'support'];
const AUDIENCE_OPTIONS = ['internal', 'client'];

function languageLabel(code: string) {
  return LANGUAGE_LABELS[code] ?? code.toUpperCase();
}

const EMPTY_DRAFT: TermDraft = {
  sourceTerm: '', targetTerm: '', sourceLang: 'en', targetLang: 'vi',
  domain: '', audience: '', keepVerbatim: false, status: 'active',
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'medium' }).format(new Date(value));
}

function csvCell(value: string) {
  return `"${value.replace(/"/g, '""')}"`;
}

export const GlossaryView: React.FC<GlossaryViewProps> = ({
  terms, languageCodes, onAddTerm, onUpdateTerm, onDeleteTerm, onPermanentDeleteTerm, onNotify,
}) => {
  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [targetFilter, setTargetFilter] = useState('all');
  const [domainFilter, setDomainFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<'all' | TermItem['status']>('all');
  const [draft, setDraft] = useState<TermDraft>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<TermItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const domains = useMemo(
    () => Array.from(new Set([...DOMAIN_OPTIONS, ...terms.map((term) => term.domain).filter(Boolean)])).sort(),
    [terms],
  );
  const filteredTerms = useMemo(() => {
    const query = search.trim().toLowerCase();
    return terms.filter((term) => {
      const matchesSearch = !query || [term.sourceTerm, term.targetTerm, term.domain, term.audience]
        .some((value) => value.toLowerCase().includes(query));
      const matchesSource = sourceFilter === 'all' || term.sourceLang === sourceFilter;
      const matchesTarget = targetFilter === 'all' || term.targetLang === targetFilter;
      const matchesDomain = domainFilter === 'all' || term.domain === domainFilter;
      const matchesStatus = statusFilter === 'all' || term.status === statusFilter;
      return matchesSearch && matchesSource && matchesTarget && matchesDomain && matchesStatus;
    });
  }, [domainFilter, search, sourceFilter, statusFilter, targetFilter, terms]);

  const activeCount = terms.filter((term) => term.status === 'active').length;
  const retiredCount = terms.length - activeCount;
  const verbatimCount = terms.filter((term) => term.keepVerbatim).length;

  const openAdd = () => {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setModalOpen(true);
  };

  const openEdit = (term: TermItem) => {
    setEditing(term);
    setDraft({
      sourceTerm: term.sourceTerm, targetTerm: term.targetTerm,
      sourceLang: term.sourceLang, targetLang: term.targetLang,
      domain: term.domain, audience: term.audience,
      keepVerbatim: term.keepVerbatim, status: term.status,
    });
    setModalOpen(true);
  };

  const changeDraft = <K extends keyof TermDraft>(key: K, value: TermDraft[K]) => {
    setDraft((current) => {
      const next = { ...current, [key]: value };
      if (next.keepVerbatim) next.targetTerm = next.sourceTerm;
      return next;
    });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft.sourceTerm.trim() || !draft.targetTerm.trim() || busy) return;
    setBusy(true);
    try {
      const normalized = {
        ...draft,
        sourceTerm: draft.sourceTerm.trim(), targetTerm: draft.targetTerm.trim(),
        domain: draft.domain.trim(), audience: draft.audience.trim(),
      };
      if (editing) {
        await onUpdateTerm(editing.id, normalized);
        onNotify(`Đã cập nhật “${normalized.sourceTerm}”`, 'success');
      } else {
        await onAddTerm(normalized);
        onNotify(`Đã thêm “${normalized.sourceTerm}” vào thuật ngữ dùng chung`, 'success');
      }
      setModalOpen(false);
      setEditing(null);
    } catch {
      onNotify('Không thể lưu thuật ngữ. Hãy kiểm tra dữ liệu hoặc mục trùng lặp.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (term: TermItem) => {
    try {
      if (term.status === 'active') {
        await onDeleteTerm(term.id);
        onNotify(`Đã ngừng áp dụng “${term.sourceTerm}”`, 'info');
      } else {
        await onUpdateTerm(term.id, { status: 'active' });
        onNotify(`Đã khôi phục “${term.sourceTerm}”`, 'success');
      }
    } catch {
      onNotify('Không thể thay đổi trạng thái thuật ngữ.', 'error');
    }
  };

  const permanentlyDelete = async () => {
    if (!confirmingDelete || busy) return;
    const term = terms.find((item) => item.id === confirmingDelete);
    setBusy(true);
    try {
      await onPermanentDeleteTerm(confirmingDelete);
      onNotify(`Đã xóa vĩnh viễn “${term?.sourceTerm ?? ''}”`, 'info');
      setConfirmingDelete(null);
    } catch {
      onNotify('Không thể xóa vĩnh viễn thuật ngữ.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const copy = async (term: TermItem) => {
    await navigator.clipboard.writeText(`${term.sourceTerm} → ${term.targetTerm}`);
    setCopiedId(term.id);
    window.setTimeout(() => setCopiedId(null), 1600);
  };

  const exportCsv = () => {
    const header = ['source_term', 'target_term', 'source_language', 'target_language', 'domain', 'audience', 'keep_verbatim', 'status', 'updated_at'];
    const rows = filteredTerms.map((term) => [
      csvCell(term.sourceTerm), csvCell(term.targetTerm), term.sourceLang, term.targetLang,
      csvCell(term.domain), csvCell(term.audience), String(term.keepVerbatim), term.status, term.updatedAt,
    ].join(','));
    const blob = new Blob([`\uFEFF${[header.join(','), ...rows].join('\n')}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `linguaflow-glossary-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          ['Tổng thuật ngữ', terms.length, 'Toàn bộ kho dùng chung'],
          ['Đang áp dụng', activeCount, 'Được nạp vào bản dịch'],
          ['Đã ngừng', retiredCount, 'Có thể khôi phục'],
          ['Giữ nguyên văn', verbatimCount, 'Không dịch sang ngôn ngữ đích'],
        ].map(([label, value, hint]) => (
          <div key={String(label)} className="bg-white p-5 rounded-xl border border-gray-200 shadow-xs">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
            <div className="mt-2 text-2xl font-bold text-gray-900">{value}</div>
            <div className="mt-2 text-xs text-gray-400">{hint}</div>
          </div>
        ))}
      </div>

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="space-y-5 border-b border-gray-100 bg-gradient-to-b from-white to-slate-50/40 p-5 lg:p-6">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-3"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><BookOpen className="h-5 w-5" /></span><div><h2 className="font-semibold text-gray-900">Thuật ngữ đang quản lý</h2><p className="mt-1 text-xs text-gray-500">Đồng bộ trực tiếp với hệ thống dịch; phạm vi trống áp dụng cho mọi hội thoại.</p></div></div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={exportCsv} className="rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-xs font-semibold text-gray-700 hover:bg-gray-50">Xuất CSV</button>
              <button type="button" onClick={openAdd} className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"><Plus className="w-4 h-4" /> Thêm thuật ngữ</button>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between"><span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">Tìm kiếm và lọc</span><span className="text-[11px] text-gray-400">{filteredTerms.length}/{terms.length} thuật ngữ</span></div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(210px,1fr)_180px_180px_150px_150px]">
            <label className="relative sm:col-span-2 md:col-span-1"><span className="sr-only">Tìm kiếm thuật ngữ</span><Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm từ gốc, bản dịch, domain, audience…" className="w-full pl-9 pr-3 py-2.5 text-xs border border-gray-300 rounded-lg outline-hidden focus:border-blue-600 focus:ring-2 focus:ring-blue-100" /></label>
            <SelectControl aria-label="Ngôn ngữ nguồn" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} className="w-full px-3 py-2.5 text-xs border border-gray-300 rounded-lg bg-white"><option value="all">Mọi ngôn ngữ nguồn</option>{languageCodes.map((code) => <option key={code} value={code}>{languageLabel(code)} ({code.toUpperCase()})</option>)}</SelectControl>
            <SelectControl aria-label="Ngôn ngữ đích" value={targetFilter} onChange={(event) => setTargetFilter(event.target.value)} className="w-full px-3 py-2.5 text-xs border border-gray-300 rounded-lg bg-white"><option value="all">Mọi ngôn ngữ đích</option>{languageCodes.map((code) => <option key={code} value={code}>{languageLabel(code)} ({code.toUpperCase()})</option>)}</SelectControl>
            <SelectControl aria-label="Domain" value={domainFilter} onChange={(event) => setDomainFilter(event.target.value)} className="w-full px-3 py-2.5 text-xs border border-gray-300 rounded-lg bg-white"><option value="all">Mọi domain</option>{domains.map((domain) => <option key={domain} value={domain}>{domain}</option>)}</SelectControl>
            <SelectControl aria-label="Trạng thái" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)} className="w-full px-3 py-2.5 text-xs border border-gray-300 rounded-lg bg-white"><option value="all">Mọi trạng thái</option><option value="active">Đang áp dụng</option><option value="retired">Đã ngừng</option></SelectControl>
            </div>
          </div>
        </div>

        {filteredTerms.length === 0 ? (
          <div className="p-12 text-center"><BookOpen className="w-8 h-8 text-gray-300 mx-auto mb-3" /><p className="text-sm font-medium text-gray-700">Chưa có thuật ngữ phù hợp</p><p className="text-xs text-gray-400 mt-1">Đổi bộ lọc hoặc thêm thuật ngữ mới.</p></div>
        ) : (
          <>
          <div className="grid gap-3 p-4 lg:hidden">
            {filteredTerms.map((term) => {
              const retired = term.status === 'retired';
              return (
                <article key={term.id} className="rounded-xl border border-gray-200 bg-white p-4 shadow-xs">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-mono font-semibold text-gray-900 break-all">{term.sourceTerm}</span><span className="text-gray-300">→</span><span className="font-semibold text-blue-700 break-all">{term.targetTerm}</span><button type="button" onClick={() => void copy(term)} title="Sao chép" className="text-gray-400 hover:text-gray-700">{copiedId === term.id ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}</button></div><div className="mt-2 flex flex-wrap gap-2"><span className="font-mono text-[11px] px-2 py-1 rounded-md bg-gray-100 text-gray-700">{term.sourceLang.toUpperCase()} → {term.targetLang.toUpperCase()}</span><span className={`text-[11px] px-2 py-1 rounded-md ${term.keepVerbatim ? 'bg-violet-50 text-violet-700' : 'bg-blue-50 text-blue-700'}`}>{term.keepVerbatim ? 'Giữ nguyên văn' : 'Dùng bản dịch chuẩn'}</span></div></div>
                    <span className={`shrink-0 inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[11px] ${retired ? 'bg-gray-100 text-gray-600' : 'bg-green-50 text-green-700'}`}><span className={`w-1.5 h-1.5 rounded-full ${retired ? 'bg-gray-400' : 'bg-green-500'}`} />{retired ? 'Đã ngừng' : 'Đang áp dụng'}</span>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-3 rounded-lg bg-gray-50 p-3 text-xs"><div><div className="text-gray-400">Domain</div><div className="mt-1 font-medium text-gray-700">{term.domain || 'Mọi domain'}</div></div><div><div className="text-gray-400">Audience</div><div className="mt-1 font-medium text-gray-700">{term.audience || 'Mọi đối tượng'}</div></div></div>
                  <div className="mt-3 flex items-center justify-between"><span className="text-[11px] text-gray-400">Cập nhật {formatDate(term.updatedAt)}</span><div className="flex items-center gap-1">{!retired && <button type="button" onClick={() => openEdit(term)} className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg" title="Chỉnh sửa"><Edit2 className="w-4 h-4" /></button>}<button type="button" onClick={() => void changeStatus(term)} className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg" title={retired ? 'Khôi phục' : 'Ngừng áp dụng'}>{retired ? <RotateCcw className="w-4 h-4" /> : <Archive className="w-4 h-4" />}</button>{retired && <button type="button" onClick={() => setConfirmingDelete(term.id)} className="p-2 text-gray-500 hover:text-rose-600 hover:bg-rose-50 rounded-lg" title="Xóa vĩnh viễn"><Trash2 className="w-4 h-4" /></button>}</div></div>
                </article>
              );
            })}
          </div>
          <div className="hidden overflow-x-auto lg:block">
            <table className="w-full text-left text-xs">
              <thead className="bg-gray-50 text-gray-500 uppercase tracking-wider border-b border-gray-200"><tr><th className="px-5 py-3">Nguồn</th><th className="px-5 py-3">Bản dịch</th><th className="px-5 py-3">Cặp</th><th className="px-5 py-3">Phạm vi</th><th className="px-5 py-3">Trạng thái</th><th className="px-5 py-3">Cập nhật</th><th className="px-5 py-3 text-right">Thao tác</th></tr></thead>
              <tbody className="divide-y divide-gray-100">
                {filteredTerms.map((term) => {
                  const retired = term.status === 'retired';
                  return (
                    <tr key={term.id} className="hover:bg-gray-50/70">
                      <td className="px-5 py-4 min-w-44"><span className="font-mono font-semibold text-gray-900">{term.sourceTerm}</span></td>
                      <td className="px-5 py-4 min-w-48"><div className="flex items-center gap-2"><span className="font-medium text-blue-700">{term.targetTerm}</span><button type="button" onClick={() => void copy(term)} title="Sao chép cặp thuật ngữ" className="text-gray-400 hover:text-gray-700">{copiedId === term.id ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}</button></div></td>
                      <td className="px-5 py-4 whitespace-nowrap"><span className="font-mono px-2 py-1 rounded bg-gray-100 text-gray-700">{term.sourceLang.toUpperCase()} → {term.targetLang.toUpperCase()}</span></td>
                      <td className="px-5 py-4"><div className="text-gray-700">{term.domain || 'Mọi domain'}</div><div className="text-gray-400 mt-0.5">{term.audience || 'Mọi đối tượng'}</div></td>
                      <td className="px-5 py-4"><span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full ${retired ? 'bg-gray-100 text-gray-600' : 'bg-green-50 text-green-700'}`}><span className={`w-1.5 h-1.5 rounded-full ${retired ? 'bg-gray-400' : 'bg-green-500'}`} />{retired ? 'Đã ngừng' : 'Đang áp dụng'}</span></td>
                      <td className="px-5 py-4 whitespace-nowrap text-gray-500">{formatDate(term.updatedAt)}</td>
                      <td className="px-5 py-4"><div className="flex items-center justify-end gap-1">{!retired && <button type="button" onClick={() => openEdit(term)} className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded" title="Chỉnh sửa"><Edit2 className="w-4 h-4" /></button>}<button type="button" onClick={() => void changeStatus(term)} className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded" title={retired ? 'Khôi phục' : 'Ngừng áp dụng'}>{retired ? <RotateCcw className="w-4 h-4" /> : <Archive className="w-4 h-4" />}</button>{retired && <button type="button" onClick={() => setConfirmingDelete(term.id)} className="p-2 text-gray-500 hover:text-rose-600 hover:bg-rose-50 rounded" title="Xóa vĩnh viễn"><Trash2 className="w-4 h-4" /></button>}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4 backdrop-blur-sm" onClick={() => setModalOpen(false)}>
          <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-gray-200 bg-white shadow-2xl shadow-slate-900/20" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between border-b border-blue-100 bg-gradient-to-r from-blue-50/80 via-white to-white px-6 py-5">
              <div className="flex items-center gap-3">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-600/25"><BookOpen className="h-5 w-5" /></span>
                <div><h3 className="text-base font-semibold text-gray-900">{editing ? 'Chỉnh sửa thuật ngữ' : 'Thêm thuật ngữ'}</h3><p className="mt-1 text-xs text-gray-500">Mục từ sẽ được áp dụng trực tiếp cho các bản dịch phù hợp.</p></div>
              </div>
              <button type="button" aria-label="Đóng" onClick={() => setModalOpen(false)} className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-white hover:text-gray-700 hover:shadow-sm"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={submit} className="space-y-5 p-6 text-xs">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Ngôn ngữ nguồn"><SelectControl value={draft.sourceLang} disabled={Boolean(editing)} onChange={(event) => changeDraft('sourceLang', event.target.value)} className="w-full p-2.5 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900 disabled:bg-gray-100">{languageCodes.map((code) => <option key={code} value={code}>{languageLabel(code)} ({code.toUpperCase()})</option>)}</SelectControl></Field>
                <Field label="Ngôn ngữ đích"><SelectControl value={draft.targetLang} disabled={Boolean(editing)} onChange={(event) => changeDraft('targetLang', event.target.value)} className="w-full p-2.5 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900 disabled:bg-gray-100">{languageCodes.map((code) => <option key={code} value={code}>{languageLabel(code)} ({code.toUpperCase()})</option>)}</SelectControl></Field>
              </div>
              {editing && <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md p-2">Backend không cho đổi cặp ngôn ngữ của mục đã tạo. Hãy ngừng mục cũ và tạo mục mới nếu cặp bị sai.</p>}
              <Field label="Thuật ngữ nguồn"><input required maxLength={200} value={draft.sourceTerm} onChange={(event) => changeDraft('sourceTerm', event.target.value)} className="w-full p-2.5 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900" /></Field>
              <Field label="Thuật ngữ đích"><input required maxLength={200} value={draft.targetTerm} disabled={draft.keepVerbatim} onChange={(event) => changeDraft('targetTerm', event.target.value)} className="w-full p-2.5 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900 disabled:bg-gray-100" /></Field>
              <div className="rounded-xl border border-gray-200 bg-gray-50/70 p-4">
                <div className="mb-3"><h4 className="font-semibold text-gray-800">Phạm vi áp dụng</h4><p className="mt-1 text-gray-500">Chọn lĩnh vực và nhóm người dùng mà thuật ngữ này được ưu tiên.</p></div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <ScopeSelect label="Domain" value={draft.domain} options={DOMAIN_OPTIONS} onChange={(value) => changeDraft('domain', value)} />
                  <ScopeSelect label="Audience" value={draft.audience} options={AUDIENCE_OPTIONS} onChange={(value) => changeDraft('audience', value)} />
                </div>
                <p className="mt-3 text-gray-400">Không chọn phạm vi nghĩa là thuật ngữ được áp dụng cho mọi hội thoại phù hợp.</p>
              </div>
              <label className="flex items-start gap-3 p-3 bg-gray-50 border border-gray-200 rounded-lg cursor-pointer">
                <span className="relative mt-0.5 h-4 w-4 shrink-0">
                  <input
                    type="checkbox"
                    checked={draft.keepVerbatim}
                    onChange={(event) => changeDraft('keepVerbatim', event.target.checked)}
                    className="peer absolute inset-0 h-4 w-4 cursor-pointer appearance-none rounded border border-gray-300 bg-white checked:border-blue-600 checked:bg-blue-600 focus-visible:ring-2 focus-visible:ring-blue-200"
                  />
                  <Check className="pointer-events-none absolute left-0.5 top-0.5 h-3 w-3 text-white opacity-0 peer-checked:opacity-100" strokeWidth={3} />
                </span>
                <span><strong className="block text-gray-800">Giữ nguyên văn</strong><span className="text-gray-500">Không dịch thuật ngữ; giá trị đích sẽ luôn bằng giá trị nguồn.</span></span>
              </label>
              <div className="-mx-6 -mb-6 flex justify-end gap-2 border-t border-gray-200 bg-gray-50 px-6 py-4"><button type="button" onClick={() => setModalOpen(false)} className="rounded-lg border border-gray-300 bg-white px-4 py-2 font-medium text-gray-700 transition-colors hover:bg-gray-100">Hủy</button><button type="submit" disabled={busy || !draft.sourceTerm.trim() || !draft.targetTerm.trim()} className="rounded-lg bg-blue-600 px-5 py-2 font-semibold text-white shadow-sm shadow-blue-600/20 transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{busy ? 'Đang lưu…' : editing ? 'Lưu thay đổi' : 'Thêm thuật ngữ'}</button></div>
            </form>
          </div>
        </div>
      )}

      {confirmingDelete && (
        <div className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4" onClick={() => setConfirmingDelete(null)}>
          <div className="bg-white rounded-xl max-w-sm w-full p-6 shadow-lg border border-gray-200" onClick={(event) => event.stopPropagation()}>
            <div className="w-10 h-10 rounded-full bg-rose-50 text-rose-600 flex items-center justify-center mx-auto"><Trash2 className="w-5 h-5" /></div>
            <h3 className="text-center font-semibold text-gray-900 mt-4">Xóa vĩnh viễn thuật ngữ?</h3>
            <p className="text-center text-xs text-gray-500 mt-2">Chỉ mục đã ngừng áp dụng mới có thể xóa. Hành động này không thể hoàn tác.</p>
            <div className="grid grid-cols-2 gap-2 mt-5"><button type="button" onClick={() => setConfirmingDelete(null)} className="py-2 border border-gray-300 rounded-md text-xs">Hủy</button><button type="button" onClick={() => void permanentlyDelete()} disabled={busy} className="py-2 bg-rose-600 text-white rounded-md text-xs disabled:opacity-50">Xóa vĩnh viễn</button></div>
          </div>
        </div>
      )}
    </div>
  );
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="block font-medium text-gray-700 mb-1">{label}</span>{children}</label>;
}

function SelectControl({ className = '', children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="relative block">
      <select {...props} className={`${className} appearance-none pr-10`}>
        {children}
      </select>
      <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" strokeWidth={2.25} />
    </span>
  );
}

function ScopeSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const optionLabels: Record<string, string> = label === 'Domain'
    ? { engineering: 'Kỹ thuật & Công nghệ', commercial: 'Kinh doanh & Thương mại', support: 'Chăm sóc & Hỗ trợ' }
    : { internal: 'Nội bộ', client: 'Khách hàng' };
  const emptyLabel = label === 'Domain' ? 'Tất cả lĩnh vực' : 'Tất cả đối tượng';

  return (
    <div>
      <label className="block font-medium text-gray-700 mb-1">{label}</label>
      <SelectControl
        aria-label={label}
        value={options.includes(value) ? value : ''}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-gray-300 bg-white p-2.5 text-gray-900 outline-hidden transition-colors focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
      >
        <option value="">{emptyLabel}</option>
        {options.map((option) => <option key={option} value={option}>{optionLabels[option] ?? option}</option>)}
      </SelectControl>
    </div>
  );
}
