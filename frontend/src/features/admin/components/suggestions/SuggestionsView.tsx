import React, { useState, useMemo } from 'react';
import {
  Lightbulb,
  CheckCircle,
  XCircle,
  Clock,
  MessageSquare,
  Search,
  Eye,
  Check,
  X,
  Tag
} from 'lucide-react';
import confetti from 'canvas-confetti';
import { TranslationSuggestion } from '../../types';

interface SuggestionsViewProps {
  suggestions: TranslationSuggestion[];
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, reason?: string) => Promise<void>;
  onNotify: (message: string, type?: 'success' | 'info' | 'error') => void;
}

export const SuggestionsView: React.FC<SuggestionsViewProps> = ({
  suggestions,
  onApprove,
  onReject,
  onNotify,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selectedSuggestion, setSelectedSuggestion] = useState<TranslationSuggestion | null>(null);

  // Rejection modal
  const [rejectingItem, setRejectingItem] = useState<TranslationSuggestion | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  // Approval modal
  const [approvingItem, setApprovingItem] = useState<TranslationSuggestion | null>(null);

  // Filtered suggestions
  const filteredSuggestions = useMemo(() => {
    return suggestions.filter((item) => {
      const matchSearch =
        item.sourceText.toLowerCase().includes(searchTerm.toLowerCase()) ||
        item.suggestedTranslation.toLowerCase().includes(searchTerm.toLowerCase()) ||
        item.currentAiTranslation.toLowerCase().includes(searchTerm.toLowerCase()) ||
        item.user.toLowerCase().includes(searchTerm.toLowerCase()) ||
        item.reason.toLowerCase().includes(searchTerm.toLowerCase());

      const matchStatus = statusFilter === 'all' || item.status === statusFilter;

      return matchSearch && matchStatus;
    });
  }, [suggestions, searchTerm, statusFilter]);

  // Counts
  const pendingCount = suggestions.filter((s) => s.status === 'pending').length;
  const approvedCount = suggestions.filter((s) => s.status === 'approved').length;
  const rejectedCount = suggestions.filter((s) => s.status === 'rejected').length;

  const handleOpenApprove = (item: TranslationSuggestion) => {
    setApprovingItem(item);
  };

  const handleConfirmApprove = async () => {
    if (!approvingItem) return;
    try {
      await onApprove(approvingItem.id);
      confetti({ particleCount: 50, spread: 60, origin: { y: 0.8 } });
      onNotify(`Đã duyệt và áp dụng thuật ngữ "${approvingItem.suggestedTranslation.slice(0, 30)}..."`, 'success');
      setApprovingItem(null);
    } catch {
      onNotify('Không thể duyệt đề xuất. Vui lòng thử lại.', 'error');
    }
  };

  const handleOpenReject = (item: TranslationSuggestion) => {
    setRejectingItem(item);
    setRejectReason('');
  };

  const handleConfirmReject = async () => {
    if (!rejectingItem) return;
    try {
      await onReject(rejectingItem.id, rejectReason || 'Chưa phù hợp với ngữ cảnh tiêu chuẩn LinguaFlow');
      onNotify(`Đã từ chối đề xuất của ${rejectingItem.user}`, 'info');
      setRejectingItem(null);
    } catch {
      onNotify('Không thể từ chối đề xuất. Vui lòng thử lại.', 'error');
    }
  };

  // Helper to render simple text comparison highlighting
  const renderHighlightedDiff = (original: string, suggested: string) => {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div className="p-3 bg-rose-50/50 rounded-lg border border-rose-100">
          <span className="text-[10px] font-bold uppercase tracking-wider text-rose-700 block mb-1">
            Bản dịch AI hiện tại
          </span>
          <p className="text-gray-800 leading-relaxed font-sans">{original}</p>
        </div>

        <div className="p-3 bg-green-50/50 rounded-lg border border-green-100">
          <span className="text-[10px] font-bold uppercase tracking-wider text-green-700 block mb-1">
            Đề xuất sửa đổi
          </span>
          <p className="text-gray-900 font-semibold leading-relaxed font-sans">{suggested}</p>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Overview Status Ribbon */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <button
          id="tab-filter-pending"
          onClick={() => setStatusFilter(statusFilter === 'pending' ? 'all' : 'pending')}
          className={`bg-white p-5 rounded-xl border text-left transition-colors shadow-xs flex flex-col justify-between ${
            statusFilter === 'pending'
              ? 'border-blue-600 ring-1 ring-blue-600'
              : 'border-gray-200 hover:border-gray-300'
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Đang chờ duyệt</span>
            <div className="p-1 rounded bg-amber-50 text-amber-700">
              <Clock className="w-4 h-4" />
            </div>
          </div>
          <div className="mt-2 text-2xl font-bold text-gray-900 tracking-tight">{pendingCount}</div>
          <div className="mt-2 text-xs text-amber-600 font-medium">Cần chuyên viên quản trị phản hồi</div>
        </button>

        <button
          id="tab-filter-approved"
          onClick={() => setStatusFilter(statusFilter === 'approved' ? 'all' : 'approved')}
          className={`bg-white p-5 rounded-xl border text-left transition-colors shadow-xs flex flex-col justify-between ${
            statusFilter === 'approved'
              ? 'border-blue-600 ring-1 ring-blue-600'
              : 'border-gray-200 hover:border-gray-300'
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Đã phê duyệt</span>
            <div className="p-1 rounded bg-green-50 text-green-700">
              <CheckCircle className="w-4 h-4" />
            </div>
          </div>
          <div className="mt-2 text-2xl font-bold text-gray-900 tracking-tight">{approvedCount}</div>
          <div className="mt-2 text-xs text-green-600 font-medium">Đã được lưu thành thuật ngữ áp dụng</div>
        </button>

        <button
          id="tab-filter-rejected"
          onClick={() => setStatusFilter(statusFilter === 'rejected' ? 'all' : 'rejected')}
          className={`bg-white p-5 rounded-xl border text-left transition-colors shadow-xs flex flex-col justify-between ${
            statusFilter === 'rejected'
              ? 'border-blue-600 ring-1 ring-blue-600'
              : 'border-gray-200 hover:border-gray-300'
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Đã từ chối</span>
            <div className="p-1 rounded bg-gray-100 text-gray-600">
              <XCircle className="w-4 h-4" />
            </div>
          </div>
          <div className="mt-2 text-2xl font-bold text-gray-900 tracking-tight">{rejectedCount}</div>
          <div className="mt-2 text-xs text-gray-400 font-medium">Không đáp ứng chuẩn ngữ cảnh</div>
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white rounded-xl p-5 border border-gray-200 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        {/* Search */}
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            id="input-suggestions-search"
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Tìm kiếm theo câu gốc, đề xuất, người gửi hoặc lý do..."
            className="w-full pl-9 pr-4 py-2 text-xs sm:text-sm bg-white border border-gray-300 rounded-md focus:border-blue-600 outline-hidden transition-colors"
          />
        </div>

        {/* Status Filter Tabs */}
        <div className="inline-flex p-1 bg-gray-100 rounded-lg text-xs font-medium shrink-0">
          <button
            onClick={() => setStatusFilter('all')}
            className={`px-3 py-1.5 rounded-md transition-all ${
              statusFilter === 'all'
                ? 'bg-white text-gray-900 shadow-xs font-semibold'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Tất cả
          </button>
          <button
            onClick={() => setStatusFilter('pending')}
            className={`px-3 py-1.5 rounded-md transition-all ${
              statusFilter === 'pending'
                ? 'bg-white text-gray-900 shadow-xs font-semibold'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Chờ duyệt
          </button>
          <button
            onClick={() => setStatusFilter('approved')}
            className={`px-3 py-1.5 rounded-md transition-all ${
              statusFilter === 'approved'
                ? 'bg-white text-gray-900 shadow-xs font-semibold'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Đã duyệt
          </button>
          <button
            onClick={() => setStatusFilter('rejected')}
            className={`px-3 py-1.5 rounded-md transition-all ${
              statusFilter === 'rejected'
                ? 'bg-white text-gray-900 shadow-xs font-semibold'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Đã từ chối
          </button>
        </div>
      </div>

      {/* Suggestions List */}
      <div className="space-y-4">
        {filteredSuggestions.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 p-12 text-center space-y-3">
            <div className="w-10 h-10 rounded-full bg-gray-100 text-gray-400 mx-auto flex items-center justify-center">
              <Lightbulb className="w-5 h-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-900">Không có đề xuất nào</p>
              <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                Không tìm thấy đề xuất bản dịch nào khớp với bộ lọc hiện tại của bạn.
              </p>
            </div>
          </div>
        ) : (
          filteredSuggestions.map((item) => (
            <div
              key={item.id}
              id={`suggestion-card-${item.id}`}
              className={`bg-white rounded-xl border transition-colors shadow-xs p-5 space-y-4 ${
                item.status === 'pending'
                  ? 'border-amber-200 hover:border-amber-300'
                  : item.status === 'approved'
                  ? 'border-gray-200'
                  : 'border-gray-200 opacity-80'
              }`}
            >
              {/* Header Info: User, Time, Status badge */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-gray-100">
                <div className="flex items-center gap-3">
                  <div className="w-7 h-7 rounded-full bg-gray-100 text-gray-700 flex items-center justify-center font-bold text-xs">
                    {item.user.charAt(0)}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-gray-900 text-xs sm:text-sm">{item.user}</span>
                      <span className="text-xs text-gray-400">({item.userEmail})</span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-gray-400 mt-0.5">
                      <span>{item.submittedAt}</span>
                      {item.domain && (
                        <>
                          <span>•</span>
                          <span className="inline-flex items-center gap-1 text-gray-600">
                            <Tag className="w-3 h-3" />
                            {item.domain}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-medium px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                    {item.sourceLang} → {item.targetLang}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${
                      item.status === 'pending'
                        ? 'bg-amber-50 text-amber-700'
                        : item.status === 'approved'
                        ? 'bg-green-50 text-green-700'
                        : 'bg-gray-100 text-gray-600'
                    }`}
                  >
                    {item.status === 'pending' && <Clock className="w-3 h-3 text-amber-600" />}
                    {item.status === 'approved' && <CheckCircle className="w-3 h-3 text-green-600" />}
                    {item.status === 'rejected' && <XCircle className="w-3 h-3 text-gray-500" />}
                    {item.status === 'pending'
                      ? 'Chờ duyệt'
                      : item.status === 'approved'
                      ? 'Đã duyệt'
                      : 'Đã từ chối'}
                  </span>
                </div>
              </div>

              {/* Source Text */}
              <div>
                <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400 block mb-1">
                  Câu gốc (Source text)
                </span>
                <p className="text-sm font-medium text-gray-900 bg-gray-50 p-3 rounded-lg border border-gray-100">
                  {item.sourceText}
                </p>
              </div>

              {/* Comparison: AI Current vs User Suggested */}
              {renderHighlightedDiff(item.currentAiTranslation, item.suggestedTranslation)}

              {/* Reason / Context */}
              <div className="p-3 bg-gray-50 rounded-lg border border-gray-100 text-xs">
                <span className="font-medium text-gray-700 flex items-center gap-1.5 mb-1">
                  <MessageSquare className="w-3.5 h-3.5 text-blue-600" />
                  Lý do đề xuất từ người dùng:
                </span>
                <p className="text-gray-600 italic">&ldquo;{item.reason}&rdquo;</p>
              </div>

              {/* Review notes if already processed */}
              {item.reviewNotes && (
                <div className="p-3 bg-blue-50/60 rounded-lg border border-blue-100 text-xs text-blue-900">
                  <span className="font-medium block mb-0.5">Ghi chú từ {item.reviewedBy}:</span>
                  <p>{item.reviewNotes}</p>
                </div>
              )}

              {/* Actions Footer */}
              <div className="pt-2 flex items-center justify-between">
                <button
                  id={`btn-view-details-${item.id}`}
                  onClick={() => setSelectedSuggestion(item)}
                  className="text-xs font-medium text-blue-600 hover:text-blue-800 inline-flex items-center gap-1"
                >
                  <Eye className="w-3.5 h-3.5" /> Xem phân tích chi tiết
                </button>

                {item.status === 'pending' && (
                  <div className="flex items-center gap-2">
                    <button
                      id={`btn-reject-sug-${item.id}`}
                      onClick={() => handleOpenReject(item)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-gray-300 hover:bg-gray-50 text-gray-700 text-xs font-medium transition-colors"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>Từ chối</span>
                    </button>
                    <button
                      id={`btn-approve-sug-${item.id}`}
                      onClick={() => handleOpenApprove(item)}
                      className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md bg-green-600 hover:bg-green-700 text-white text-xs font-medium shadow-xs transition-colors"
                    >
                      <Check className="w-3.5 h-3.5" />
                      <span>Duyệt đề xuất</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Approve Modal */}
      {approvingItem && (
        <div
          id="approve-modal"
          className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4"
          onClick={() => setApprovingItem(null)}
        >
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 shadow-lg border border-gray-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 pb-3 border-b border-gray-100">
              <div className="w-8 h-8 rounded-full bg-green-50 text-green-600 flex items-center justify-center">
                <CheckCircle className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-semibold text-gray-900 text-base">Phê duyệt bản dịch đề xuất</h3>
                <p className="text-xs text-gray-400">Áp dụng chuẩn dịch mới vào hệ thống</p>
              </div>
            </div>

            <div className="p-3 bg-green-50/50 rounded-lg border border-green-100 text-xs">
              <span className="font-medium text-green-900 block mb-1">Bản dịch được duyệt:</span>
              <p className="text-gray-900 font-medium">{approvingItem.suggestedTranslation}</p>
            </div>

            <div className="rounded-lg border border-green-100 bg-green-50/50 p-3 text-xs text-green-900">
              Khi duyệt, đề xuất sẽ được lưu thành thuật ngữ đang áp dụng cho các lần dịch sau. Thao tác này dùng dữ liệu backend, không chỉ cập nhật giao diện.
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                onClick={() => setApprovingItem(null)}
                className="px-4 py-2 font-medium text-gray-700 hover:bg-gray-100 rounded-md"
              >
                Hủy
              </button>
              <button
                onClick={handleConfirmApprove}
                className="px-4 py-2 font-medium text-white bg-green-600 hover:bg-green-700 rounded-md shadow-xs"
              >
                Xác nhận Duyệt
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Modal */}
      {rejectingItem && (
        <div
          id="reject-modal"
          className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4"
          onClick={() => setRejectingItem(null)}
        >
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 shadow-lg border border-gray-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 pb-3 border-b border-gray-100">
              <div className="w-8 h-8 rounded-full bg-rose-50 text-rose-600 flex items-center justify-center">
                <XCircle className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-semibold text-gray-900 text-base">Từ chối đề xuất chỉnh sửa</h3>
                <p className="text-xs text-gray-400">Giữ nguyên bản dịch AI hiện tại</p>
              </div>
            </div>

            <div className="text-xs space-y-2">
              <label className="font-medium text-gray-700 block">
                Lý do từ chối phản hồi lại người dùng:
              </label>
              <textarea
                rows={3}
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Ví dụ: Bản dịch ban đầu phù hợp với ngữ cảnh trang trọng hơn..."
                className="w-full p-2 bg-white border border-gray-300 rounded-md outline-hidden focus:border-blue-600 text-gray-900"
              />
            </div>

            <div className="pt-2 flex justify-end gap-2 text-xs">
              <button
                onClick={() => setRejectingItem(null)}
                className="px-4 py-2 font-medium text-gray-700 hover:bg-gray-100 rounded-md"
              >
                Hủy
              </button>
              <button
                onClick={handleConfirmReject}
                className="px-4 py-2 font-medium text-white bg-rose-600 hover:bg-rose-700 rounded-md shadow-xs"
              >
                Xác nhận Từ chối
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Suggestion Detail Modal */}
      {selectedSuggestion && (
        <div
          id="suggestion-detail-modal"
          className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4"
          onClick={() => setSelectedSuggestion(null)}
        >
          <div
            className="bg-white rounded-xl max-w-lg w-full p-6 shadow-lg border border-gray-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-3 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-gray-900 text-base">Chi tiết Đề xuất</h3>
                <span className="text-xs font-mono text-gray-600 bg-gray-100 px-2 py-0.5 rounded">
                  {selectedSuggestion.id}
                </span>
              </div>
              <button
                onClick={() => setSelectedSuggestion(null)}
                className="text-gray-400 hover:text-gray-700 p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-2 p-3 bg-gray-50 rounded-lg">
                <div>
                  <span className="text-gray-400 block">Người gửi:</span>
                  <span className="font-medium text-gray-900">{selectedSuggestion.user}</span>
                </div>
                <div>
                  <span className="text-gray-400 block">Thời gian:</span>
                  <span className="font-medium text-gray-700">{selectedSuggestion.submittedAt}</span>
                </div>
              </div>

              <div>
                <label className="font-medium text-gray-700 block mb-1">Văn bản gốc:</label>
                <div className="p-3 bg-gray-50 rounded-lg border border-gray-200 text-gray-800 leading-relaxed font-sans">
                  {selectedSuggestion.sourceText}
                </div>
              </div>

              {renderHighlightedDiff(
                selectedSuggestion.currentAiTranslation,
                selectedSuggestion.suggestedTranslation
              )}

              <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                <span className="font-medium text-gray-700 block mb-1">Góp ý từ người dùng:</span>
                <p className="text-gray-700 italic">&ldquo;{selectedSuggestion.reason}&rdquo;</p>
              </div>
            </div>

            <div className="pt-2 flex justify-end">
              <button
                onClick={() => setSelectedSuggestion(null)}
                className="px-4 py-2 bg-gray-900 hover:bg-gray-800 text-white text-xs font-medium rounded-md"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
