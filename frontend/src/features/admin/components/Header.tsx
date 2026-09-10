import React from 'react';
import {
  CalendarRange,
  ChevronDown,
  RotateCw,
} from 'lucide-react';
import { AdminInterfaceLanguage, AdminTab, TimeRangeFilter } from '../types';

interface HeaderProps {
  currentTab: AdminTab;
  interfaceLanguage: AdminInterfaceLanguage;
  timeRange: TimeRangeFilter;
  onTimeRangeChange: (range: TimeRangeFilter) => void;
  onRefresh: () => void;
  isRefreshing?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  currentTab,
  interfaceLanguage,
  timeRange,
  onTimeRangeChange,
  onRefresh,
  isRefreshing = false,
}) => {
  const getTabTitle = () => {
    const vi = interfaceLanguage === 'vi';
    switch (currentTab) {
      case 'analytics':
        return vi ? 'Trung tâm quản trị' : 'Admin center';
      case 'feedback':
        return vi ? 'Phản hồi bản dịch' : 'Translation feedback';
      case 'glossary':
        return vi ? 'Quản lý thuật ngữ' : 'Glossary management';
      case 'suggestions':
        return vi ? 'Kiểm duyệt đề xuất' : 'Suggestion review';
      case 'chat':
        return vi ? 'Kiểm tra dịch thuật' : 'Translation testing';
      default:
        return vi ? 'Trung tâm quản trị' : 'Admin center';
    }
  };

  return (
    <header
      id="admin-header"
      className="h-16 bg-white border-b border-gray-200 px-4 sm:px-8 flex items-center justify-between sticky top-0 z-30"
    >
      {/* Left: Page title */}
      <div className="flex items-center">
        <h1 className="text-lg sm:text-xl font-semibold text-gray-900 tracking-tight">
          {getTabTitle()}
        </h1>
      </div>

      {/* Right: Actions, Filters, Profile */}
      <div className="flex items-center gap-3">
        {/* Time Range Filter (In Analytics) */}
        {currentTab === 'analytics' && (
          <label className="relative hidden items-center sm:flex" htmlFor="filter-time-range">
            <CalendarRange className="pointer-events-none absolute left-3 h-4 w-4 text-blue-600" />
            <span className="sr-only">{interfaceLanguage === 'vi' ? 'Chọn khoảng thời gian' : 'Select time range'}</span>
            <select
              id="filter-time-range"
              value={timeRange}
              onChange={(event) => onTimeRangeChange(event.target.value as TimeRangeFilter)}
              className="h-9 min-w-36 appearance-none rounded-lg border border-gray-200 bg-white py-1.5 pl-9 pr-9 text-sm font-semibold text-gray-700 outline-none transition-colors hover:border-blue-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              aria-label={interfaceLanguage === 'vi' ? 'Khoảng thời gian thống kê' : 'Analytics time range'}
            >
              <option value="24h">{interfaceLanguage === 'vi' ? '24 giờ qua' : 'Last 24 hours'}</option>
              <option value="7d">{interfaceLanguage === 'vi' ? '7 ngày qua' : 'Last 7 days'}</option>
              <option value="30d">{interfaceLanguage === 'vi' ? '30 ngày qua' : 'Last 30 days'}</option>
              <option value="all">{interfaceLanguage === 'vi' ? 'Tất cả' : 'All time'}</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 h-4 w-4 text-gray-500" />
          </label>
        )}

        {/* Refresh Button */}
        <button
          id="btn-refresh-data"
          onClick={onRefresh}
          disabled={isRefreshing}
          aria-label={interfaceLanguage === 'vi' ? 'Lấy dữ liệu mới' : 'Fetch latest data'}
          title={interfaceLanguage === 'vi' ? 'Lấy dữ liệu mới từ máy chủ' : 'Fetch latest data from server'}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-700 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 disabled:cursor-wait disabled:opacity-70"
        >
          <RotateCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-blue-600' : ''}`} />
        </button>

      </div>
    </header>
  );
};
