import React, { useState } from 'react';
import {
  ChevronRight,
  X
} from 'lucide-react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import {
  MetricCardData,
  LanguagePairStat,
  ModelUsageStat,
  TimeSeriesDataPoint,
  RequestLog,
  TimeRangeFilter,
} from '../../types';

interface AnalyticsViewProps {
  metrics: MetricCardData[];
  languagePairs: LanguagePairStat[];
  aiModels: ModelUsageStat[];
  timeSeriesData: TimeSeriesDataPoint[];
  requestLogs: RequestLog[];
  timeRange: TimeRangeFilter;
  onOpenLiveChat: () => void;
}

const getFriendlyModelName = (snapshotName: string) => {
  const baseName = snapshotName.replace(/-\d{4}-\d{2}-\d{2}$/, '');

  if (/^gpt-/i.test(baseName)) {
    const [family, ...parts] = baseName.split('-');
    return `${family.toUpperCase()}-${parts.join(' ')}`;
  }

  return baseName.replace(/-/g, ' ');
};

export const AnalyticsView: React.FC<AnalyticsViewProps> = ({
  metrics,
  languagePairs,
  aiModels,
  timeSeriesData,
  requestLogs,
  timeRange,
  onOpenLiveChat,
}) => {
  const [selectedLog, setSelectedLog] = useState<RequestLog | null>(null);
  const [activeChartTab, setActiveChartTab] = useState<'volume' | 'latency' | 'tokens'>('volume');
  const activeSegmentStyle: React.CSSProperties = {
    backgroundColor: '#2563EB',
    color: '#FFFFFF',
    boxShadow: '0 1px 3px rgba(37, 99, 235, 0.28)',
  };
  const metricNumber = (id: string) => {
    const value = metrics.find((metric) => metric.id === id)?.value;
    return typeof value === 'number' ? value : Number.parseFloat(String(value ?? 0).replace(/,/g, '')) || 0;
  };
  const p50Latency = metricNumber('p50_latency');
  const p95Latency = metricNumber('p95_latency');

  return (
    <div className="space-y-6">
      {/* 4 to 6 Key KPI Cards Grid - Clean Utility Style */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 sm:gap-6">
        {metrics.filter((metric) => !['p50_latency', 'p95_latency'].includes(metric.id)).map((m) => {
          return (
            <div
              key={m.id}
              id={`metric-card-${m.id}`}
              className="bg-white p-5 rounded-xl border border-gray-200 shadow-xs flex flex-col justify-between"
            >
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1 truncate" title={m.title}>
                  {m.title}
                </p>
                <div className="flex items-baseline gap-1">
                  <span className="text-2xl font-bold text-gray-900 tracking-tight">
                    {typeof m.value === 'number' ? m.value.toLocaleString() : m.value}
                  </span>
                  {m.unit && <span className="text-xs text-gray-500 font-medium">{m.unit}</span>}
                </div>
              </div>

              {m.change && <div className="mt-3 text-xs font-medium text-gray-500">{m.change}</div>}
            </div>
          );
        })}
      </div>

      {/* Main Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Time-series Chart (2 columns on large screens) */}
        <div className="lg:col-span-2 bg-white p-6 rounded-xl border border-gray-200 shadow-xs flex flex-col justify-between">
          <div className="mb-6 flex flex-col justify-between gap-3 xl:flex-row xl:items-center">
            <div>
              <h3 className="font-semibold text-gray-900 text-base">
                Lượt dịch theo thời gian
              </h3>
              <p className="text-xs text-gray-400 mt-0.5">
                {timeRange === '24h'
                  ? '24 giờ qua'
                  : timeRange === '30d'
                    ? '30 ngày qua'
                    : timeRange === 'all'
                      ? 'Toàn bộ thời gian'
                      : 'Bảy ngày qua'}
              </p>
            </div>

            {/* Metric Mode Switcher */}
            <div className="inline-flex self-start shrink-0 gap-0.5 rounded-md border border-gray-200 bg-gray-100 p-0.5 text-xs font-medium">
              <button
                id="chart-tab-volume"
                onClick={() => setActiveChartTab('volume')}
                aria-pressed={activeChartTab === 'volume'}
                style={activeChartTab === 'volume' ? activeSegmentStyle : undefined}
                className={`px-3 py-1 rounded transition-colors ${
                  activeChartTab === 'volume'
                    ? 'bg-blue-600 text-white shadow-sm font-semibold'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Lượt dịch
              </button>
              <button
                id="chart-tab-latency"
                onClick={() => setActiveChartTab('latency')}
                aria-pressed={activeChartTab === 'latency'}
                style={activeChartTab === 'latency' ? activeSegmentStyle : undefined}
                className={`px-3 py-1 rounded transition-colors ${
                  activeChartTab === 'latency'
                    ? 'bg-blue-600 text-white shadow-sm font-semibold'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Độ trễ P50/P95
              </button>
              <button
                id="chart-tab-tokens"
                onClick={() => setActiveChartTab('tokens')}
                aria-pressed={activeChartTab === 'tokens'}
                style={activeChartTab === 'tokens' ? activeSegmentStyle : undefined}
                className={`px-3 py-1 rounded transition-colors ${
                  activeChartTab === 'tokens'
                    ? 'bg-blue-600 text-white shadow-sm font-semibold'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Tokens
              </button>
            </div>
          </div>

          {/* Chart Canvas */}
          <div className="h-64 w-full">
            {timeSeriesData.length > 0 ? <ResponsiveContainer width="100%" height="100%">
              {activeChartTab === 'volume' ? (
                <AreaChart data={timeSeriesData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorTranslations" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#2563EB" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#2563EB" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="colorFallback" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#F59E0B" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#F59E0B" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F3F4F6" />
                  <XAxis dataKey="time" stroke="#9CA3AF" fontSize={11} tickLine={false} />
                  <YAxis stroke="#9CA3AF" fontSize={11} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#FFFFFF',
                      borderRadius: '8px',
                      border: '1px solid #E5E7EB',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                      fontSize: '12px',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                  <Area
                    type="monotone"
                    dataKey="translations"
                    name="Lượt dịch thành công"
                    stroke="#2563EB"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorTranslations)"
                  />
                  <Area
                    type="monotone"
                    dataKey="fallbackCount"
                    name="Yêu cầu Fallback"
                    stroke="#F59E0B"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#colorFallback)"
                  />
                </AreaChart>
              ) : activeChartTab === 'latency' ? (
                <AreaChart data={timeSeriesData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorP50" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#3B82F6" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F3F4F6" />
                  <XAxis dataKey="time" stroke="#9CA3AF" fontSize={11} tickLine={false} />
                  <YAxis stroke="#9CA3AF" fontSize={11} unit="ms" tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#FFFFFF',
                      borderRadius: '8px',
                      border: '1px solid #E5E7EB',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                      fontSize: '12px',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                  <Area
                    type="monotone"
                    dataKey="p50Latency"
                    name="Độ trễ P50 (ms)"
                    stroke="#2563EB"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorP50)"
                  />
                  <Area
                    type="monotone"
                    dataKey="p95Latency"
                    name="Độ trễ P95 (ms)"
                    stroke="#F59E0B"
                    strokeWidth={1.5}
                    fill="transparent"
                  />
                </AreaChart>
              ) : (
                <BarChart data={timeSeriesData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F3F4F6" />
                  <XAxis dataKey="time" stroke="#9CA3AF" fontSize={11} tickLine={false} />
                  <YAxis stroke="#9CA3AF" fontSize={11} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#FFFFFF',
                      borderRadius: '8px',
                      border: '1px solid #E5E7EB',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                      fontSize: '12px',
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                  <Bar dataKey="inputTokens" name="Input Tokens" fill="#3B82F6" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="outputTokens" name="Output Tokens" fill="#8B5CF6" radius={[2, 2, 0, 0]} />
                </BarChart>
              )}
            </ResponsiveContainer> : (
              <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-gray-200 text-center text-sm text-gray-500">
                Chưa có lượt dịch trong khoảng thời gian này.
              </div>
            )}
          </div>
        </div>

        {/* Latency & AI Model Breakdown - Matching Clean Utility Theme */}
        <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-xs flex flex-col justify-between">
          <div>
            <h3 className="font-semibold text-gray-900 mb-6">Độ trễ trung bình</h3>

            {/* P50 & P95 progress bars */}
            <div className="space-y-6">
              <div>
                <div className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2.5 text-sm">
                  <span className="text-gray-600">Độ trễ P50</span>
                  <span className="font-semibold text-gray-900">{p50Latency.toLocaleString()} ms</span>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2.5 text-sm">
                  <span className="text-gray-600">Độ trễ P95</span>
                  <span className="font-semibold text-gray-900">{p95Latency.toLocaleString()} ms</span>
                </div>
              </div>

              {/* AI Models Breakdown list */}
              <div className="pt-4 border-t border-gray-100">
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                  AI Models
                </h4>
                <div className="space-y-2 text-sm">
                  {aiModels.map((model) => (
                    <div key={model.id} className="flex items-center justify-between">
                      <span
                        className="flex min-w-0 items-center gap-2 text-gray-600"
                        title={model.name}
                        aria-label={`${getFriendlyModelName(model.name)} (${model.name})`}
                      >
                        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: model.color }} />
                        <span className="truncate">{getFriendlyModelName(model.name)}</span>
                      </span>
                      <span className="font-medium text-gray-900">{model.servedCount.toLocaleString()} lần ({model.percentage.toFixed(1)}%)</span>
                    </div>
                  ))}
                  {aiModels.length === 0 && <p className="text-xs text-gray-500">Chưa có dữ liệu model.</p>}
                </div>
              </div>
            </div>
          </div>

          <div className="pt-4 mt-4 border-t border-gray-100">
            <button
              onClick={onOpenLiveChat}
              className="w-full flex items-center justify-center gap-1.5 py-2 px-3 border border-gray-300 rounded-md text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              <span>Thử nghiệm trong Chat</span>
              <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
            </button>
          </div>
        </div>
      </div>

      {/* Language Pairs Statistics Table - Clean Utility Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-xs overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
          <h3 className="font-semibold text-gray-900">Thống kê cặp ngôn ngữ</h3>
          <span className="text-xs text-gray-400 font-medium">{languagePairs.length} cặp đang hoạt động</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-gray-50 text-[11px] text-gray-400 uppercase font-bold tracking-wider border-b border-gray-100">
              <tr>
                <th className="px-6 py-3">Cặp ngôn ngữ</th>
                <th className="px-6 py-3 text-right">Số lần</th>
                <th className="px-6 py-3 text-right">P50</th>
                <th className="px-6 py-3 text-right">P95</th>
                <th className="px-6 py-3 text-right">TB Tokens / yêu cầu</th>
                <th className="px-6 py-3 text-center">Trạng thái</th>
              </tr>
            </thead>
            <tbody className="text-sm divide-y divide-gray-100">
              {languagePairs.map((pair) => (
                <tr key={pair.pair} className="hover:bg-gray-50/50 transition-colors">
                  <td className="px-6 py-3 font-medium text-gray-900">{pair.pair}</td>
                  <td className="px-6 py-3 text-right text-gray-600 font-medium">{pair.count}</td>
                  <td className="px-6 py-3 text-right text-gray-600 font-mono text-xs">
                    {pair.p50.toLocaleString()} ms
                  </td>
                  <td className="px-6 py-3 text-right text-gray-600 font-mono text-xs">
                    {pair.p95.toLocaleString()} ms
                  </td>
                  <td className="px-6 py-3 text-right text-gray-500 font-mono text-xs">
                    {pair.avgInputTokens} in / {pair.avgOutputTokens} out
                  </td>
                  <td className="px-6 py-3 text-center">
                    <span className="inline-block w-2 h-2 rounded-full bg-green-500" title="Hoạt động tốt"></span>
                  </td>
                </tr>
              ))}
              {languagePairs.length === 0 && <tr><td colSpan={6} className="px-6 py-8 text-center text-sm text-gray-500">Chưa có dữ liệu cặp ngôn ngữ.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* Live Request Logs Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-xs overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
          <div>
            <h3 className="font-semibold text-gray-900">Nhật ký yêu cầu gần đây</h3>
            <p className="text-xs text-gray-400 mt-0.5">Thời gian thực</p>
          </div>
          <span className="text-xs text-gray-500 font-mono">{requestLogs.length} bản ghi</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-gray-50 text-[11px] text-gray-400 uppercase font-bold tracking-wider border-b border-gray-100">
              <tr>
                <th className="px-6 py-3">Thời gian</th>
                <th className="px-6 py-3">Cặp</th>
                <th className="px-6 py-3">Model</th>
                <th className="px-6 py-3">Nội dung</th>
                <th className="px-6 py-3 text-right">Độ trễ</th>
                <th className="px-6 py-3 text-right">Tokens</th>
                <th className="px-6 py-3 text-center">Chi tiết</th>
              </tr>
            </thead>
            <tbody className="text-sm divide-y divide-gray-100 text-gray-600">
              {requestLogs.map((log) => (
                <tr key={log.id} className="hover:bg-gray-50/60 transition-colors">
                  <td className="px-6 py-3 font-mono text-xs text-gray-400 whitespace-nowrap">
                    {log.timestamp}
                  </td>
                  <td className="px-6 py-3 font-medium text-gray-900 whitespace-nowrap">
                    {log.pair}
                  </td>
                  <td className="px-6 py-3 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                        log.isFallback
                          ? 'bg-amber-50 text-amber-700 border border-amber-200'
                          : 'bg-gray-100 text-gray-700'
                      }`}
                    >
                      {log.model}
                    </span>
                  </td>
                  <td className="px-6 py-3 max-w-xs truncate text-gray-800" title={log.sourcePreview}>
                    <p className="truncate text-xs">{log.sourcePreview}</p>
                    {log.matchedGlossaryTerms && log.matchedGlossaryTerms.length > 0 && (
                      <div className="flex items-center gap-1 mt-0.5">
                        <span className="text-[10px] text-gray-400">Thuật ngữ:</span>
                        {log.matchedGlossaryTerms.map((t) => (
                          <span
                            key={t}
                            className="text-[10px] bg-blue-50 text-blue-700 px-1 py-0.2 rounded border border-blue-200"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-xs whitespace-nowrap">
                    {log.latency.toLocaleString()} ms
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-xs text-gray-500 whitespace-nowrap">
                    {log.inputTokens} / {log.outputTokens}
                  </td>
                  <td className="px-6 py-3 text-center whitespace-nowrap">
                    <button
                      id={`btn-view-log-${log.id}`}
                      onClick={() => setSelectedLog(log)}
                      className="px-2.5 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors"
                    >
                      Xem
                    </button>
                  </td>
                </tr>
              ))}
              {requestLogs.length === 0 && <tr><td colSpan={7} className="px-6 py-8 text-center text-sm text-gray-500">Chưa ghi nhận yêu cầu dịch trong khoảng thời gian này.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* Log Detail Modal */}
      {selectedLog && (
        <div
          id="log-detail-modal"
          className="fixed inset-0 bg-gray-900/40 backdrop-blur-xs z-50 flex items-center justify-center p-4"
          onClick={() => setSelectedLog(null)}
        >
          <div
            className="bg-white rounded-xl max-w-lg w-full p-6 shadow-lg border border-gray-200 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-3 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-gray-900 text-base">Chi tiết Yêu cầu</span>
                <span className="font-mono text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded border border-gray-200">
                  {selectedLog.id}
                </span>
              </div>
              <button
                onClick={() => setSelectedLog(null)}
                className="text-gray-400 hover:text-gray-700 p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className="text-gray-400 block mb-0.5">Thời gian:</span>
                <span className="font-medium text-gray-900">{selectedLog.timestamp}</span>
              </div>
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className="text-gray-400 block mb-0.5">Cặp ngôn ngữ:</span>
                <span className="font-semibold text-gray-900">{selectedLog.pair}</span>
              </div>
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className="text-gray-400 block mb-0.5">AI Model:</span>
                <span className="font-medium text-gray-900">{selectedLog.model}</span>
              </div>
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className="text-gray-400 block mb-0.5">Độ trễ & Tokens:</span>
                <span className="font-mono text-gray-900">
                  {selectedLog.latency} ms | {selectedLog.inputTokens} in / {selectedLog.outputTokens} out
                </span>
              </div>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <label className="font-semibold text-gray-700 block mb-1">Văn bản gốc:</label>
                <div className="p-3 bg-gray-50 rounded-lg border border-gray-200 text-gray-900 leading-relaxed">
                  {selectedLog.sourcePreview}
                </div>
              </div>
              <div>
                <label className="font-semibold text-gray-700 block mb-1">Bản dịch AI:</label>
                <div className="p-3 bg-blue-50/50 rounded-lg border border-blue-100 text-gray-900 leading-relaxed">
                  {selectedLog.targetPreview}
                </div>
              </div>
            </div>

            {selectedLog.matchedGlossaryTerms && selectedLog.matchedGlossaryTerms.length > 0 && (
              <div className="p-3 bg-gray-50 rounded-lg border border-gray-200 text-xs">
                <span className="font-semibold text-gray-700 block mb-1.5">
                  Thuật ngữ chuyên ngành áp dụng:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {selectedLog.matchedGlossaryTerms.map((t) => (
                    <span
                      key={t}
                      className="px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200 font-medium"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="pt-2 flex justify-end">
              <button
                onClick={() => setSelectedLog(null)}
                className="px-4 py-2 border border-gray-300 rounded-md text-xs font-medium text-gray-700 hover:bg-gray-50"
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
