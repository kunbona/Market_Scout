import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { FilterTabs } from '../components/FilterTabs';
import { apiFetch } from '../lib/api';

interface QmtOverview {
  status: {
    enabled: boolean;
    connected: boolean;
    version: string | null;
  };
  snapshot: {
    updated_at: string | null;
    source: string | null;
    market: string | null;
  };
  kpis: {
    limit_down_count: number;
    up_count: number | null;
    down_count: number | null;
    flat_count: number | null;
    turnover: number | null;
  };
}

interface QmtLimitDownMonitor {
  status: {
    enabled: boolean;
    connected: boolean;
    version: string | null;
  };
  snapshot: {
    updated_at: string | null;
    source: string | null;
    market: string | null;
  };
  summary: {
    total_count: number;
    st_count: number;
    non_st_count: number;
    industry_count: number;
  };
  industry_distribution: Array<{
    sector: string;
    count: number;
  }>;
  items: Array<{
    stock_code: string;
    stock_name: string;
    sector: string;
    trade_date: string;
    last_price: number | null;
    last_close: number | null;
    down_limit: number | null;
    limit_gap_pct: number | null;
    is_st: boolean;
    tags: string[];
  }>;
}

interface QmtIndustryDraggers {
  status: {
    enabled: boolean;
    connected: boolean;
    version: string | null;
  };
  snapshot: {
    updated_at: string | null;
    source: string | null;
    market: string | null;
  };
  summary: {
    sector_count: number;
    top_dragger_sector: string | null;
    top_dragger_limit_down_count: number;
  };
  items: Array<{
    sector: string;
    limit_down_count: number;
    st_count: number;
    non_st_count: number;
    lead_stock_code: string;
    lead_stock_name: string;
    drag_score: number;
  }>;
}

interface QmtIndustryStats {
  status: {
    enabled: boolean;
    connected: boolean;
    version: string | null;
  };
  snapshot: {
    updated_at: string | null;
    source: string | null;
    market: string | null;
  };
  summary: {
    sector_count: number;
    strong_ma10_sector_count: number;
    strong_ma10_sector_count_yesterday: number;
    top_meat_sector: string | null;
    top_main_inflow_sector: string | null;
    data_date: string | null;
    data_lag_days: number | null;
    realtime_data_date: string | null;
  };
  items: Array<{
    sector: string;
    stock_count: number;
    above_ma10_count: number;
    above_ma10_count_csv: number;
    above_ma10_ratio_realtime: number;
    above_ma10_ratio_csv: number;
    above_ma10_ratio_delta: number;
    above_ma10_ratio: number;  // 兼容旧字段，等价于 realtime
    ma10_realtime: number;  // 组内个股 ma10_realtime 算术平均（参考值）
    ma10_csv: number;       // 组内个股 ma10_csv 算术平均（参考值）
    meat_count: number;
    big_loss_count: number;
    limit_up_count: number;
    limit_down_count: number;
    yesterday_main_inflow: number;
    today_main_inflow: number;
    data_date: string;
  }>;
}

// staleness 视觉编码：0=绿(今日)、1=绿(正常 T+1)、2=黄、3+=灰
const stalenessColor = (lag: number | null | undefined): string => {
  if (lag == null) return 'bg-gray-50 border-gray-200 text-gray-500';
  if (lag <= 1) return 'bg-emerald-50 border-emerald-200 text-emerald-700';
  if (lag === 2) return 'bg-amber-50 border-amber-200 text-amber-700';
  return 'bg-gray-100 border-gray-300 text-gray-600';
};

const stalenessLabel = (dataDate: string | null, lag: number | null): string => {
  if (!dataDate || lag == null) return '无数据';
  if (lag === 0) return `今日数据 ${dataDate}`;
  if (lag === 1) return `T+1 数据 ${dataDate}`;
  return `滞后 ${lag} 天 · 数据 ${dataDate}`;
};

const fmtCount = (value: number | null | undefined): string => (
  value == null ? '--' : `${value}`
);

const fmtAmountYi = (value: number | null | undefined): string => (
  value == null ? '--' : `${(value / 1e8).toFixed(2)}亿`
);

const fmtPrice = (value: number | null | undefined): string => (
  value == null ? '--' : value.toFixed(2)
);

const fmtPercent = (value: number | null | undefined): string => (
  value == null ? '--' : `${(value * 100).toFixed(1)}%`
);

