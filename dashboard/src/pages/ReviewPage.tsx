import { useCallback, useEffect, useState } from 'react';
import { TabHeader } from '../components/TabHeader';
import { FilterTabs } from '../components/FilterTabs';
import { apiFetch } from '../lib/api';

// ─── 类型 ───────────────────────────────────────────────────
interface ReviewData {
  trade_date: string;
  computed_at: string;
  overview: {
    total_amount_yi: number;
    amount_ma20?: number;
    amount_ratio?: number;
    main_net_yi: number;
    main_net_ma20?: number;
    main_net_diff?: number;
    main_net_ratio?: number | null;
    retail_net_yi: number;
    up_count: number;
    down_count: number;
    stock_count: number;
  };
  sector_flow: Array<{ sector: string; main_net_yi: number; retail_net_yi: number; amount_yi: number; avg_change_pct: number; stock_count: number }>;
  limit_analysis: { limit_up_count: number; limit_down_count: number; limit_up_sectors: Array<{ sector: string; count: number }>; limit_down_sectors: Array<{ sector: string; count: number }>; limit_up_trend: Array<{ date: string; count: number }>; limit_down_trend: Array<{ date: string; count: number }> };
  market_cap_groups: Array<{ group: string; avg_change_pct: number; count: number; up_ratio: number; amount_yi: number }>;
  price_groups: Array<{ group: string; avg_change_pct: number; count: number; up_ratio: number; amount_yi: number }>;
  turnover_groups: Array<{ group: string; avg_change_pct: number; count: number; amount_yi: number }>;
  flow_trend: Array<{ date: string; main_yi: number; retail_yi: number }>;
  huddle: { current: number; ma20: number; trend: Array<{ date: string; ratio: number }> };
  style: { mode: string; cycle_avg: number; value_avg: number; growth_avg: number; sectors: Array<{ sector: string; strength: number; style: string }> };
  sentiment: { score: number; dimensions: Record<string, number> };
}

const fmtYi = (v?: number | null) => v != null ? `${(v).toFixed(2)}亿` : '--';
const fmtPct = (v?: number | null) => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '--';

const numColor = (v?: number | null) => v == null ? 'text-gray-400' : v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-gray-400';

const REVIEW_TABS = [
  { id: 'flow', label: '资金流' },
  { id: 'limit', label: '涨跌停' },
  { id: 'style', label: '风格研判' },
  { id: 'structure', label: '结构分布' },
  { id: 'sentiment', label: '情绪' },
  // DM-kun 市场分析 6 个 tab (quant.dm_kun 脚本产出, server 后台预热填 cache)
  { id: 'regime', label: '市场状态' },
  { id: 'sentiment-cycle', label: '情绪周期' },
  { id: 'industry-crowding', label: '行业拥挤' },
  { id: 'industry-enhanced', label: '行业增强' },
  { id: 'theme-ladder', label: '主题阶梯' },
  { id: 'stock-recommender', label: '选股推荐' },
];

export function ReviewPage() {
  const [data, setData] = useState<ReviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [activeTab, setActiveTab] = useState('flow');
  const [recomputing, setRecomputing] = useState(false);  // ⚠️ 必须放 top-level, 早 return 之前

  const fetchData = async (date?: string) => {
    setLoading(true);
    setError('');
    try {
      const url = date ? `/api/review/data?date=${date.replace(/-/g, '')}` : '/api/review/latest';
      const d = await apiFetch<ReviewData>(url);
      setData(d);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    apiFetch<string[]>('/api/review/dates').then(setDates).catch(() => {});
    fetchData();
  }, []);

  const handleDateChange = (d: string) => {
    setSelectedDate(d);
    fetchData(d);
  };

  const handleRecompute = async () => {
    if (recomputing) return;
    if (!confirm('重算当前显示日期的复盘数据?\n5000+ 股票 × 250 天约需 30-120 秒, 期间页面会卡住')) return;
    setRecomputing(true);
    setError('');
    try {
      const targetDate = (selectedDate || data?.trade_date || '').replace(/-/g, '');
      const url = `/api/review/data?date=${targetDate}&force=1`;
      await fetch(url);  // 后端 INSERT OR REPLACE
      // 重算完 reload
      await fetchData();
    } catch (e: any) {
      setError(e.message || '重算失败');
    } finally {
      setRecomputing(false);
    }
  };

  if (loading && !data) {
    return (
      <div>
        <TabHeader title="复盘数据" subtitle="基于全市场日线数据的板块效应分析" />
        <div className="px-4 py-12 text-center text-gray-400 text-sm">计算中...</div>
      </div>
    );
  }

  return (
    <div>
      <TabHeader title="复盘数据" subtitle={data ? `${data.trade_date} · 全市场${data.overview.stock_count}只股票` : ''} />

      {/* 日期选择器 */}
      {dates.length > 0 && (
        <div className="flex items-center gap-2 px-4 mb-4">
          <span className="text-xs text-gray-400">日期:</span>
          <select
            value={selectedDate}
            onChange={e => handleDateChange(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-3 py-1.5 text-gray-700"
          >
            <option value="">最新</option>
            {dates.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <button
            onClick={handleRecompute}
            disabled={recomputing || loading}
            className="ml-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-gray-700 border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
          >
            {recomputing ? '⏳ 重算中…' : '🔄 重算'}
          </button>
          {loading && <span className="text-xs text-amber-500">加载中...</span>}
          {error && <span className="text-xs text-red-500">{error}</span>}
        </div>
      )}

      {data && (
        <>
          {/* KPI 概览卡片 */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 px-4 mb-4">
            {[
              {
                label: '全市场成交额',
                value: fmtYi(data.overview.total_amount_yi),
                extra: (() => {
                  // 跟市场数据页 AmountRatioGauge 4 档色完全一致
                  const r = data.overview.amount_ratio;
                  if (r == null) return undefined;
                  if (r >= 1.5) return 'text-red-600';
                  if (r >= 1.0) return 'text-red-400';
                  if (r >= 0.8) return 'text-amber-500';
                  return 'text-green-600';
                })(),
                sub: data.overview.amount_ratio != null ? (() => {
                  const r = data.overview.amount_ratio!;
                  return {
                    text: `MA20 ${r.toFixed(2)}x`,
                    color: r >= 1.5 ? 'text-red-500' : r >= 1.0 ? 'text-red-400' : r >= 0.8 ? 'text-amber-500' : 'text-green-600',
                  };
                })() : undefined,
              },
              {
                label: '主力净流入',
                value: fmtYi(data.overview.main_net_yi),
                extra: numColor(data.overview.main_net_yi),
                sub: data.overview.main_net_ma20 != null ? (() => {
                  const diff = data.overview.main_net_diff ?? 0;
                  const ratio = data.overview.main_net_ratio;
                  return {
                    text: `MA20 ${diff >= 0 ? '+' : ''}${diff.toFixed(1)}亿${ratio != null ? ` · ${ratio}x` : ''}`,
                    color: diff > 50 ? 'text-red-500' : diff > 0 ? 'text-red-400' : diff < -50 ? 'text-green-600' : diff < 0 ? 'text-green-500' : 'text-gray-400',
                  };
                })() : undefined,
              },
              { label: '上涨家数', value: `${data.overview.up_count} 只`, extra: 'text-red-600' },
              { label: '下跌家数', value: `${data.overview.down_count} 只`, extra: 'text-green-600' },
              { label: '平盘家数', value: `${data.overview.stock_count - data.overview.up_count - data.overview.down_count} 只`, extra: 'text-gray-500' },
              { label: '抱团度', value: `${data.huddle.current}%`, extra: data.huddle.current > data.huddle.ma20 ? 'text-amber-600' : 'text-gray-500' },
            ].map(kpi => (
              <div key={kpi.label} className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
                <div className="text-xs text-gray-400 mb-1">{kpi.label}</div>
                <div className={`text-lg font-bold ${kpi.extra || 'text-gray-900'}`}>{kpi.value}</div>
                {kpi.sub && <div className={`text-[10px] mt-0.5 font-mono ${kpi.sub.color}`}>{kpi.sub.text}</div>}
              </div>
            ))}
          </div>

          {/* 子Tab */}
          <FilterTabs tabs={REVIEW_TABS} activeTab={activeTab} onTabChange={setActiveTab} />

          <div className="mt-3 space-y-4">
            {activeTab === 'flow' && <FlowTab data={data} />}
            {activeTab === 'limit' && <LimitTab data={data} />}
            {activeTab === 'style' && <StyleTab data={data} />}
            {activeTab === 'structure' && <StructureTab data={data} />}
            {activeTab === 'sentiment' && <SentimentTab data={data} />}
            {activeTab === 'regime' && <DmMarkdownTab name="market-regime" title="市场状态 (4 维: 趋势/波动/风格/宽度)" hint="10s 跑 12 指数 + 2839 股票" />}
            {activeTab === 'sentiment-cycle' && <DmMarkdownTab name="sentiment-cycle" title="情绪周期 (涨停/连板/炸板/涨跌停比)" hint="60s 跑 5879 只股票" />}
            {activeTab === 'industry-crowding' && <DmMarkdownTab name="industry-crowding" title="行业拥挤度 (成交占比 × 近 1 年分位)" hint="5s 跑 5477 只 × 31 行业" />}
            {activeTab === 'industry-enhanced' && <DmMarkdownTab name="industry-enhanced" title="行业增强分析 (BIAS20 热力 + 抱团检测 + 二级热点 + 持续性)" hint="30s 跑 5477 只" />}
            {activeTab === 'theme-ladder' && <DmMarkdownTab name="theme-ladder" title="主题阶梯 (广发机构视角: 题材热度梯次)" hint="10s 跑 31 行业" />}
            {activeTab === 'stock-recommender' && <DmMarkdownTab name="stock-recommender" title="选股推荐 (按拥挤区 top 5 行业筛强势股)" hint="5s 自动选 top 5 拥挤行业" />}
          </div>
        </>
      )}
    </div>
  );
}

// ─── 资金流 Tab ──────────────────────────────────────────────

function FlowTab({ data }: { data: ReviewData }) {
  const secFlow = data.sector_flow;

  return (
    <div className="space-y-4 px-4">
      {/* 近5日时间序列 */}
      <div className="bg-white rounded-xl border border-gray-100 p-4">
        <div className="text-sm font-semibold text-gray-900 mb-3">近5日资金流向</div>
        <div className="grid grid-cols-5 gap-2">
          {data.flow_trend.map((d, i) => (
            <div key={i} className="text-center">
              <div className="text-xs text-gray-400">{d.date.slice(5)}</div>
              <div className={`text-xs font-mono ${d.main_yi >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                {d.main_yi >= 0 ? '+' : ''}{d.main_yi.toFixed(0)}亿
              </div>
              <div className="text-[10px] text-gray-400">散户{d.retail_yi >= 0 ? '+' : ''}{d.retail_yi.toFixed(0)}亿</div>
            </div>
          ))}
        </div>
      </div>

      {/* 行业资金流排名 */}
      <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 text-sm font-semibold text-gray-900">
          行业资金流 ({secFlow.length})
        </div>
        <div className="grid grid-cols-6 px-4 py-2 text-xs text-gray-400 bg-gray-50">
          <span>行业</span><span className="text-center">主力净流入</span><span className="text-center">散户净流入</span><span className="text-center">成交额</span><span className="text-center">涨跌幅</span><span className="text-center">家数</span>
        </div>
        <div className="divide-y divide-gray-50 max-h-[600px] overflow-y-auto">
          {secFlow.map(row => (
            <div key={row.sector} className="grid grid-cols-6 px-4 py-2.5 text-xs hover:bg-gray-50">
              <span className="text-gray-800">{row.sector}</span>
              <span className={`text-center font-mono ${numColor(row.main_net_yi)}`}>{fmtYi(row.main_net_yi)}</span>
              <span className={`text-center font-mono ${numColor(row.retail_net_yi)}`}>{fmtYi(row.retail_net_yi)}</span>
              <span className="text-center text-gray-500">{row.amount_yi.toFixed(0)}亿</span>
              <span className={`text-center font-mono ${numColor(row.avg_change_pct)}`}>{fmtPct(row.avg_change_pct)}</span>
              <span className="text-center text-gray-500">{row.stock_count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── 涨跌停 Tab ──────────────────────────────────────────────

function LimitTab({ data }: { data: ReviewData }) {
  const la = data.limit_analysis;
  return (
    <div className="space-y-4 px-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">涨停</div>
          <div className="text-2xl font-bold text-red-600">{la.limit_up_count}</div>
        </div>
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">跌停</div>
          <div className="text-2xl font-bold text-green-600">{la.limit_down_count}</div>
        </div>
      </div>

      {/* 近5日趋势 */}
      <div className="bg-white rounded-xl border border-gray-100 p-4">
        <div className="text-sm font-semibold text-gray-900 mb-2">近5日趋势</div>
        <div className="flex items-end gap-3 h-20">
          {la.limit_up_trend.map((d, i) => (
            <div key={i} className="flex-1 flex flex-col items-center">
              <span className="text-xs font-mono text-red-600">{d.count}</span>
              <div className="w-full bg-red-100 rounded-t" style={{ height: `${Math.max(4, d.count / la.limit_up_trend.reduce((m, x) => Math.max(m, x.count), 1) * 60)}px` }} />
              <span className="text-[10px] text-gray-400">{d.date.slice(5)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 行业分布 */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
          <div className="px-4 py-2 text-xs text-gray-400 bg-gray-50">涨停行业分布 Top10</div>
          {la.limit_up_sectors.map(r => (
            <div key={r.sector} className="flex justify-between px-4 py-2 text-xs border-b border-gray-50">
              <span>{r.sector}</span>
              <span className="text-red-600">{r.count}</span>
            </div>
          ))}
        </div>
        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
          <div className="px-4 py-2 text-xs text-gray-400 bg-gray-50">跌停行业分布 Top10</div>
          {la.limit_down_sectors.map(r => (
            <div key={r.sector} className="flex justify-between px-4 py-2 text-xs border-b border-gray-50">
              <span>{r.sector}</span>
              <span className="text-green-600">{r.count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── 风格研判 Tab ─────────────────────────────────────────────

function StyleTab({ data }: { data: ReviewData }) {
  const s = data.style;
  return (
    <div className="space-y-4 px-4">
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: '当前模式', val: s.mode, color: 'text-indigo-600' },
          { label: '成长', val: `${s.growth_avg}%`, color: 'text-blue-600' },
          { label: '价值', val: `${s.value_avg}%`, color: 'text-teal-600' },
          { label: '周期', val: `${s.cycle_avg}%`, color: 'text-amber-600' },
          { label: '抱团度', val: `${data.huddle.current}%`, color: 'text-gray-700' },
          { label: 'MA20', val: `${data.huddle.ma20}%`, color: 'text-gray-500' },
        ].map(kpi => (
          <div key={kpi.label} className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
            <div className="text-xs text-gray-400">{kpi.label}</div>
            <div className={`text-lg font-bold ${kpi.color}`}>{kpi.val}</div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
        <div className="px-4 py-2 text-xs text-gray-400 bg-gray-50">行业强势度排名 (收盘{'>'}MA10占比)</div>
        {s.sectors.map(r => (
          <div key={r.sector} className="flex items-center gap-3 px-4 py-2 text-xs border-b border-gray-50 hover:bg-gray-50">
            <span className="text-gray-400 w-12">{r.style}</span>
            <span className="flex-1 text-gray-800">{r.sector}</span>
            <div className="w-40 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${Math.min(100, r.strength)}%` }} />
            </div>
            <span className="w-12 text-right font-mono text-gray-700">{r.strength}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── 结构分布 Tab ─────────────────────────────────────────────

function StructureTab({ data }: { data: ReviewData }) {
  const renderGroup = (title: string, groups: Array<{ group: string; avg_change_pct: number; count: number; up_ratio?: number; amount_yi: number }>) => (
    <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
      <div className="px-4 py-2 text-xs text-gray-400 bg-gray-50">{title}</div>
      <div className="grid grid-cols-5 px-4 py-1.5 text-[10px] text-gray-400">
        <span>分组</span><span className="text-center">涨跌</span><span className="text-center">家数</span><span className="text-center">上涨比</span><span className="text-center">成交额</span>
      </div>
      {groups.map(g => (
        <div key={g.group} className="grid grid-cols-5 px-4 py-2 text-xs border-b border-gray-50">
          <span className="text-gray-800">{g.group}</span>
          <span className={`text-center font-mono ${numColor(g.avg_change_pct)}`}>{fmtPct(g.avg_change_pct)}</span>
          <span className="text-center text-gray-500">{g.count}</span>
          <span className="text-center text-gray-500">{g.up_ratio != null ? `${g.up_ratio}%` : '--'}</span>
          <span className="text-center text-gray-500">{g.amount_yi.toFixed(0)}亿</span>
        </div>
      ))}
    </div>
  );

  return (
    <div className="space-y-4 px-4">
      {renderGroup('市值分布', data.market_cap_groups)}
      {renderGroup('价格区间', data.price_groups)}
      {renderGroup('换手率分布', data.turnover_groups)}
    </div>
  );
}

// ─── 情绪 Tab ─────────────────────────────────────────────────

function SentimentTab({ data }: { data: ReviewData }) {
  const sent = data.sentiment;
  return (
    <div className="space-y-4 px-4">
      <div className="kpi-card bg-white rounded-xl border border-gray-100 p-4 text-center">
        <div className="text-xs text-gray-400 mb-2">综合情绪评分</div>
        <div className={`text-4xl font-bold ${sent.score >= 50 ? 'text-red-600' : sent.score >= 30 ? 'text-amber-600' : 'text-green-600'}`}>
          {sent.score}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(sent.dimensions).map(([key, val]) => (
          <div key={key} className="bg-white rounded-xl border border-gray-100 p-3 flex justify-between items-center">
            <span className="text-xs text-gray-500">{key}</span>
            <span className="text-sm font-semibold text-gray-800">{typeof val === 'number' && val % 1 !== 0 ? val.toFixed(1) : val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── DM-kun 通用 markdown Tab ──────────────────────────────
// 给 review 页 3 个新 tab 共用: market-regime / sentiment-cycle / industry-crowding
// 数据来源: server.py 的 quant.dm_kun.<脚本>.main() 后台预热填内存 cache
// endpoint: GET /api/dm-kun/<name> 返 {markdown, computed_at, loading, error}
//          POST /api/dm-kun/<name>/recompute 手动重算 (后台线程)

interface DmCache {
  markdown: string | null;
  computed_at: string | null;
  loading: boolean;
  error: string | null;
}

function DmMarkdownTab({ name, title, hint }: { name: 'market-regime' | 'sentiment-cycle' | 'industry-crowding' | 'industry-enhanced' | 'theme-ladder' | 'stock-recommender'; title: string; hint: string }) {
  const [cache, setCache] = useState<DmCache | null>(null);
  const [loading, setLoading] = useState(true);
  const [recomputing, setRecomputing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiFetch<DmCache>(`/api/dm-kun/${name}`);
      setCache(d);
    } catch (e: any) {
      setCache({ markdown: null, computed_at: null, loading: false, error: e.message || '加载失败' });
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRecompute = async () => {
    if (recomputing) return;
    if (!confirm(`重算 ${title}?\n${hint}\n跑完会自动刷新当前数据`)) return;
    setRecomputing(true);
    try {
      await fetch(`/api/dm-kun/${name}/recompute`, { method: 'POST' });
      // poll 最多 90s, 每 2s 拉一次, loading=false 就 break
      const start = Date.now();
      while (Date.now() - start < 90000) {
        await new Promise(r => setTimeout(r, 2000));
        const fresh = await apiFetch<DmCache>(`/api/dm-kun/${name}`);
        setCache(fresh);
        if (!fresh.loading) break;
      }
    } catch (e: any) {
      setCache(prev => prev ? { ...prev, error: e.message || '重算失败' } : { markdown: null, computed_at: null, loading: false, error: e.message || '重算失败' });
    } finally {
      setRecomputing(false);
    }
  };

  return (
    <div className="space-y-3 px-4">
      {/* 状态条: 标题 + computed_at + loading + 重算按钮 */}
      <div className="flex items-center gap-3 text-xs">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-gray-900 mb-1">{title}</div>
          <div className="flex items-center gap-2 text-gray-400">
            {cache?.computed_at ? (
              <span>📅 {cache.computed_at} 计算</span>
            ) : cache?.loading ? (
              <span className="text-amber-500">⟳ 加载中...</span>
            ) : (
              <span>未计算</span>
            )}
            {recomputing && <span className="text-blue-500">⟳ 重算中...</span>}
            <span className="text-gray-300">·</span>
            <span className="text-gray-400">{hint}</span>
          </div>
        </div>
        <button
          onClick={handleRecompute}
          disabled={recomputing || loading}
          className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-gray-700 border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
        >
          {recomputing ? '⏳ 重算中…' : '🔄 重算'}
        </button>
      </div>

      {/* 错误提示 */}
      {cache?.error && (
        <div className="bg-red-50 border border-red-200 text-red-800 text-xs rounded-lg p-3">
          ⚠ {cache.error}
        </div>
      )}

      {/* markdown 内容 (脚本 main() 的 stdout 输出) */}
      {cache?.markdown ? (
        <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">
          {cache.markdown}
        </pre>
      ) : !loading ? (
        <div className="text-center text-gray-400 text-sm py-12">
          暂无数据, 点 🔄 重算 跑一次
        </div>
      ) : null}
    </div>
  );
}