// 10 日线上占比颜色：>70% 强势（红）/ 30-70% 中性（灰）/ <30% 弱势（绿）
// A 股惯例: 红涨绿跌 — 强势=涨=红, 弱势=跌=绿
const ratioColor = (ratio: number | null | undefined): string => {
  if (ratio == null) return 'text-gray-400';
  if (ratio > 0.7) return 'text-red-600';
  if (ratio < 0.3) return 'text-green-600';
  return 'text-gray-700';
};

// 差值颜色：正红(今天比昨天强) / 负绿(今天比昨天弱) / 0 灰
const deltaColor = (delta: number | null | undefined): string => {
  if (delta == null) return 'text-gray-400';
  if (delta > 0.05) return 'text-red-600';
  if (delta < -0.05) return 'text-green-600';
  return 'text-gray-500';
};

const fmtDelta = (delta: number | null | undefined): string => {
  if (delta == null) return '--';
  const v = delta * 100;
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(1)}pp`;
};

export function QmtDataPage() {
  const [data, setData] = useState<QmtOverview | null>(null);
  const [monitorData, setMonitorData] = useState<QmtLimitDownMonitor | null>(null);
  const [draggerData, setDraggerData] = useState<QmtIndustryDraggers | null>(null);
  const [industryStatsData, setIndustryStatsData] = useState<QmtIndustryStats | null>(null);
  const [industryStatsLoading, setIndustryStatsLoading] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [keyword, setKeyword] = useState<string>('');
  const [stOnly, setStOnly] = useState<boolean>(false);
  const [selectedSector, setSelectedSector] = useState<string>('all');
  const [statsSortField, setStatsSortField] = useState<string>('today_main_inflow');
  const [statsSortDir, setStatsSortDir] = useState<'asc' | 'desc'>('desc');
  // 视图 Tab：3 个分区，避免一屏堆太多相似模块
  const [activeView, setActiveView] = useState<'limit_down' | 'draggers' | 'industry'>('limit_down');

  const toggleStatsSort = (field: string) => {
    setStatsSortField((prev) => {
      if (prev === field) {
        setStatsSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setStatsSortDir(field === 'sector' ? 'asc' : 'desc');
      }
      return field;
    });
  };

  const sortedIndustryStats = useMemo(() => {
    if (!industryStatsData?.items) return [];
    const items = [...industryStatsData.items];
    const dir = statsSortDir === 'asc' ? 1 : -1;
    items.sort((a, b) => {
      let va: number;
      let vb: number;
      if (statsSortField === 'sector') {
        return dir * a.sector.localeCompare(b.sector, 'zh');
      }
      va = Number(a[statsSortField as keyof typeof a]) ?? 0;
      vb = Number(b[statsSortField as keyof typeof b]) ?? 0;
      return dir * (va - vb);
    });
    return items;
  }, [industryStatsData?.items, statsSortField, statsSortDir]);

  const fetchCoreData = async (): Promise<[
    QmtOverview,
    QmtLimitDownMonitor,
    QmtIndustryDraggers
  ]> => (
    Promise.all([
      apiFetch<QmtOverview>('/api/qmt-overview'),
      apiFetch<QmtLimitDownMonitor>('/api/qmt-limit-down-monitor'),
      apiFetch<QmtIndustryDraggers>('/api/qmt-industry-draggers'),
    ])
  );

  const fetchIndustryStats = async (): Promise<QmtIndustryStats> => (
    apiFetch<QmtIndustryStats>('/api/qmt-industry-stats')
  );

  useEffect(() => {
    let cancelled = false;
    const loadInitial = async (): Promise<void> => {
      try {
        const next = await fetchCoreData();
        if (!cancelled) {
          setData(next[0]);
          setMonitorData(next[1]);
          setDraggerData(next[2]);
          setError('');
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载失败');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }

      try {
        const industryStats = await fetchIndustryStats();
        if (!cancelled) {
          setIndustryStatsData(industryStats);
        }
      } catch (err) {
        if (!cancelled) {
          setError((prev) => prev || (err instanceof Error ? err.message : '行业统计加载失败'));
        }
      } finally {
        if (!cancelled) {
          setIndustryStatsLoading(false);
        }
      }
    };

    void loadInitial();
    const timerId = window.setInterval(() => {
      void (async () => {
        try {
          const next = await fetchCoreData();
          if (!cancelled) {
            setData(next[0]);
            setMonitorData(next[1]);
            setDraggerData(next[2]);
            setError('');
          }
        } catch (err) {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : '加载失败');
          }
        }

        try {
          const industryStats = await fetchIndustryStats();
          if (!cancelled) {
            setIndustryStatsData(industryStats);
          }
        } catch (err) {
          if (!cancelled) {
            setError((prev) => prev || (err instanceof Error ? err.message : '行业统计加载失败'));
          }
        } finally {
          if (!cancelled) {
            setIndustryStatsLoading(false);
          }
        }
      })();
    }, 60_000);

    return () => {
      cancelled = true;
      window.clearInterval(timerId);
    };
  }, []);

  const handleRefresh = async (): Promise<void> => {
    setRefreshing(true);
    setError('');
    try {
      const next = await fetchCoreData();
      setData(next[0]);
      setMonitorData(next[1]);
      setDraggerData(next[2]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }

    setIndustryStatsLoading(true);
    try {
      const industryStats = await fetchIndustryStats();
      setIndustryStatsData(industryStats);
    } catch (err) {
      setError((prev) => prev || (err instanceof Error ? err.message : '行业统计加载失败'));
    } finally {
      setIndustryStatsLoading(false);
      setRefreshing(false);
    }
  };

  const statusTone = useMemo((): string => {
    if (!data?.status.enabled) {
      return 'bg-gray-50 text-gray-500 border-gray-200';
    }
    if (!data.status.connected) {
      return 'bg-amber-50 text-amber-700 border-amber-200';
    }
    return 'bg-green-50 text-green-700 border-green-200';
  }, [data]);

  const sectorOptions = useMemo((): string[] => {
    const sectors = new Set(
      (monitorData?.items ?? [])
        .map((row) => row.sector)
        .filter((sector) => Boolean(sector)),
    );
    return ['all', ...Array.from(sectors).sort((a, b) => a.localeCompare(b, 'zh-CN'))];
  }, [monitorData]);
  const filteredMonitorRows = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return (monitorData?.items ?? []).filter((row) => {
      // 数据兜底: 表里可能有历史脏数据 (QMT tick 错位/停牌瞬间/退市股)
      // 跌停定义 = 现价 ≈ 跌停价, 用 ratio check 比 abs check 鲁棒
      // 兼容 A 股新旧规则:
      // - 主板普通股 10% 跌停: last_price / down_limit ≈ 1.0
      // - 主板 ST 旧规则 5% 跌停: down_limit=0.95*lc, last_price≈0.95*lc, ratio≈1.0
      //   但 fetch 用了新规 0.9 算法, 历史数据会落到 [0.94, 0.95] 区间
      // - 主板 ST 新规则 10% 跌停 (2026-07-06): down_limit=0.9*lc, last_price≈0.9*lc, ratio≈1.0
      // - 创业板/科创板 20% 跌停: ratio=1.0
      // - 北交所 30% 跌停: ratio=1.0
      // - 跌穿 (脏数据/退市股): ratio<0.9 → 挡
      // - 没跌停 (现价 > 跌停价): ratio>1.01 → 挡
      if (
        row.last_price != null &&
        row.down_limit != null &&
        row.down_limit > 0 &&
        (row.last_price / row.down_limit < 0.9 ||
          row.last_price / row.down_limit > 1.01)
      ) {
        return false;
      }
      if (stOnly && !row.is_st) {
        return false;
      }
      if (selectedSector !== 'all' && row.sector !== selectedSector) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }
      return (
        row.stock_code.toLowerCase().includes(normalizedKeyword) ||
        row.stock_name.toLowerCase().includes(normalizedKeyword)
      );
    });
  }, [keyword, monitorData, selectedSector, stOnly]);

  return (
    <>
      <TabHeader title="QMT数据" />
      <div className="space-y-6">
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-gray-900">miniQMT 运行状态</p>
              <p className="text-xs text-gray-400 mt-1">展示当前连接状态与最近一次可用快照。</p>
            </div>
            <button
              onClick={() => void handleRefresh()}
              disabled={refreshing}
              className="inline-flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>

          <div className="mt-4 flex flex-wrap gap-3">
            <span className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border ${statusTone}`}>
              <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" />
              {!data?.status.enabled ? 'QMT未启用' : data.status.connected ? 'QMT已连接' : 'QMT未连接'}
            </span>
            <span className="text-xs text-gray-500">xtquant 版本：{data?.status.version ?? '--'}</span>
            <span className="text-xs text-gray-500">最近快照：{data?.snapshot.updated_at ?? '--'}</span>
          </div>

          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
          {!loading && !error && !data?.status.enabled && (
            <p className="mt-3 text-xs text-gray-500">当前未启用 QMT，可前往“设置”页开启 miniQMT 数据源。</p>
          )}
          {!loading && !error && data?.status.enabled && !data.status.connected && (
            <p className="mt-3 text-xs text-amber-600">QMT 已启用但未连接，请确认 miniQMT 客户端已启动并登录。</p>
          )}
          {!loading && !error && data?.status.enabled && data.status.connected && !data.snapshot.updated_at && (
            <p className="mt-3 text-xs text-gray-500">QMT 已连接，但当前暂无最新市场宽度快照。</p>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">
          {[
            { label: 'QMT跌停数', value: fmtCount(data?.kpis.limit_down_count) },
            { label: '上涨家数', value: fmtCount(data?.kpis.up_count) },
            { label: '下跌家数', value: fmtCount(data?.kpis.down_count) },
            { label: '平盘家数', value: fmtCount(data?.kpis.flat_count) },
            { label: '全市场成交额', value: fmtAmountYi(data?.kpis.turnover) },
          ].map((item) => (
            <div key={item.label} className="bg-white rounded-xl border border-gray-100 p-4">
              <div className="text-xs text-gray-400">{item.label}</div>
              <div className="mt-2 text-2xl font-semibold text-gray-900">{loading ? '--' : item.value}</div>
            </div>
          ))}
        </div>

        {/* Tab 切换：跌停监控 / 行业拖累 / 行业统计，避免一屏堆 3 个相似模块 */}
        <FilterTabs
          tabs={[
            { id: 'limit_down', label: '跌停监控', count: monitorData?.summary.total_count ?? 0 },
            { id: 'draggers', label: '行业拖累', count: draggerData?.items.length ?? 0 },
            { id: 'industry', label: '行业统计', count: industryStatsData?.summary.sector_count ?? 0 },
          ]}
          activeTab={activeView}
          onTabChange={(id) => setActiveView(id as typeof activeView)}
        />

        {activeView === 'limit_down' && (
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-gray-900">跌停监控中心</p>
              <p className="text-xs text-gray-400 mt-1">从 `dt_pool_v3` 派生的当前跌停监控视图。</p>
            </div>
            <span className="text-xs text-gray-400">{monitorData?.summary.total_count ?? 0} 条</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {[
              { label: '跌停总数', value: `${monitorData?.summary.total_count ?? 0}` },
              { label: 'ST跌停数', value: `${monitorData?.summary.st_count ?? 0}` },
              { label: '非ST跌停数', value: `${monitorData?.summary.non_st_count ?? 0}` },
              { label: '涉及行业数', value: `${monitorData?.summary.industry_count ?? 0}` },
            ].map((item) => (
              <div key={item.label} className="rounded-xl border border-gray-100 p-4">
                <div className="text-xs text-gray-400">{item.label}</div>
                <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="按代码或名称搜索"
              className="px-3 py-2 text-sm border border-gray-200 rounded-lg"
            />
            <select
              value={selectedSector}
              onChange={(event) => setSelectedSector(event.target.value)}
              className="px-3 py-2 text-sm border border-gray-200 rounded-lg"
            >
              {sectorOptions.map((sector) => (
                <option key={sector} value={sector}>
                  {sector === 'all' ? '全部行业' : sector}
                </option>
              ))}
            </select>
            <label className="inline-flex items-center gap-2 px-3 py-2 text-sm border border-gray-200 rounded-lg">
              <input
                type="checkbox"
                checked={stOnly}
                onChange={(event) => setStOnly(event.target.checked)}
              />
              仅看 ST
            </label>
          </div>

          <div className="grid grid-cols-8 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
            <span>日期</span>
            <span>代码</span>
            <span>名称</span>
            <span className="text-center">行业</span>
            <span className="text-center">最新价</span>
            <span className="text-center">昨收</span>
            <span className="text-center">跌停价</span>
            <span className="text-center">标签</span>
          </div>
          {filteredMonitorRows.length === 0 ? (
            <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
              暂无符合条件的跌停监控数据
            </div>
          ) : (
            <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
              {filteredMonitorRows.map((row) => (
                <div key={row.stock_code} className="grid grid-cols-8 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
                  <span className="text-gray-500">{row.trade_date || '--'}</span>
                  <span>{row.stock_code}</span>
                  <span>{row.stock_name || '--'}</span>
                  <span className="text-center">{row.sector || '--'}</span>
                  <span className="text-center">{fmtPrice(row.last_price)}</span>
                  <span className="text-center">{fmtPrice(row.last_close)}</span>
                  <span className="text-center text-green-600 font-medium">{fmtPrice(row.down_limit)}</span>
                  <span className="text-center">{row.tags.join(' / ')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        )}

        {activeView === 'draggers' && (
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-gray-900">行业拖累榜</p>
              <p className="text-xs text-gray-400 mt-1">按当前跌停集合统计的行业拖累强度。</p>
            </div>
            <span className="text-xs text-gray-400">{draggerData?.summary.sector_count ?? 0} 个行业</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              { label: '拖累行业数', value: `${draggerData?.summary.sector_count ?? 0}` },
              { label: '最弱行业', value: draggerData?.summary.top_dragger_sector ?? '--' },
              { label: '最大行业跌停数', value: `${draggerData?.summary.top_dragger_limit_down_count ?? 0}` },
            ].map((item) => (
              <div key={item.label} className="rounded-xl border border-gray-100 p-4">
                <div className="text-xs text-gray-400">{item.label}</div>
                <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-6 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
            <span>行业</span>
            <span className="text-center">跌停数</span>
            <span className="text-center">ST数</span>
            <span className="text-center">非ST数</span>
            <span className="text-center">代表个股</span>
            <span className="text-center">拖累分</span>
          </div>
          {(draggerData?.items.length ?? 0) === 0 ? (
            <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
              暂无行业拖累数据
            </div>
          ) : (
            <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
              {draggerData!.items.map((row) => (
                <div key={row.sector || row.lead_stock_code} className="grid grid-cols-6 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
                  <span>{row.sector || '--'}</span>
                  <span className="text-center">{row.limit_down_count}</span>
                  <span className="text-center">{row.st_count}</span>
                  <span className="text-center">{row.non_st_count}</span>
                  <span className="text-center">{row.lead_stock_name || row.lead_stock_code || '--'}</span>
                  <span className="text-center">{row.drag_score.toFixed(1)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        )}

        {activeView === 'industry' && (
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-gray-900">行业统计榜</p>
              <p className="text-xs text-gray-400 mt-1">按全市场行业横截面统计 QMT 实时强弱与昨日主力净流入。</p>
            </div>
            <span className="text-xs text-gray-400">{industryStatsData?.summary.sector_count ?? 0} 个行业</span>
          </div>

          {/* staleness 提示：CSV 是 T+1 截面，MA10 实时 / CSV 两列同时展示便于对比 */}
          <div
            className={`rounded-lg border px-3 py-2 text-xs flex items-center justify-between gap-3 ${stalenessColor(
              industryStatsData?.summary.data_lag_days,
            )}`}
            title="实时 MA10 = 今天 tick 价顶替 CSV 末日，参与 10 日均线；MA10·T+1 = 纯 CSV 10 日均线。强势/主力来自 CSV 截面（T+1/T+2）。涨跌/大肉来自 QMT tick，是今日实时。"
          >
            <span className="font-medium">
              📊 {stalenessLabel(industryStatsData?.summary.data_date ?? null, industryStatsData?.summary.data_lag_days ?? null)}
              <span className="ml-2 text-emerald-700 font-normal">
                · 实时 {industryStatsData?.summary.realtime_data_date ?? '今天'}
              </span>
            </span>
            <span className="opacity-80">
              MA10 实时 vs T+1 对比显示 · 主力/强势为 T+1 截面
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {(() => {
              const realCnt = industryStatsData?.summary.strong_ma10_sector_count ?? 0;
              const yestCnt = industryStatsData?.summary.strong_ma10_sector_count_yesterday ?? 0;
              const delta = realCnt - yestCnt;
              const deltaSign = delta > 0 ? '+' : '';
              const deltaStr = delta === 0 ? '±0' : `${deltaSign}${delta}`;
              const deltaCls = delta > 0
                ? 'text-red-600'
                : delta < 0
                  ? 'text-green-600'
                  : 'text-gray-400';
              return (
                <div className="rounded-xl border border-gray-100 p-4" title="实时占比 >50% 的行业数，对比昨日。差值显示今日盘口强势行业的增减。">
                  <div className="text-xs text-gray-400">MA10 强势行业</div>
                  <div className="mt-2 text-2xl font-semibold text-gray-900">
                    {realCnt}
                    <span className="text-sm font-normal text-gray-400"> / 昨 {yestCnt}</span>
                  </div>
                  <div className={`text-xs mt-1 ${deltaCls}`}>
                    今日 {deltaStr} 个
                  </div>
                </div>
              );
            })()}
            {[
              { label: '行业数', value: `${industryStatsData?.summary.sector_count ?? 0}` },
              { label: '最大大肉行业', value: industryStatsData?.summary.top_meat_sector ?? '--' },
              { label: '主力净流入第一行业', value: industryStatsData?.summary.top_main_inflow_sector ?? '--' },
            ].map((item) => (
              <div key={item.label} className="rounded-xl border border-gray-100 p-4">
                <div className="text-xs text-gray-400">{item.label}</div>
                <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-9 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
            {(() => {
              const csvDate = industryStatsData?.summary.data_date;
              const csvDateShort = csvDate ? csvDate.slice(5) : '昨';
              return [
                ['行业', 'sector'],
                ['个股数', 'stock_count'],
                [
                  'ma10 线上占比·实时',
                  'above_ma10_ratio_realtime',
                  '行业里 tick > ma10_realtime 的个股占比。盘中实时变，>50% 算强势。副文本是组内个股 ma10_realtime 算术平均。',
                ],
                [
                  `ma10 线上占比·${csvDateShort}`,
                  'above_ma10_ratio_csv',
                  `行业里 last_close > ma10_csv 的个股占比。基线数据来自本地 CSV 截面（data_date=${csvDate || '?'}，T+1 截面），稳定可复现。副文本是组内个股 ma10_csv 算术平均。`,
                ],
                [
                  '差值 (实时-昨)',
                  'above_ma10_ratio_delta',
                  '实时占比 − 昨日(本地CSV)占比。>0 今日盘口比昨天强，<0 弱，单位 pp（百分点）。',
                ],
                ['大肉数', 'meat_count'],
                ['涨停数', 'limit_up_count'],
                ['大面数', 'big_loss_count'],
                ['跌停数', 'limit_down_count'],
                [
                  `主力·T+1 (${csvDateShort})`,
                  'today_main_inflow',
                  `行业主力净流入，数据日期 ${csvDate || '?'}`,
                ],
              ];
            })().map(([label, field, hint]) => (
              <button
                key={field as string}
                onClick={() => toggleStatsSort(field as string)}
                title={hint as string | undefined}
                className={`text-center cursor-pointer hover:text-gray-700 select-none ${
                  statsSortField === field ? 'text-gray-700 font-semibold' : ''
                }`}
              >
                {label as string}{statsSortField === field ? (statsSortDir === 'asc' ? ' ↑' : ' ↓') : ''}
              </button>
            ))}
          </div>
          {industryStatsLoading ? (
            <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
              行业统计计算中...
            </div>
          ) : sortedIndustryStats.length === 0 ? (
            <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
              暂无行业统计数据
            </div>
          ) : (
            <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
              {sortedIndustryStats.map((row) => (
                <div key={row.sector} className="grid grid-cols-10 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
                  <span>{row.sector}</span>
                  <span className="text-center">{row.stock_count}</span>
                  <span className={`text-center font-semibold ${ratioColor(row.above_ma10_ratio_realtime)}`}>
                    {fmtPercent(row.above_ma10_ratio_realtime)}
                  </span>
                  <span className="text-center font-semibold text-gray-700">
                    {fmtPercent(row.above_ma10_ratio_csv)}
                  </span>
                  <span className={`text-center font-medium ${deltaColor(row.above_ma10_ratio_delta)}`}>
                    {fmtDelta(row.above_ma10_ratio_delta)}
                  </span>
                  <span className="text-center">{row.meat_count}</span>
                  <span className="text-center">{row.limit_up_count}</span>
                  <span className="text-center">{row.big_loss_count}</span>
                  <span className="text-center">{row.limit_down_count}</span>
                  <span className="text-center">{fmtAmountYi(row.today_main_inflow)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        )}
      </div>
    </>
  );
}
