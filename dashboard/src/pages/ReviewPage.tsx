import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
      // 直接传 ISO 格式 (DB 存的就是 ISO) — 后端兼容 compact 格式做兜底
      const url = date ? `/api/review/data?date=${date}` : '/api/review/latest';
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
      const targetDate = selectedDate || data?.trade_date || '';
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
                // 学习自 MarketSentimentPage.AmountRatioGauge: 0-2x 量程水平 Gauge
                // 1.0 = 均值线 (50% 位置), 复用 kpi-gauge-bar CSS 动画
                gauge: data.overview.amount_ratio != null ? (() => {
                  const r = data.overview.amount_ratio!;
                  return {
                    ratio: r,
                    color: r >= 1.5 ? '#ef4444' : r >= 1.0 ? '#f87171' : r >= 0.8 ? '#f59e0b' : '#16a34a',
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
                {kpi.gauge && <MiniGauge ratio={kpi.gauge.ratio} color={kpi.gauge.color} />}
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

// ─── 行业拥挤度专用渲染 (从 markdown 提取, 替代 raw <pre>) ─────
// 学习自 quant/dm_kun/industry_crowding_analyzer.py 的 stdout 输出格式
// 重点: 31 行业热力表 (1y/3y/5y 分位染色), 拥挤/出清区横向 bar
interface IndustryRow {
  name: string;
  assetClass: string;
  ratio5: number;       // 占比 MA5 (%)
  y1: number;           // 1年分位 (%)
  y3: number;           // 3年分位 (%)
  y5: number;           // 5年分位 (%)
  dir: string;          // 5日方向 ↑/→/↓
  zone: string;         // 🔴拥挤 / 中性 / 🟢出清
  y3Mean: number;
  y3Max: number;
  y3Min: number;
}
interface AssetRule {
  name: string;
  zone: string;
  assetClass: string;
  rule: string;
}
interface ParsedIndustry {
  amount20d: number | null;
  amount60d: number | null;
  crowded: Array<{ name: string; y1: number; y3: number; y5: number }>;
  cleared: Array<{ name: string; y1: number; y3: number; y5: number }>;
  rows: IndustryRow[];
  assetRules: AssetRule[];
}

function parseIndustryCrowding(md: string): ParsedIndustry | null {
  // 顶部统计
  const m20 = md.match(/近20日均值\s*(\d+)\s*亿/);
  const m60 = md.match(/60日均值\s*(\d+)\s*亿/);

  // 拥挤区 / 出清区 list
  const parseList = (s: string | undefined) => {
    if (!s) return [];
    return s.split('、').map(item => {
      const m = item.trim().match(/(.+?)\(1y:(\d+)%\s*\/\s*3y:(\d+)%\s*\/\s*5y:(\d+)%\)/);
      if (!m) return null;
      return { name: m[1].trim(), y1: +m[2], y3: +m[3], y5: +m[4] };
    }).filter(Boolean) as Array<{ name: string; y1: number; y3: number; y5: number }>;
  };
  const crowdedM = md.match(/\*\*🔴\s*拥挤区[^*]*\*\*[：:]\s*(.+)/);
  const clearedM = md.match(/\*\*🟢\s*出清区[^*]*\*\*[：:]\s*(.+)/);
  const crowded = parseList(crowdedM?.[1]);
  const cleared = parseList(clearedM?.[1]);

  // 找 markdown 表格 (主表 + 分资产类别表)
  const lines = md.split('\n');
  const tables: string[][] = [];
  let cur: string[] = [];
  for (const l of lines) {
    if (l.trim().startsWith('|')) {
      cur.push(l);
    } else {
      if (cur.length >= 3) tables.push(cur);
      cur = [];
    }
  }
  if (cur.length >= 3) tables.push(cur);

  // 解析表格行 → dict
  const parseTable = (table: string[]): Array<Record<string, string>> => {
    if (table.length < 3) return [];
    const header = table[0].split('|').slice(1, -1).map(s => s.trim());
    return table.slice(2).map(l => {
      const cells = l.split('|').slice(1, -1).map(s => s.trim());
      const row: Record<string, string> = {};
      header.forEach((h, i) => { row[h] = cells[i] || ''; });
      return row;
    });
  };

  // 主表: 31 行业
  const mainTable = parseTable(tables[0] || []);
  const rows: IndustryRow[] = mainTable.map(r => ({
    name: r['行业'] || '',
    assetClass: r['资产类别'] || '',
    ratio5: parseFloat(r['占比MA5(%)']) || 0,
    y1: parseFloat((r['1年分位'] || '0').replace('%', '')) || 0,
    y3: parseFloat((r['3年分位'] || '0').replace('%', '')) || 0,
    y5: parseFloat((r['5年分位'] || '0').replace('%', '')) || 0,
    dir: r['5日方向'] || '',
    zone: r['区间'] || '',
    y3Mean: parseFloat(r['3年均值']) || 0,
    y3Max: parseFloat(r['3年最高']) || 0,
    y3Min: parseFloat(r['3年最低']) || 0,
  }));

  // 分资产类别表
  const ruleTable = parseTable(tables[1] || []);
  const assetRules: AssetRule[] = ruleTable.map(r => ({
    name: r['行业'] || '',
    zone: r['区间'] || '',
    assetClass: r['资产类别'] || '',
    rule: r['适用规律'] || '',
  }));

  return {
    amount20d: m20 ? +m20[1] : null,
    amount60d: m60 ? +m60[1] : null,
    crowded, cleared, rows, assetRules,
  };
}

// 分位热力色: 0-20% 绿 / 20-50% 浅黄 / 50-80% 橙 / 80-100% 红
function percentileColor(p: number): string {
  if (p >= 80) return 'bg-red-100 text-red-700';
  if (p >= 50) return 'bg-orange-100 text-orange-700';
  if (p >= 20) return 'bg-yellow-50 text-yellow-700';
  return 'bg-green-100 text-green-700';
}

// ════════════════════════════════════════════════════════════════
// 选股推荐 (stock_recommender) 解析 + 图形化
// ════════════════════════════════════════════════════════════════
interface RecStock {
  rank: number; code: string; name: string; price: number;
  score: number; d20: number; mainNet3: number; reason: string;
}
interface RecIndustry {
  name: string; stocks: RecStock[];
}
interface RecOverview {
  scanIndustries: string[];
  totalFiles: number;
  totalStocks: number;
  filteredStocks: number;
}
interface ParsedRecommender {
  overview: RecOverview;
  industries: RecIndustry[];
}

function parseRecommender(md: string): ParsedRecommender | null {
  const overview: RecOverview = {
    scanIndustries: [], totalFiles: 0, totalStocks: 0, filteredStocks: 0,
  };
  const scanM = md.match(/扫描行业:\s*\[([^\]]+)\]/);
  if (scanM) {
    overview.scanIndustries = scanM[1].match(/['"]([^'"]+)['"]/g)?.map(s => s.replace(/['"]/g, '')) || [];
  }
  const fileM = md.match(/共\s*(\d+)\s*个股票文件/);
  if (fileM) overview.totalFiles = parseInt(fileM[1]);
  const foundM = md.match(/找到\s*(\d+)\s*只目标行业股票/);
  if (foundM) overview.totalStocks = parseInt(foundM[1]);
  const filtM = md.match(/过滤后[（(][^）)]+[）)]:\s*(\d+)\s*只/);
  if (filtM) overview.filteredStocks = parseInt(filtM[1]);

  const industries: RecIndustry[] = [];
  // 按 "## 行业名" 切分 (markdown h2)
  const blocks = md.split(/\n## /);
  for (const block of blocks) {
    const firstLine = block.split('\n')[0].trim();
    // "通信" "建筑材料" "电子" "有色金属" - 中文行业名
    if (!firstLine || firstLine.includes('强势行业个股推荐') || firstLine.startsWith('|') || firstLine.startsWith('#')) continue;
    if (!/^[\u4e00-\u9fa5]{2,8}$/.test(firstLine)) continue;
    const stocks: RecStock[] = [];
    const lines = block.split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length < 8) continue;
      const codeM = c[1].match(/([a-z]{2}\d{6})/);
      const priceM = c[3].match(/([\d.]+)/);
      const scoreM = c[4].match(/(\d+)/);
      const d20M = c[5].match(/([+\-]?[\d.]+)/);
      const mainM = c[6].match(/([+\-]?[\d.]+)/);
      if (codeM) {
        stocks.push({
          rank: parseInt(c[0]),
          code: codeM[1],
          name: c[2],
          price: priceM ? parseFloat(priceM[1]) : 0,
          score: scoreM ? parseInt(scoreM[1]) : 0,
          d20: d20M ? parseFloat(d20M[1]) : 0,
          mainNet3: mainM ? parseFloat(mainM[1]) : 0,
          reason: c[7],
        });
      }
    }
    if (stocks.length > 0) industries.push({ name: firstLine, stocks });
  }

  if (!industries.length) return null;
  return { overview, industries };
}

function RecommenderView({ md }: { md: string }) {
  const p = useMemo(() => parseRecommender(md), [md]);
  if (!p) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }
  // 找全局最大综合分
  const maxScore = Math.max(...p.industries.flatMap(i => i.stocks.map(s => s.score)), 1);
  return (
    <div className="space-y-4">
      {/* 区域 1: 概览 */}
      <div className="bg-white border border-gray-100 rounded-xl p-3">
        <div className="flex items-center justify-between flex-wrap gap-2 text-xs">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-700">🔍 扫描行业</span>
            {p.overview.scanIndustries.map(s => (
              <span key={s} className="text-[10px] px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-100 rounded">
                {s}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-3 text-[10px] text-gray-500">
            <span>总股票: <span className="font-mono font-semibold text-gray-700">{p.overview.totalFiles}</span></span>
            <span>目标行业: <span className="font-mono font-semibold text-gray-700">{p.overview.totalStocks}</span></span>
            <span>过滤后: <span className="font-mono font-semibold text-red-600">{p.overview.filteredStocks}</span></span>
          </div>
        </div>
      </div>
      {/* 区域 2: 4 行业 × 10 股票表 */}
      {p.industries.map(ind => (
        <div key={ind.name} className="bg-white border border-gray-100 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <div className="text-sm font-semibold text-gray-700">{ind.name}</div>
            <span className="text-[10px] text-gray-500">{ind.stocks.length} 只 · 按综合分排序</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-100">
                  <th className="text-left py-1.5 pr-2 font-medium">#</th>
                  <th className="text-left py-1.5 px-1 font-medium">代码</th>
                  <th className="text-left py-1.5 px-1 font-medium">名称</th>
                  <th className="text-right py-1.5 px-1 font-medium">现价</th>
                  <th className="text-right py-1.5 px-1 font-medium">综合分</th>
                  <th className="text-left py-1.5 px-1 font-medium" style={{ minWidth: 100 }}>强度</th>
                  <th className="text-right py-1.5 px-1 font-medium">20日涨</th>
                  <th className="text-right py-1.5 px-1 font-medium">主力3日</th>
                  <th className="text-left py-1.5 pl-2 font-medium">核心理由</th>
                </tr>
              </thead>
              <tbody>
                {ind.stocks.map(s => {
                  const barPct = (s.score / maxScore) * 100;
                  const barColor = s.score >= 70 ? 'bg-red-500' :
                                   s.score >= 60 ? 'bg-orange-400' :
                                   s.score >= 50 ? 'bg-yellow-400' : 'bg-gray-300';
                  return (
                    <tr key={s.code} className="border-b border-gray-50 hover:bg-gray-50/50">
                      <td className="py-1.5 pr-2 text-gray-400 font-mono text-[10px]">{s.rank}</td>
                      <td className="py-1.5 px-1 font-mono text-[10px] text-gray-500">{s.code}</td>
                      <td className="py-1.5 px-1 font-medium text-gray-700 whitespace-nowrap">{s.name}</td>
                      <td className="py-1.5 px-1 text-right font-mono text-gray-700">{s.price.toFixed(2)}</td>
                      <td className="py-1.5 px-1 text-right font-mono font-semibold text-gray-800">{s.score}</td>
                      <td className="py-1.5 px-1">
                        <div className="h-2.5 bg-gray-50 rounded overflow-hidden">
                          <div className={`h-full rounded ${barColor}`} style={{ width: `${barPct}%` }} />
                        </div>
                      </td>
                      <td className={`py-1.5 px-1 text-right font-mono ${pctColor(s.d20)}`}>
                        {s.d20 > 0 ? '+' : ''}{s.d20.toFixed(1)}%
                      </td>
                      <td className={`py-1.5 px-1 text-right font-mono ${pctColor(s.mainNet3)}`}>
                        {s.mainNet3 > 0 ? '+' : ''}{s.mainNet3.toFixed(1)}亿
                      </td>
                      <td className="py-1.5 pl-2 text-gray-600 text-[10px]">{s.reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// 主题阶梯 (theme_ladder) 解析 + 6 区域图形化
// ════════════════════════════════════════════════════════════════
interface SentimentSummary {
  ztCount: number; ztNorm: string; ztLevel: string; ztNote: string;
  highBan: string; highBanNorm: string;
  firstBan: number; firstBanProj: string;
}
interface MainLineRow {
  theme: string; todayZt: number; todayPct: number;
  yestPct: number; prePct: number; judge: string;
}
interface PyramidRow {
  theme: string; low: number; mid: number; high: number; topBan: string; shape: string;
}
interface PromotionRow {
  path: string; yesterday: number; today: number; rate: number; note: string;
}
interface FiveClassMember {
  role: string; name: string; code: string; note: string;
}
interface FiveClassGroup {
  caliber: string; theme: string;
  members: FiveClassMember[];
}
interface PoolStock {
  pool: string; theme: string; code: string; name: string; reason: string;
}
interface ParsedThemeLadder {
  summary: SentimentSummary;
  mainLines: MainLineRow[];
  hasMainLine: boolean;     // 题材荒 = false
  pyramid: PyramidRow[];
  promotion: PromotionRow[];
  fiveClass: FiveClassGroup[];
  pools: PoolStock[];
  stage: string;            // 轮动补涨/题材荒/...
  stageNote: string;
}

function parseThemeLadder(md: string): ParsedThemeLadder | null {
  // 短线情绪
  const summary: SentimentSummary = {
    ztCount: 0, ztNorm: '', ztLevel: '', ztNote: '',
    highBan: '', highBanNorm: '', firstBan: 0, firstBanProj: '',
  };
  const ztM = md.match(/涨停\s*(\d+)\s*家[（(]([^）)]+)[）)][^→]*→\s*([^→\n]+)[→\n]([^（\n]+)/);
  if (ztM) {
    summary.ztCount = parseInt(ztM[1]);
    summary.ztNorm = ztM[2];
    summary.ztLevel = ztM[3].trim();
    summary.ztNote = ztM[4].trim();
  }
  const hbM = md.match(/高度板\s*(\d+B)[（(]([^）)]+)[）)]/);
  if (hbM) {
    summary.highBan = hbM[1];
    summary.highBanNorm = hbM[2];
  }
  const fbM = md.match(/首板\s*(\d+)\s*家/);
  if (fbM) summary.firstBan = parseInt(fbM[1]);
  const projM = md.match(/今日首板\s*(\d+)\s*家\s*→\s*(\S+)/);
  if (projM) summary.firstBanProj = projM[2];

  // 主线判定
  const mainLines: MainLineRow[] = [];
  const mlBlock = md.match(/### 主线题材判定[\s\S]*?\n([\s\S]*?)(?=\n###|\*\*⚠️|\n\n)/);
  if (mlBlock) {
    const lines = mlBlock[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 5) {
        const todayZtM = c[1].match(/(\d+)/);
        const todayPctM = c[2].match(/(\d+)/);
        const yestPctM = c[3].match(/(\d+)/);
        const prePctM = c[4].match(/(\d+)/);
        mainLines.push({
          theme: c[0],
          todayZt: todayZtM ? parseInt(todayZtM[1]) : 0,
          todayPct: todayPctM ? parseInt(todayPctM[1]) : 0,
          yestPct: yestPctM ? parseInt(yestPctM[1]) : 0,
          prePct: prePctM ? parseInt(prePctM[1]) : 0,
          judge: c[5] || '',
        });
      }
    }
  }
  // 题材荒
  const hasMainLine = mainLines.some(r => r.todayPct >= 40);
  const droughtM = md.match(/\*\*⚠️ 题材荒[（(]无主线[）)]\*\*[：:]\s*([^\n]+(?:\n[^-#\n][^\n]*)*)/);
  const drought = droughtM ? droughtM[1].trim() : '';

  // 梯队金字塔
  const pyramid: PyramidRow[] = [];
  const pyBlock = md.match(/### 题材梯队金字塔[\s\S]*?\n([\s\S]*?)(?=\n###|\n\n)/);
  if (pyBlock) {
    const lines = pyBlock[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 6) {
        pyramid.push({
          theme: c[0],
          low: parseInt(c[1]) || 0,
          mid: parseInt(c[2]) || 0,
          high: parseInt(c[3]) || 0,
          topBan: c[4],
          shape: c[5],
        });
      }
    }
  }

  // 晋级率
  const promotion: PromotionRow[] = [];
  const prBlock = md.match(/### 晋级率[\s\S]*?\n([\s\S]*?)(?=\n###|\n\n)/);
  if (prBlock) {
    const lines = prBlock[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 4) {
        const yestM = c[1].match(/(\d+)/);
        const todayM = c[2].match(/(\d+)/);
        const rateM = c[3].match(/(\d+)/);
        promotion.push({
          path: c[0],
          yesterday: yestM ? parseInt(yestM[1]) : 0,
          today: todayM ? parseInt(todayM[1]) : 0,
          rate: rateM ? parseInt(rateM[1]) : 0,
          note: c[4] || '',
        });
      }
    }
  }

  // 五类结构 - 解析 3 段 (申万一级/二级/概念)
  const fiveClass: FiveClassGroup[] = [];
  // 申万一级
  const sw1Block = md.match(/### 板块内五类结构[（(]申万一级[）)][\s\S]*?(?=\n###|\n\n###|$)/);
  if (sw1Block) {
    const sw1Lines = sw1Block[0].split('\n').filter(l => l.trim().startsWith('**'));
    for (const line of sw1Lines) {
      // "**医药生物** —— 龙头：... ｜ 人气股：... ｜ 中军：... ｜ 先锋：... ｜ 杂毛：..."
      const themeM = line.match(/^\*\*([^*]+)\*\*\s*——\s*(.+)$/);
      if (themeM) {
        const theme = themeM[1].trim();
        const content = themeM[2];
        const members: FiveClassMember[] = [];
        const parts = content.split(' ｜ ');
        for (const p of parts) {
          const m = p.match(/^(龙头|人气股|中军|先锋|杂毛)[：:]\s*(.+?)(?:（([^）]+)）)?$/);
          if (m) {
            const text = m[2].trim();
            const codeM = text.match(/(\w{2}\d{6})/);
            members.push({
              role: m[1],
              name: text.replace(/\(\w{2}\d{6}\)/, '').replace(/（[\d.亿]+家）/, '').trim(),
              code: codeM ? codeM[1] : '',
              note: m[3] || '',
            });
          }
        }
        if (members.length > 0) fiveClass.push({ caliber: '申万一级', theme, members });
      }
    }
  }

  // 观察池 - 5 池
  const pools: PoolStock[] = [];
  const poolPatterns: { pool: string; re: RegExp }[] = [
    { pool: '容量核心', re: /\*\*容量核心\*\*[^*]*?\n([\s\S]*?)(?=\n\*\*|\n###|$)/ },
    { pool: '情绪核心', re: /\*\*情绪核心\*\*[^*]*?\n([\s\S]*?)(?=\n\*\*|\n###|$)/ },
    { pool: '人气股',   re: /\*\*人气股\*\*[^*]*?\n([\s\S]*?)(?=\n\*\*|\n###|$)/ },
    { pool: '补涨候选', re: /\*\*补涨候选\*\*[^*]*?\n([\s\S]*?)(?=\n\*\*|\n###|$)/ },
    { pool: '低吸候选', re: /\*\*低吸候选\*\*[^*]*?\n([\s\S]*?)(?=\n\*\*|\n###|$)/ },
  ];
  for (const { pool, re } of poolPatterns) {
    const m = md.match(re);
    if (!m) continue;
    // 每行 "- [医药生物] 药明康德(sh603259) —— 题材成交额第1..."
    for (const line of m[1].split('\n')) {
      const lm = line.match(/-\s*\[([^\]]+)\]\s*(\S+?)\(([a-z]{2}\d{6})\)\s*——\s*(.+)$/);
      if (lm) {
        pools.push({ pool, theme: lm[1], name: lm[2], code: lm[3], reason: lm[4].trim() });
      }
    }
  }

  // 阶段判定
  const stageM = md.match(/\*\*([^*]+阶段)\*\*[：:]?\s*([^\n]+(?:\n[^#*\n][^\n]*)*)/);
  const stage = stageM ? stageM[1].trim() : '';
  const stageNote = stageM ? stageM[2].trim() : '';

  if (!mainLines.length && !pyramid.length && !promotion.length) return null;
  return { summary, mainLines, hasMainLine: hasMainLine && !drought, pyramid, promotion, fiveClass, pools, stage, stageNote };
}

// 主题阶梯 - 顶部 4 数字卡
function SentSummaryCards({ s, hasMainLine }: { s: SentimentSummary; hasMainLine: boolean }) {
  const cards = [
    { label: '涨停家数', value: `${s.ztCount}`, sub: s.ztNorm, color: s.ztCount >= 50 ? 'text-red-600' : s.ztCount >= 30 ? 'text-orange-600' : 'text-gray-600' },
    { label: '高度板', value: s.highBan, sub: s.highBanNorm, color: 'text-purple-600' },
    { label: '首板家数', value: `${s.firstBan}`, sub: s.firstBanProj, color: 'text-blue-600' },
    { label: '主线条数', value: hasMainLine ? '有主线' : '题材荒', sub: hasMainLine ? '情绪一致' : '轮动补涨', color: hasMainLine ? 'text-red-600' : 'text-orange-600' },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((c, i) => (
        <div key={i} className="bg-white border border-gray-100 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">{c.label}</div>
          <div className={`text-xl font-bold ${c.color}`}>{c.value}</div>
          <div className="text-[10px] text-gray-400 mt-0.5">{c.sub}</div>
        </div>
      ))}
    </div>
  );
}

// 主线判定 - 8 行业 × 6 列 (今日/昨日/前日 占比横向条)
function MainLineTable({ rows, hasMainLine }: { rows: MainLineRow[]; hasMainLine: boolean }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-xs font-semibold text-gray-700">🎯 主线题材判定 (涨停占比 ≥ 40% 连续 2 天 = 主线)</div>
        <span className={`text-[10px] px-2 py-0.5 rounded ${hasMainLine ? 'bg-red-50 text-red-700 border border-red-100' : 'bg-orange-50 text-orange-700 border border-orange-100'}`}>
          {hasMainLine ? '✅ 主线存在' : '⚠️ 题材荒'}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-100">
              <th className="text-left py-1.5 pr-2 font-medium">题材</th>
              <th className="text-right py-1.5 px-1 font-medium">今日涨停</th>
              <th className="text-left py-1.5 px-1 font-medium" colSpan={3}>3 日占比 (今日 / 昨日 / 前日)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const max = Math.max(r.todayPct, r.yestPct, r.prePct, 1);
              return (
                <tr key={r.theme} className="border-b border-gray-50">
                  <td className="py-1.5 pr-2 font-medium text-gray-700 whitespace-nowrap">{r.theme}</td>
                  <td className="py-1.5 px-1 text-right font-mono font-semibold text-red-600">{r.todayZt}</td>
                  <td className="py-1.5 px-1">
                    <div className="flex items-center gap-1">
                      <div className="w-16 h-3 bg-gray-50 rounded overflow-hidden">
                        <div className="h-full bg-red-500 rounded" style={{ width: `${(r.todayPct / max) * 100}%` }} />
                      </div>
                      <span className="font-mono text-[10px] w-7 text-right">{r.todayPct}%</span>
                    </div>
                  </td>
                  <td className="py-1.5 px-1">
                    <div className="flex items-center gap-1">
                      <div className="w-16 h-3 bg-gray-50 rounded overflow-hidden">
                        <div className="h-full bg-orange-400 rounded" style={{ width: `${(r.yestPct / max) * 100}%` }} />
                      </div>
                      <span className="font-mono text-[10px] w-7 text-right">{r.yestPct}%</span>
                    </div>
                  </td>
                  <td className="py-1.5 px-1">
                    <div className="flex items-center gap-1">
                      <div className="w-16 h-3 bg-gray-50 rounded overflow-hidden">
                        <div className="h-full bg-yellow-400 rounded" style={{ width: `${(r.prePct / max) * 100}%` }} />
                      </div>
                      <span className="font-mono text-[10px] w-7 text-right">{r.prePct}%</span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 梯队金字塔
function PyramidTable({ rows }: { rows: PyramidRow[] }) {
  const maxLow = Math.max(...rows.map(r => r.low), 1);
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-700 mb-3">🏛️ 题材梯队金字塔 (越完整持续性越强)</div>
      <div className="space-y-2">
        {rows.map(r => (
          <div key={r.theme} className="flex items-center gap-2 text-xs">
            <span className="w-20 truncate text-gray-700 font-medium" title={r.theme}>{r.theme}</span>
            <div className="flex-1 flex items-center gap-1">
              <div className="flex items-center" title={`低位 1-3B: ${r.low}家`}>
                <div className="h-3 bg-gray-50 rounded overflow-hidden flex items-center">
                  <div className="h-full bg-green-400 rounded-l" style={{ width: `${Math.max((r.low / maxLow) * 100, 5)}%`, minWidth: '8px' }} />
                </div>
                <span className="font-mono text-[10px] ml-1 w-5 text-green-700">{r.low}</span>
              </div>
              <div className="flex items-center" title={`中位 4-6B: ${r.mid}家`}>
                <div className="h-3 bg-gray-50 rounded overflow-hidden flex items-center">
                  <div className="h-full bg-orange-400 rounded" style={{ width: `${Math.max((r.mid / maxLow) * 100, 5)}%`, minWidth: r.mid > 0 ? '8px' : '0' }} />
                </div>
                <span className="font-mono text-[10px] ml-1 w-5 text-orange-700">{r.mid}</span>
              </div>
              <div className="flex items-center" title={`高位 7B+: ${r.high}家`}>
                <div className="h-3 bg-gray-50 rounded overflow-hidden flex items-center">
                  <div className="h-full bg-red-500 rounded-r" style={{ width: `${Math.max((r.high / maxLow) * 100, 5)}%`, minWidth: r.high > 0 ? '8px' : '0' }} />
                </div>
                <span className="font-mono text-[10px] ml-1 w-5 text-red-700">{r.high}</span>
              </div>
            </div>
            <span className="font-mono text-[10px] text-gray-600 w-10 text-center">{r.topBan}</span>
            <span className="text-[10px] text-gray-500 w-32 truncate" title={r.shape}>{r.shape}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 text-[10px] text-gray-400 mt-2 pt-2 border-t border-gray-50">
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-400 rounded"/>低位 1-3B</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-orange-400 rounded"/>中位 4-6B</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500 rounded"/>高位 7B+</span>
      </div>
    </div>
  );
}

// 晋级率
function PromotionTable({ rows }: { rows: PromotionRow[] }) {
  const maxYest = Math.max(...rows.map(r => r.yesterday), 1);
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs font-semibold text-gray-700 mb-3">📈 晋级率 (昨日 N 板 → 今日 N+1 板)</div>
      <div className="space-y-2">
        {rows.map(r => {
          const barPct = (r.yesterday / maxYest) * 100;
          const rateColor = r.rate >= 30 ? 'text-red-600' : r.rate >= 10 ? 'text-orange-600' : 'text-green-600';
          return (
            <div key={r.path} className="flex items-center gap-2 text-xs">
              <span className="w-12 font-mono text-gray-700 font-semibold">{r.path}</span>
              <div className="flex-1 flex items-center gap-2">
                <span className="text-[10px] text-gray-500 w-12">基数 {r.yesterday}</span>
                <div className="flex-1 h-3 bg-gray-50 rounded overflow-hidden">
                  <div className="h-full bg-blue-400 rounded" style={{ width: `${barPct}%` }} />
                </div>
                <span className="text-[10px] text-gray-500 w-12">晋级 {r.today}</span>
              </div>
              <span className={`font-mono font-semibold w-12 text-right ${rateColor}`}>{r.rate}%</span>
              <span className="text-[10px] text-gray-500 w-32 truncate" title={r.note}>{r.note}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 五类结构 (按口径分组)
function FiveClassView({ groups }: { groups: FiveClassGroup[] }) {
  const calibers = ['申万一级', '申万二级', '概念口径'];
  return (
    <div className="space-y-3">
      {calibers.map(cal => {
        const themes = groups.filter(g => g.caliber === cal);
        if (!themes.length) return null;
        return (
          <div key={cal} className="bg-white border border-gray-100 rounded-xl p-4">
            <div className="text-xs font-semibold text-gray-700 mb-3">🎭 五类结构 · {cal} ({themes.length} 题材)</div>
            <div className="space-y-3">
              {themes.map((g, i) => (
                <div key={i} className="rounded-lg border border-gray-100 p-2.5">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-xs font-semibold text-gray-700">{g.theme}</span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-5 gap-1.5 text-[10px]">
                    {g.members.map((m, j) => {
                      const roleColors: Record<string, string> = {
                        '龙头': 'bg-red-50 text-red-700 border-red-100',
                        '人气股': 'bg-orange-50 text-orange-700 border-orange-100',
                        '中军': 'bg-blue-50 text-blue-700 border-blue-100',
                        '先锋': 'bg-purple-50 text-purple-700 border-purple-100',
                        '杂毛': 'bg-gray-50 text-gray-600 border-gray-100',
                      };
                      return (
                        <div key={j} className={`rounded border px-1.5 py-1 ${roleColors[m.role] || ''}`}>
                          <div className="text-[9px] opacity-70">{m.role}</div>
                          <div className="font-semibold truncate" title={m.name}>{m.name}</div>
                          <div className="font-mono text-[9px] opacity-70">{m.code}</div>
                          {m.note && <div className="text-[9px] opacity-60 truncate" title={m.note}>{m.note}</div>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 观察池 (5 池)
function PoolView({ pools }: { pools: PoolStock[] }) {
  const poolOrder = ['容量核心', '情绪核心', '人气股', '补涨候选', '低吸候选'];
  const poolColors: Record<string, string> = {
    '容量核心': 'bg-blue-50 text-blue-700 border-blue-200',
    '情绪核心': 'bg-red-50 text-red-700 border-red-200',
    '人气股':   'bg-orange-50 text-orange-700 border-orange-200',
    '补涨候选': 'bg-purple-50 text-purple-700 border-purple-200',
    '低吸候选': 'bg-green-50 text-green-700 border-green-200',
  };
  return (
    <div className="space-y-3">
      {poolOrder.map(pool => {
        const list = pools.filter(p => p.pool === pool);
        if (!list.length) return null;
        return (
          <div key={pool} className={`rounded-xl border p-3 ${poolColors[pool]}`}>
            <div className="text-xs font-semibold mb-2 flex items-center gap-2">
              <span>{pool}</span>
              <span className="text-[10px] opacity-70">({list.length} 只)</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
              {list.map((s, i) => (
                <div key={i} className="bg-white/70 rounded p-2 text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="font-mono opacity-60">[{s.theme}]</span>
                    <span className="font-mono opacity-60">{s.code}</span>
                  </div>
                  <div className="font-semibold text-gray-800">{s.name}</div>
                  <div className="opacity-70 truncate" title={s.reason}>{s.reason}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ThemeLadderView({ md }: { md: string }) {
  const p = useMemo(() => parseThemeLadder(md), [md]);
  if (!p) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }
  return (
    <div className="space-y-4">
      {/* 区域 1: 4 数字卡 */}
      <SentSummaryCards s={p.summary} hasMainLine={p.hasMainLine} />
      {/* 区域 2: 阶段判定 */}
      {p.stage && (
        <div className="bg-white border border-gray-100 rounded-xl p-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold text-gray-700">🎲 阶段判定</span>
            <span className="text-[10px] px-2 py-0.5 bg-indigo-50 text-indigo-700 border border-indigo-100 rounded">
              {p.stage}
            </span>
          </div>
          <div className="text-[10px] text-gray-600 leading-relaxed">{p.stageNote}</div>
        </div>
      )}
      {/* 区域 3: 主线判定 */}
      {p.mainLines.length > 0 && <MainLineTable rows={p.mainLines} hasMainLine={p.hasMainLine} />}
      {/* 区域 4: 梯队金字塔 */}
      {p.pyramid.length > 0 && <PyramidTable rows={p.pyramid} />}
      {/* 区域 5: 晋级率 */}
      {p.promotion.length > 0 && <PromotionTable rows={p.promotion} />}
      {/* 区域 6: 五类结构 (3 口径) */}
      {p.fiveClass.length > 0 && <FiveClassView groups={p.fiveClass} />}
      {/* 区域 7: 观察池 (5 池) */}
      {p.pools.length > 0 && <PoolView pools={p.pools} />}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// 行业增强 (industry_enhanced) 解析 + 6 区域图形化
// ════════════════════════════════════════════════════════════════
interface TopSnapshotRow {
  rank: number; name: string; upPct: number; zt: number; bigMeat: number;
  aboveMA10: number; aboveMA20: number; avgChange: number;
}
interface Bias20Row {
  rank: number; name: string; bias20Pct: number; d5Mean: number; d20Mean: number;
  trend: string; persistDays: number;
}
interface HeatTrendRow {
  name: string; values: number[]; direction: string;
}
interface HuddleRow {
  name: string; level: string; huddleCount: string;
  aboveMA10: number; aboveMA20: number; bigMeat: number; zt: number; score: number;
}
interface PersistenceRow {
  window: string; corr: number; top10Retention: string; note: string;
}
interface RankChange {
  direction: 'up' | 'down'; name: string; change: number;
}
interface ParsedIndustryEnhanced {
  top10: TopSnapshotRow[];
  bias20: Bias20Row[];
  accelUp: { name: string; pp: number }[];
  accelDown: { name: string; pp: number }[];
  heatTrend: HeatTrendRow[];
  huddle: HuddleRow[];
  cyclePeriod: string;
  huddleCount: string;
  histHuddleTop: { name: string; pct: number }[];
  persistence: PersistenceRow[];
  rankUp: RankChange[];
  rankDown: RankChange[];
}

function parseTableRow(line: string): string[] {
  return line.split('|').map(s => s.trim()).filter(s => s.length > 0);
}

function parseIndustryEnhanced(md: string): ParsedIndustryEnhanced | null {
  // 找 "### 今日行业综合快照 Top 10" 表
  const top10Match = md.match(/### 今日行业综合快照 Top 10\s*\n\s*\n([\s\S]*?)(?=\n###|\n\n###|$)/);
  const top10: TopSnapshotRow[] = [];
  if (top10Match) {
    const lines = top10Match[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 8) {
        const upMatch = c[2].match(/(\d+)/);
        const ztMatch = c[3].match(/(\d+)/);
        const meatMatch = c[4].match(/(\d+)/);
        const ma10Match = c[5].match(/(\d+)/);
        const ma20Match = c[6].match(/(\d+)/);
        const avgMatch = c[7].match(/([+\-]?[\d.]+)/);
        top10.push({
          rank: parseInt(c[0]),
          name: c[1],
          upPct: upMatch ? parseInt(upMatch[1]) : 0,
          zt: ztMatch ? parseInt(ztMatch[1]) : 0,
          bigMeat: meatMatch ? parseInt(meatMatch[1]) : 0,
          aboveMA10: ma10Match ? parseInt(ma10Match[1]) : 0,
          aboveMA20: ma20Match ? parseInt(ma20Match[1]) : 0,
          avgChange: avgMatch ? parseFloat(avgMatch[1]) : 0,
        });
      }
    }
  }
  // ── 31 行业 BIAS20 ──
  const biasMatch = md.match(/### 31行业BIAS20热度排名[\s\S]*?\n([\s\S]*?)(?=\n###|\n\*\*热度)/);
  const bias20: Bias20Row[] = [];
  if (biasMatch) {
    const lines = biasMatch[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 7) {
        bias20.push({
          rank: parseInt(c[0]),
          name: c[1],
          bias20Pct: parseFloat(c[2]),
          d5Mean: parseFloat(c[3]),
          d20Mean: parseFloat(c[4]),
          trend: c[5],
          persistDays: parseInt(c[6]),
        });
      }
    }
  }
  // ── 热度加速变化 (文字段) ── 格式: "电子(变化+28.5pp)" ──
  const accelUp: { name: string; pp: number }[] = [];
  const accelDown: { name: string; pp: number }[] = [];
  for (const m of md.matchAll(/\*\*热度加速上升\*\*:\s*([^\n]+)/g)) {
    for (const piece of m[1].split(',')) {
      const pm = piece.match(/(\S+)\(变化([+\-]?[\d.]+)pp\)/);
      if (pm) accelUp.push({ name: pm[1], pp: parseFloat(pm[2]) });
    }
  }
  for (const m of md.matchAll(/\*\*热度加速下滑\*\*:\s*([^\n]+)/g)) {
    for (const piece of m[1].split(',')) {
      const pm = piece.match(/(\S+)\(变化([+\-]?[\d.]+)pp\)/);
      if (pm) accelDown.push({ name: pm[1], pp: parseFloat(pm[2]) });
    }
  }
  // ── BIAS20 趋势 (近10日) ──
  const trendMatch = md.match(/### BIAS20热度趋势[\s\S]*?\n([\s\S]*?)(?=\*Top 8|\n###|$)/);
  const heatTrend: HeatTrendRow[] = [];
  if (trendMatch) {
    const lines = trendMatch[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 12) {
        const values: number[] = [];
        for (let j = 1; j <= 10; j++) {
          const vm = c[j].match(/(\d+)/);
          values.push(vm ? parseInt(vm[1]) : 0);
        }
        heatTrend.push({ name: c[0], values, direction: c[11] });
      }
    }
  }
  // ── 抱团检测 ──
  const huddleMatch = md.match(/### 行业抱团检测[\s\S]*?\n([\s\S]*?)(?=\n###|\n\*\*当前周期|$)/);
  const huddle: HuddleRow[] = [];
  if (huddleMatch) {
    const lines = huddleMatch[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 8) {
        const ma10M = c[3].match(/(\d+)/);
        const ma20M = c[4].match(/(\d+)/);
        const meatM = c[5].match(/(\d+)/);
        const ztM = c[6].match(/(\d+)/);
        const scoreM = c[7].match(/([\d.]+)/);
        huddle.push({
          name: c[1], level: c[0], huddleCount: c[2],
          aboveMA10: ma10M ? parseInt(ma10M[1]) : 0,
          aboveMA20: ma20M ? parseInt(ma20M[1]) : 0,
          bigMeat: meatM ? parseInt(meatM[1]) : 0,
          zt: ztM ? parseInt(ztM[1]) : 0,
          score: scoreM ? parseFloat(scoreM[1]) : 0,
        });
      }
    }
  }
  // 当前周期 + 抱团行业数 + 历史抱团 Top 5
  const cycleM = md.match(/\*\*当前周期\*\*:\s*([^\n]+)/);
  const countM = md.match(/\*\*抱团行业数\*\*:\s*([^\n]+)/);
  const histHuddleTop: { name: string; pct: number }[] = [];
  const histM = md.match(/\*\*历史抱团频率 Top 5\*\*:\s*([^\n]+)/);
  if (histM) {
    for (const piece of histM[1].split(',')) {
      const pm = piece.match(/(\S+)\((\d+)%\)/);
      if (pm) histHuddleTop.push({ name: pm[1], pct: parseInt(pm[2]) });
    }
  }
  // ── 多周期持续性 ──
  const persistMatch = md.match(/### 多周期持续性分析\s*\n\s*\n([\s\S]*?)(?=\n###|\n\*\*排名大幅|$)/);
  const persistence: PersistenceRow[] = [];
  if (persistMatch) {
    const lines = persistMatch[1].split('\n').filter(l => l.trim().startsWith('|') && !l.includes('---'));
    for (let i = 1; i < lines.length; i++) {
      const c = parseTableRow(lines[i]);
      if (c.length >= 4) {
        const corrM = c[1].match(/([+\-]?[\d.]+)/);
        persistence.push({
          window: c[0],
          corr: corrM ? parseFloat(corrM[1]) : 0,
          top10Retention: c[2],
          note: c[3],
        });
      }
    }
  }
  // 排名变化 (markdown 格式: "- ↑ 跃升: 国防军工(+26位), 有色金属(+26位), ...")
  const rankUp: RankChange[] = [];
  const rankDown: RankChange[] = [];
  const upM = md.match(/↑ 跃升:\s*([^\n]+)/);
  const downM = md.match(/↓ 下滑:\s*([^\n]+)/);
  if (upM) {
    for (const piece of upM[1].split(',')) {
      const pm = piece.match(/(\S+)\(\+(\d+)位\)/);
      if (pm) rankUp.push({ direction: 'up', name: pm[1], change: parseInt(pm[2]) });
    }
  }
  if (downM) {
    for (const piece of downM[1].split(',')) {
      const pm = piece.match(/(\S+)\(-(\d+)位\)/);
      if (pm) rankDown.push({ direction: 'down', name: pm[1], change: parseInt(pm[2]) });
    }
  }
  if (!top10.length && !bias20.length && !huddle.length) return null;
  return {
    top10, bias20, accelUp, accelDown, heatTrend, huddle,
    cyclePeriod: cycleM ? cycleM[1].trim() : '',
    huddleCount: countM ? countM[1].trim() : '',
    histHuddleTop, persistence, rankUp, rankDown,
  };
}

function sparkPath(values: number[], width: number, height: number): string {
  if (!values.length) return '';
  const max = 100, min = 0;
  const stepX = width / (values.length - 1);
  return values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / (max - min)) * height;
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
}

function IndustryEnhancedView({ md }: { md: string }) {
  const p = useMemo(() => parseIndustryEnhanced(md), [md]);
  if (!p) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }
  return (
    <div className="space-y-4">
      {/* 区域 1: Top 10 综合快照 */}
      {p.top10.length > 0 && <EnhancedTop10 rows={p.top10} />}
      {/* 区域 2: 31 行业 BIAS20 + 加速变化 */}
      {p.bias20.length > 0 && (
        <EnhancedBias20
          rows={p.bias20} accelUp={p.accelUp} accelDown={p.accelDown}
        />
      )}
      {/* 区域 3: BIAS20 热度趋势 (sparkline) */}
      {p.heatTrend.length > 0 && <EnhancedHeatTrend rows={p.heatTrend} />}
      {/* 区域 4: 抱团检测 */}
      {p.huddle.length > 0 && (
        <EnhancedHuddle
          rows={p.huddle}
          cyclePeriod={p.cyclePeriod}
          huddleCount={p.huddleCount}
          histTop={p.histHuddleTop}
        />
      )}
      {/* 区域 5: 持续性分析 + 排名变化 */}
      <EnhancedPersistence
        rows={p.persistence}
        rankUp={p.rankUp}
        rankDown={p.rankDown}
      />
    </div>
  );
}

// 区域 1: Top 10 综合快照
function EnhancedTop10({ rows }: { rows: TopSnapshotRow[] }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">
        🏆 今日行业综合快照 Top 10
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        {rows.map(r => (
          <div key={r.name} className="rounded-lg border border-gray-100 p-2 hover:shadow-sm transition">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-gray-400 font-mono">#{r.rank}</span>
              <span className={`text-[10px] font-mono font-semibold ${r.avgChange >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                {r.avgChange >= 0 ? '+' : ''}{r.avgChange.toFixed(1)}%
              </span>
            </div>
            <div className="text-xs font-semibold text-gray-700 truncate" title={r.name}>{r.name}</div>
            <div className="text-[10px] text-gray-500 mt-1 space-y-0.5">
              <div className="flex justify-between">
                <span>上涨占比</span>
                <span className={`font-mono ${r.upPct >= 80 ? 'text-red-600' : 'text-gray-700'}`}>{r.upPct}%</span>
              </div>
              <div className="flex justify-between">
                <span>MA10/20</span>
                <span className="font-mono">{r.aboveMA10}/{r.aboveMA20}</span>
              </div>
              <div className="flex justify-between">
                <span>涨停/大肉</span>
                <span className="font-mono">{r.zt}/{r.bigMeat}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// 区域 2: 31 行业 BIAS20 + 加速变化
function EnhancedBias20({ rows, accelUp, accelDown }: {
  rows: Bias20Row[];
  accelUp: { name: string; pp: number }[];
  accelDown: { name: string; pp: number }[];
}) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">
        📊 31 行业 BIAS20 热度排名 (中期趋势)
      </div>
      <div className="space-y-1 mb-3 max-h-72 overflow-y-auto">
        {rows.map(r => (
          <div key={r.name} className="flex items-center gap-2 text-xs">
            <span className="w-6 text-right text-gray-400 font-mono text-[10px]">{r.rank}</span>
            <span className="w-20 truncate text-gray-700" title={r.name}>{r.name}</span>
            <div className="flex-1 h-3 bg-gray-50 rounded overflow-hidden">
              <div
                className={`h-full rounded ${
                  r.bias20Pct >= 90 ? 'bg-red-500' :
                  r.bias20Pct >= 70 ? 'bg-red-400' :
                  r.bias20Pct >= 50 ? 'bg-orange-400' :
                  r.bias20Pct >= 30 ? 'bg-yellow-400' : 'bg-gray-300'
                }`}
                style={{ width: `${r.bias20Pct}%` }}
              />
            </div>
            <span className="w-12 text-right font-mono text-[10px] text-gray-600">{r.bias20Pct.toFixed(0)}%</span>
            <span className={`w-12 text-right font-mono text-[10px] ${
              r.trend.includes('加速') ? 'text-red-600 font-semibold' :
              r.trend.includes('减速') ? 'text-green-600 font-semibold' : 'text-gray-600'
            }`}>{r.trend}</span>
            <span className="w-12 text-right font-mono text-[10px] text-gray-500">{r.persistDays}天</span>
          </div>
        ))}
      </div>
      {/* 加速变化 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-3 border-t border-gray-50">
        <div>
          <div className="text-[10px] text-red-600 font-semibold mb-1.5">↑ 热度加速上升</div>
          <div className="space-y-0.5">
            {accelUp.map((x, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-gray-700">{x.name}</span>
                <span className="font-mono text-red-600">+{x.pp.toFixed(1)}pp</span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-green-600 font-semibold mb-1.5">↓ 热度加速下滑</div>
          <div className="space-y-0.5">
            {accelDown.map((x, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-gray-700">{x.name}</span>
                <span className="font-mono text-green-600">{x.pp.toFixed(1)}pp</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// 区域 3: BIAS20 趋势 sparkline
function EnhancedHeatTrend({ rows }: { rows: HeatTrendRow[] }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">
        📈 BIAS20 热度趋势 (近 10 日 · Top 8 + Bottom 8)
      </div>
      <div className="space-y-1.5">
        {rows.map(r => {
          const last = r.values[r.values.length - 1] || 0;
          const first = r.values[0] || 0;
          const up = last > first;
          const lineColor = up ? '#ef4444' : '#22c55e';
          return (
            <div key={r.name} className="flex items-center gap-2 text-xs">
              <span className="w-20 truncate text-gray-700" title={r.name}>{r.name}</span>
              <svg width="160" height="20" className="flex-shrink-0">
                <path d={sparkPath(r.values, 160, 20)} stroke={lineColor} strokeWidth="1.5" fill="none" />
                <circle cx={160} cy={20 - (last / 100) * 20} r="2" fill={lineColor} />
              </svg>
              <div className="flex gap-0.5 text-[9px] font-mono text-gray-400 flex-1">
                {r.values.map((v, i) => (
                  <span key={i} className={v >= 50 ? 'text-red-500' : 'text-gray-500'}>{v}</span>
                ))}
              </div>
              <span className={`w-8 text-right font-mono text-[11px] ${
                r.direction === '↑' ? 'text-red-600' : 'text-green-600'
              }`}>{r.direction}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 区域 4: 抱团检测
function EnhancedHuddle({ rows, cyclePeriod, huddleCount, histTop }: {
  rows: HuddleRow[];
  cyclePeriod: string;
  huddleCount: string;
  histTop: { name: string; pct: number }[];
}) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-xs text-gray-500 font-semibold text-gray-700">
          🔥 行业抱团检测 (5 日周期 · 多维度评分)
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-100 rounded">
            周期: {cyclePeriod}
          </span>
          <span className="px-2 py-0.5 bg-orange-50 text-orange-700 border border-orange-100 rounded">
            抱团: {huddleCount}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-100">
              <th className="text-left py-1.5 pr-2 font-medium">行业</th>
              <th className="text-left py-1.5 pr-2 font-medium">级别</th>
              <th className="text-right py-1.5 px-1 font-medium">抱团</th>
              <th className="text-right py-1.5 px-1 font-medium">MA10</th>
              <th className="text-right py-1.5 px-1 font-medium">MA20</th>
              <th className="text-right py-1.5 px-1 font-medium">大肉</th>
              <th className="text-right py-1.5 px-1 font-medium">涨停</th>
              <th className="text-right py-1.5 pl-1 font-medium">评分</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const scoreBg = r.score >= 3.5 ? 'bg-red-100 text-red-700' :
                              r.score >= 3.0 ? 'bg-orange-100 text-orange-700' :
                              r.score >= 2.5 ? 'bg-yellow-50 text-yellow-700' : 'bg-gray-50 text-gray-600';
              return (
                <tr key={r.name} className="border-b border-gray-50 hover:bg-gray-50/50">
                  <td className="py-1.5 pr-2 font-medium text-gray-700 whitespace-nowrap">{r.name}</td>
                  <td className="py-1.5 pr-2 text-[10px] whitespace-nowrap">{r.level}</td>
                  <td className="py-1.5 px-1 text-right font-mono text-gray-600">{r.huddleCount}</td>
                  <td className="py-1.5 px-1 text-right font-mono text-gray-600">{r.aboveMA10}%</td>
                  <td className="py-1.5 px-1 text-right font-mono text-gray-600">{r.aboveMA20}%</td>
                  <td className="py-1.5 px-1 text-right font-mono text-gray-600">{r.bigMeat}家</td>
                  <td className="py-1.5 px-1 text-right font-mono text-gray-600">{r.zt}家</td>
                  <td className={`py-1.5 pl-1 text-right font-mono font-semibold rounded ${scoreBg}`}>
                    {r.score.toFixed(1)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {histTop.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-50">
          <div className="text-[10px] text-gray-500 font-semibold mb-1.5">📜 历史抱团频率 Top 5</div>
          <div className="flex flex-wrap gap-2">
            {histTop.map((h, i) => (
              <span key={i} className="text-[10px] px-2 py-0.5 bg-orange-50 text-orange-700 border border-orange-100 rounded">
                {h.name} {h.pct}%
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// 区域 5: 持续性 + 排名变化
function EnhancedPersistence({ rows, rankUp, rankDown }: {
  rows: PersistenceRow[];
  rankUp: RankChange[];
  rankDown: RankChange[];
}) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">
        🔁 多周期持续性 + 排名变化 (5 日)
      </div>
      {rows.length > 0 && (
        <table className="w-full text-xs mb-3">
          <thead>
            <tr className="text-gray-500 border-b border-gray-100">
              <th className="text-left py-1.5 pr-2 font-medium">窗口</th>
              <th className="text-right py-1.5 px-1 font-medium">Spearman 相关性</th>
              <th className="text-right py-1.5 px-1 font-medium">Top10 留存率</th>
              <th className="text-left py-1.5 pl-2 font-medium">说明</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.window} className="border-b border-gray-50">
                <td className="py-1.5 pr-2 font-medium text-gray-700">{r.window}</td>
                <td className={`py-1.5 px-1 text-right font-mono ${r.corr >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {r.corr >= 0 ? '+' : ''}{r.corr.toFixed(3)}
                </td>
                <td className="py-1.5 px-1 text-right font-mono text-gray-700">{r.top10Retention}</td>
                <td className="py-1.5 pl-2 text-gray-600">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-3 border-t border-gray-50">
        <div>
          <div className="text-[10px] text-red-600 font-semibold mb-1.5">↑ 跃升行业 ({rankUp.length})</div>
          <div className="space-y-0.5">
            {rankUp.map((r, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-gray-700">{r.name}</span>
                <span className="font-mono text-red-600">+{r.change}位</span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-green-600 font-semibold mb-1.5">↓ 下滑行业 ({rankDown.length})</div>
          <div className="space-y-0.5">
            {rankDown.map((r, i) => (
              <div key={i} className="flex justify-between text-xs">
                <span className="text-gray-700">{r.name}</span>
                <span className="font-mono text-green-600">-{r.change}位</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// 市场状态 (market_regime) 解析 + 6 区域图形化
// ════════════════════════════════════════════════════════════════
interface IndexRow {
  name: string;
  close: number;
  day: number;
  d5: number;
  d10: number;
  d20: number;
  d60: number;
  ytd: number;
  vol: number;
  ma5: '↑' | '↓';
  ma10: '↑' | '↓';
  ma20: '↑' | '↓';
  ma60: '↑' | '↓';
  ma120: '↑' | '↓';
}
interface StyleCompare {
  label: string;     // e.g. "大盘vs小盘"
  winner: string;    // e.g. "中证2000"
  loser: string;     // e.g. "沪深300"
  diff: number;      // e.g. 1.4
  winnerPct: number; // 近20日 winner 涨幅
  loserPct: number;
}
interface BreadthData {
  aboveMa10: number;
  aboveMa20: number;
  up5d: number;
  up20d: number;
  median5d: number;
  median20d: number;
  judgment: string;
}
interface IndustryRegime {
  name: string;
  strongPct: number;
  d5: number;
  d20: number;
  samples: number;
}
interface RegimeMeta {
  trend: string;       // e.g. "📈 温和上涨"
  volatility: string;  // e.g. "高波动"
  breadth: string;     // e.g. "强势"
  style: string;       // e.g. "周期主导"
  regimeTag: string;   // e.g. "高波震荡"
  confidence: string;  // e.g. "中"
  recommends: string[];
  cautions: string[];
  fallback: string;
}
interface ParsedRegime {
  indices: IndexRow[];
  styles: StyleCompare[];
  breadth: BreadthData;
  industries: IndustryRegime[];
  styleSummary: string;     // 周期/价值/成长
  meta: RegimeMeta;
  tradeDate: string;
}

function pctColor(v: number, bold = false): string {
  if (v > 0) return bold ? 'text-red-700 font-semibold' : 'text-red-600';
  if (v < 0) return bold ? 'text-green-700 font-semibold' : 'text-green-600';
  return 'text-gray-500';
}

function pctBg(v: number): string {
  if (v > 2) return 'bg-red-100';
  if (v > 0) return 'bg-red-50';
  if (v > -2) return 'bg-gray-50';
  return 'bg-green-50';
}

function parseMarketRegime(md: string): ParsedRegime | null {
  // ── 1. 指数表 ─────────────────────────────────────────
  // 找表头行 "MA5 MA10 MA20 MA60 MA120" → 下一行 "=====" → 数据行 → 下一段 "=====" 或空行
  const indices: IndexRow[] = [];
  const lines = md.split('\n');
  let headerLineIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (/MA5\s+MA10\s+MA20\s+MA60\s+MA120/.test(lines[i])) {
      headerLineIdx = i;
      break;
    }
  }
  if (headerLineIdx >= 0) {
    // 跳到表头下一行 (skip 表头和 "=====" 分隔)
    for (let i = headerLineIdx + 1; i < Math.min(headerLineIdx + 20, lines.length); i++) {
      const line = lines[i];
      // 数据行: 名字(中文) + 收盘价(数字) + 多个百分比
      // 提取所有数字字段和箭头
      const pctFields = line.matchAll(/(-?[\d.]+)%/g);
      const pcts = Array.from(pctFields).map(m => parseFloat(m[1]));
      const arrows = (line.match(/[↑↓]/g) || []);
      if (pcts.length >= 7 && arrows.length >= 5) {
        // pcts[0]=当日 d5[1] d10[2] d20[3] d60[4] ytd[5] vol[6]
        // 收盘价在 pcts[0] 之前
        const closeMatch = line.match(/^\s*(\S+(?:\s\S+)*?)\s{2,}(\d{3,5})\s/);
        if (closeMatch) {
          indices.push({
            name: closeMatch[1].trim(),
            close: parseFloat(closeMatch[2]),
            day: pcts[0],
            d5: pcts[1],
            d10: pcts[2],
            d20: pcts[3],
            d60: pcts[4],
            ytd: pcts[5],
            ma5: arrows[0] as '↑' | '↓',
            ma10: arrows[1] as '↑' | '↓',
            ma20: arrows[2] as '↑' | '↓',
            ma60: arrows[3] as '↑' | '↓',
            ma120: arrows[4] as '↑' | '↓',
            vol: pcts[6],
          });
        }
      }
    }
  }

  // ── 2. 风格对比 (3 行) ────────────────────────────────
  const styles: StyleCompare[] = [];
  const styleMatches = md.matchAll(/近20日\s+(\S+)\((-?[\d.]+)%\)\s+vs\s+(\S+)\((-?[\d.]+)%\),\s*差值(-?[\d.]+)%,\s*当前(\S+)占优/g);
  for (const m of styleMatches) {
    // m[1]/m[2] = 第一个指数名/涨幅, m[3]/m[4] = 第二个, m[5] = 差值, m[6] = 当前占优方
    const winner = m[6];
    const isFirstWinner = m[1] === winner;
    const winnerPct = parseFloat(isFirstWinner ? m[2] : m[4]);
    const loserPct = parseFloat(isFirstWinner ? m[4] : m[2]);
    const loser = isFirstWinner ? m[3] : m[1];
    const label = m[1].includes('2000') || m[3].includes('2000') ? '大盘vs小盘'
      : m[1].includes('500') || m[3].includes('500') ? '超大盘vs中盘'
      : '价值vs成长';
    styles.push({ label, winner, loser, diff: parseFloat(m[5]), winnerPct, loserPct });
  }

  // ── 3. 宽度分析 ───────────────────────────────────────
  let breadth: BreadthData = {
    aboveMa10: 0, aboveMa20: 0, up5d: 0, up20d: 0, median5d: 0, median20d: 0, judgment: ''
  };
  const ma10 = md.match(/股价\s*>\s*MA10\s*占比:\s*([\d.]+)%/);
  const ma20 = md.match(/股价\s*>\s*MA20\s*占比:\s*([\d.]+)%/);
  const up5 = md.match(/近5日上涨占比:\s*([\d.]+)%/);
  const up20 = md.match(/近20日上涨占比:\s*([\d.]+)%/);
  const med5 = md.match(/中位数5日收益率:\s*(-?[\d.]+)%/);
  const med20 = md.match(/中位数20日收益率:\s*(-?[\d.]+)%/);
  const bj = md.match(/宽度判断:\s*([^\n]+)/);
  if (ma10) breadth.aboveMa10 = parseFloat(ma10[1]);
  if (ma20) breadth.aboveMa20 = parseFloat(ma20[1]);
  if (up5) breadth.up5d = parseFloat(up5[1]);
  if (up20) breadth.up20d = parseFloat(up20[1]);
  if (med5) breadth.median5d = parseFloat(med5[1]);
  if (med20) breadth.median20d = parseFloat(med20[1]);
  if (bj) breadth.judgment = bj[1].trim();

  // ── 4. 行业轮动 ──────────────────────────────────────
  const industries: IndustryRegime[] = [];
  const indBlock = md.match(/行业轮动分析[\s\S]*?\n([\s\S]*?)(?=\n=+\s*$|周期\/价值|综合市场状态)/m);
  if (indBlock) {
    const indLines = indBlock[1].split('\n');
    for (const line of indLines) {
      // 行业名     强势占比  5日  20日  样本数 [bar]
      const m = line.match(/^\s*(\S+(?:\s\S+)*?)\s{2,}([\d.]+)%\s+(-?[\d.]+)%\s+(-?[\d.]+)%\s+(\d+)\s/);
      if (m) {
        industries.push({
          name: m[1].trim(),
          strongPct: parseFloat(m[2]),
          d5: parseFloat(m[3]),
          d20: parseFloat(m[4]),
          samples: parseInt(m[5]),
        });
      }
    }
  }

  // ── 5. 风格总结 (周期/价值/成长) ──────────────────────
  let styleSummary = '';
  const ssMatch = md.match(/当前风格:\s*([^\n]+)/);
  if (ssMatch) styleSummary = ssMatch[1].trim();

  // ── 6. 综合判断 + 策略 ────────────────────────────────
  const meta: RegimeMeta = {
    trend: '', volatility: '', breadth: '', style: '',
    regimeTag: '', confidence: '',
    recommends: [], cautions: [], fallback: '',
  };
  const trendM = md.match(/趋势:\s*([^\n]+)/);
  const volM = md.match(/波动率:\s*([^\n]+)/);
  const brM = md.match(/宽度:\s*([^\n]+)/);
  const stM = md.match(/风格:\s*([^\n]+)/);
  if (trendM) meta.trend = trendM[1].trim();
  if (volM) meta.volatility = volM[1].trim();
  if (brM) meta.breadth = brM[1].trim();
  if (stM) meta.style = stM[1].trim();
  const regM = md.match(/Regime标签:\s*([^\n]+)/);
  const confM = md.match(/置信度:\s*([^\n]+)/);
  if (regM) meta.regimeTag = regM[1].trim();
  if (confM) meta.confidence = confM[1].trim();
  // 推荐 + 谨慎
  for (const m of md.matchAll(/✅\s*推荐:\s*([^\n]+)/g)) {
    meta.recommends.push(m[1].trim());
  }
  for (const m of md.matchAll(/⚠️\s*谨慎:\s*([^\n]+)/g)) {
    meta.cautions.push(m[1].trim());
  }
  const fbM = md.match(/⚠️\s*如果判断错了:\s*([\s\S]+?)(?:\n=+\s*$|$)/m);
  if (fbM) meta.fallback = fbM[1].trim();

  // ── 7. 交易日 ────────────────────────────────────────
  const tdM = md.match(/A股市场状态分析\s*-\s*(\d{4}-\d{2}-\d{2})/);
  const tradeDate = tdM ? tdM[1] : '';

  if (!indices.length && !industries.length) return null;
  return { indices, styles, breadth, industries, styleSummary, meta, tradeDate };
}

// ─── 市场状态顶层容器 ─────────────────────────────────────────
function MarketRegimeView({ md }: { md: string }) {
  const p = useMemo(() => parseMarketRegime(md), [md]);
  if (!p) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }
  return (
    <div className="space-y-4">
      {p.tradeDate && (
        <div className="text-xs text-gray-400">交易日: <span className="font-mono">{p.tradeDate}</span></div>
      )}
      {/* 区域 1: 4 标签 + Regime 总览 + 置信度 */}
      <RegimeHeader meta={p.meta} />
      {/* 区域 2: 指数表 (12 行, 6 列 heatmap) */}
      <RegimeIndices rows={p.indices} />
      {/* 区域 3: 风格对比 (3 mini 卡) */}
      {p.styles.length > 0 && <RegimeStyles styles={p.styles} />}
      {/* 区域 4: 宽度分析 (5 数 + 横向条) */}
      <RegimeBreadth b={p.breadth} />
      {/* 区域 5: 行业轮动 (24 行业) */}
      {p.industries.length > 0 && (
        <RegimeIndustries rows={p.industries} styleSummary={p.styleSummary} />
      )}
      {/* 区域 6: 策略建议 */}
      <RegimeStrategy meta={p.meta} />
    </div>
  );
}

// ── 区域 1: 顶部 4 标签 + Regime ──────────────────────────────
function RegimeHeader({ meta }: { meta: RegimeMeta }) {
  const tagBg = (s: string) => {
    if (s.includes('强势') || s.includes('温和') || s.includes('占优')) return 'bg-red-50 text-red-700 border-red-200';
    if (s.includes('弱势') || s.includes('下跌') || s.includes('衰退')) return 'bg-green-50 text-green-700 border-green-200';
    return 'bg-gray-50 text-gray-600 border-gray-200';
  };
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <div className={`rounded-lg border p-2.5 ${tagBg(meta.trend)}`}>
          <div className="text-[10px] text-gray-500 mb-0.5">趋势</div>
          <div className="text-sm font-semibold">{meta.trend || '--'}</div>
        </div>
        <div className={`rounded-lg border p-2.5 ${tagBg(meta.volatility)}`}>
          <div className="text-[10px] text-gray-500 mb-0.5">波动率</div>
          <div className="text-sm font-semibold">{meta.volatility || '--'}</div>
        </div>
        <div className={`rounded-lg border p-2.5 ${tagBg(meta.breadth)}`}>
          <div className="text-[10px] text-gray-500 mb-0.5">宽度</div>
          <div className="text-sm font-semibold">{meta.breadth || '--'}</div>
        </div>
        <div className={`rounded-lg border p-2.5 ${tagBg(meta.style)}`}>
          <div className="text-[10px] text-gray-500 mb-0.5">风格</div>
          <div className="text-sm font-semibold">{meta.style || '--'}</div>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-gray-500">Regime:</span>
        <span className="px-2.5 py-1 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 text-blue-700 rounded-md text-sm font-bold">
          {meta.regimeTag || '--'}
        </span>
        <span className="text-[10px] text-gray-500 ml-2">置信度:</span>
        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
          meta.confidence === '高' ? 'bg-red-100 text-red-700' :
          meta.confidence === '中' ? 'bg-orange-100 text-orange-700' :
          meta.confidence === '低' ? 'bg-gray-100 text-gray-600' : 'bg-gray-50 text-gray-400'
        }`}>
          {meta.confidence || '--'}
        </span>
      </div>
    </div>
  );
}

// ── 区域 2: 指数表 ────────────────────────────────────────────
function RegimeIndices({ rows }: { rows: IndexRow[] }) {
  if (!rows.length) return null;
  const cols: { key: keyof IndexRow; label: string }[] = [
    { key: 'd5', label: '5日' },
    { key: 'd10', label: '10日' },
    { key: 'd20', label: '20日' },
    { key: 'd60', label: '60日' },
    { key: 'ytd', label: '年内' },
    { key: 'vol', label: '20日波' },
  ];
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 flex items-center gap-2">
        <span className="font-semibold text-gray-700">📊 12 大指数多周期</span>
        <span className="text-[10px] text-gray-400">MA5/10/20/60/120 方向 + 6 周期涨幅</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-100">
              <th className="text-left py-1.5 pr-2 font-medium">指数</th>
              <th className="text-right py-1.5 px-1.5 font-medium">收盘</th>
              {cols.map(c => (
                <th key={c.key} className="text-right py-1.5 px-1.5 font-medium">{c.label}</th>
              ))}
              <th className="text-right py-1.5 px-1.5 font-medium">MA</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.name} className="border-b border-gray-50 hover:bg-gray-50/50">
                <td className="py-1.5 pr-2 font-medium text-gray-700 whitespace-nowrap">{r.name}</td>
                <td className="py-1.5 px-1.5 text-right font-mono text-gray-600">{r.close}</td>
                {cols.map(c => {
                  const v = r[c.key] as number;
                  return (
                    <td key={c.key} className={`py-1.5 px-1.5 text-right font-mono ${pctBg(v)} ${pctColor(v)}`}>
                      {v > 0 ? '+' : ''}{v.toFixed(1)}%
                    </td>
                  );
                })}
                <td className="py-1.5 px-1.5 text-right font-mono text-[10px] space-x-0.5">
                  {[r.ma5, r.ma10, r.ma20, r.ma60, r.ma120].map((d, i) => (
                    <span key={i} className={d === '↑' ? 'text-red-500' : 'text-green-500'}>{d}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── 区域 3: 风格对比 ──────────────────────────────────────────
function RegimeStyles({ styles }: { styles: StyleCompare[] }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">⚔️ 风格对比 (近 20 日)</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {styles.map((s, i) => {
          const winnerUp = s.winnerPct > s.loserPct;
          return (
            <div key={i} className="rounded-lg border border-gray-100 p-3">
              <div className="text-[10px] text-gray-500 mb-2">{s.label}</div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-semibold ${winnerUp ? 'text-red-600' : 'text-green-600'}`}>
                    {s.winner}
                  </span>
                  <span className={`text-sm font-mono font-semibold ${pctColor(s.winnerPct, true)}`}>
                    {s.winnerPct > 0 ? '+' : ''}{s.winnerPct.toFixed(1)}%
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className={`text-xs ${!winnerUp ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}`}>
                    {s.loser}
                  </span>
                  <span className={`text-sm font-mono ${pctColor(s.loserPct)}`}>
                    {s.loserPct > 0 ? '+' : ''}{s.loserPct.toFixed(1)}%
                  </span>
                </div>
                <div className="border-t border-gray-100 pt-1.5 flex items-center justify-between text-[10px]">
                  <span className="text-gray-500">差值</span>
                  <span className={`font-mono font-semibold ${s.diff > 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {s.diff > 0 ? '+' : ''}{s.diff.toFixed(1)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 区域 4: 宽度分析 ──────────────────────────────────────────
function RegimeBreadth({ b }: { b: BreadthData }) {
  const items: { label: string; v: number; suffix: string; isPct: boolean }[] = [
    { label: '股价 > MA10', v: b.aboveMa10, suffix: '%', isPct: true },
    { label: '股价 > MA20', v: b.aboveMa20, suffix: '%', isPct: true },
    { label: '近 5 日上涨', v: b.up5d, suffix: '%', isPct: true },
    { label: '近 20 日上涨', v: b.up20d, suffix: '%', isPct: true },
    { label: '中位数 5 日', v: b.median5d, suffix: '%', isPct: true },
    { label: '中位数 20 日', v: b.median20d, suffix: '%', isPct: true },
  ];
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-gray-500 font-semibold text-gray-700">📏 市场宽度</div>
        {b.judgment && (
          <span className="text-[10px] px-2 py-0.5 bg-blue-50 text-blue-700 border border-blue-100 rounded">
            {b.judgment}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {items.map((it, i) => {
          const max = it.isPct ? 100 : 10;
          const pct = Math.min(Math.abs(it.v) / max * 100, 100);
          const positive = it.v > 0;
          return (
            <div key={i}>
              <div className="flex items-center justify-between text-[10px] text-gray-500 mb-1">
                <span>{it.label}</span>
                <span className={`font-mono font-semibold ${pctColor(it.v, true)}`}>
                  {it.v > 0 ? '+' : ''}{it.v.toFixed(1)}{it.suffix}
                </span>
              </div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${positive ? 'bg-red-400' : 'bg-green-400'}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 区域 5: 行业轮动 ──────────────────────────────────────────
function RegimeIndustries({ rows, styleSummary }: { rows: IndustryRegime[]; styleSummary: string }) {
  // 排序: 强势占比降序
  const sorted = [...rows].sort((a, b) => b.strongPct - a.strongPct);
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-xs font-semibold text-gray-700">🔄 行业轮动 ({rows.length} 行业 · 强势占比)</div>
        {styleSummary && (
          <span className="text-[10px] px-2 py-0.5 bg-orange-50 text-orange-700 border border-orange-100 rounded">
            {styleSummary}
          </span>
        )}
      </div>
      <div className="space-y-1.5">
        {sorted.map((r, i) => {
          const barPct = r.strongPct;
          return (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span className="w-24 truncate text-gray-700" title={r.name}>{r.name}</span>
              <div className="flex-1 h-3 bg-gray-50 rounded overflow-hidden relative">
                <div
                  className={`h-full rounded ${
                    r.strongPct >= 80 ? 'bg-red-400' :
                    r.strongPct >= 50 ? 'bg-orange-400' :
                    r.strongPct >= 20 ? 'bg-yellow-400' :
                    'bg-gray-300'
                  }`}
                  style={{ width: `${barPct}%` }}
                />
              </div>
              <span className="w-12 text-right font-mono text-gray-600 text-[11px]">{r.strongPct.toFixed(0)}%</span>
              <span className={`w-14 text-right font-mono text-[11px] ${pctColor(r.d5)}`}>
                {r.d5 > 0 ? '+' : ''}{r.d5.toFixed(1)}%
              </span>
              <span className={`w-14 text-right font-mono text-[11px] ${pctColor(r.d20)}`}>
                {r.d20 > 0 ? '+' : ''}{r.d20.toFixed(1)}%
              </span>
              <span className="w-10 text-right font-mono text-[10px] text-gray-400">{r.samples}</span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 text-[10px] text-gray-400 mt-2 pt-2 border-t border-gray-50">
        <span>← 强势占比%</span>
        <span>· 5日%</span>
        <span>· 20日%</span>
        <span className="ml-auto">· 样本数</span>
      </div>
    </div>
  );
}

// ── 区域 6: 策略建议 ──────────────────────────────────────────
function RegimeStrategy({ meta }: { meta: RegimeMeta }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-4">
      <div className="text-xs text-gray-500 mb-3 font-semibold text-gray-700">🎯 策略适配</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        {meta.recommends.length > 0 && (
          <div className="rounded-lg border border-red-100 bg-red-50/50 p-3">
            <div className="text-[10px] text-red-600 font-semibold mb-1.5">✅ 推荐策略</div>
            <ul className="space-y-1">
              {meta.recommends.map((r, i) => (
                <li key={i} className="text-xs text-red-800">• {r}</li>
              ))}
            </ul>
          </div>
        )}
        {meta.cautions.length > 0 && (
          <div className="rounded-lg border border-orange-100 bg-orange-50/50 p-3">
            <div className="text-[10px] text-orange-600 font-semibold mb-1.5">⚠️ 谨慎策略</div>
            <ul className="space-y-1">
              {meta.cautions.map((r, i) => (
                <li key={i} className="text-xs text-orange-800">• {r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {meta.fallback && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="text-[10px] text-gray-600 font-semibold mb-1.5">🔄 错判预案</div>
          <div className="text-xs text-gray-700 whitespace-pre-wrap leading-relaxed">{meta.fallback}</div>
        </div>
      )}
    </div>
  );
}

function IndustryCrowdingView({ md }: { md: string }) {
  const parsed = useMemo(() => parseIndustryCrowding(md), [md]);
  if (!parsed) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }

  // 行排序: 拥挤 → 中性 → 出清, 同区间按 3y 分位降序
  const sortKey = (r: IndustryRow) => {
    if (r.zone.includes('拥挤')) return 0;
    if (r.zone.includes('出清')) return 2;
    return 1;
  };
  const sortedRows = [...parsed.rows].sort((a, b) => {
    const sk = sortKey(a) - sortKey(b);
    if (sk !== 0) return sk;
    return b.y3 - a.y3;
  });
  const maxRatio5 = Math.max(1, ...parsed.rows.map(r => r.ratio5));

  return (
    <div className="space-y-3">
      {/* 顶部 4 个统计卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">🔴 拥挤区</div>
          <div className="text-xl font-bold text-red-600 mt-0.5">{parsed.crowded.length} 个</div>
        </div>
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">🟢 出清区</div>
          <div className="text-xl font-bold text-green-600 mt-0.5">{parsed.cleared.length} 个</div>
        </div>
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">全市场 20 日均</div>
          <div className="text-xl font-bold text-gray-900 mt-0.5">{parsed.amount20d?.toLocaleString() ?? '--'} <span className="text-xs text-gray-400">亿</span></div>
        </div>
        <div className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">全市场 60 日均</div>
          <div className="text-xl font-bold text-gray-900 mt-0.5">{parsed.amount60d?.toLocaleString() ?? '--'} <span className="text-xs text-gray-400">亿</span></div>
        </div>
      </div>

      {/* 拥挤区 + 出清区: 横向 bar */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ZoneList title="🔴 拥挤区 (3年分位≥80%)" color="red" items={parsed.crowded} />
        <ZoneList title="🟢 出清区 (3年分位≤20%)" color="green" items={parsed.cleared} />
      </div>

      {/* 主表 31 行业, 热力染色 */}
      <div className="bg-white border border-gray-100 rounded-xl overflow-hidden">
        <div className="text-xs font-semibold text-gray-700 px-3 py-2 bg-gray-50 border-b border-gray-100">
          31 行业明细 (按区间分组 + 3年分位排序)
        </div>
        <div className="overflow-x-auto">
          <div className="grid text-[11px] min-w-[760px]" style={{ gridTemplateColumns: '1.4fr 0.9fr 1.5fr 0.7fr 0.7fr 0.7fr 0.6fr 0.9fr 0.7fr 0.7fr 0.7fr' }}>
            {/* Header */}
            {['行业', '资产', '占比MA5(%)', '1年', '3年', '5年', '方向', '区间', '3年均', '3年高', '3年低'].map((h, i) => (
              <div key={i} className="px-2 py-1.5 text-center text-gray-500 font-medium bg-gray-50 border-b border-gray-100">{h}</div>
            ))}
            {/* Rows */}
            {sortedRows.map((r) => {
              const dirColor = r.dir === '↑' ? 'text-red-500' : r.dir === '↓' ? 'text-green-600' : 'text-gray-400';
              const zoneBg = r.zone.includes('拥挤') ? 'bg-red-50 text-red-700' : r.zone.includes('出清') ? 'bg-green-50 text-green-700' : 'bg-gray-50 text-gray-500';
              return (
                <>
                  <div className="px-2 py-1.5 text-gray-800 border-b border-gray-50 truncate">{r.name}</div>
                  <div className="px-2 py-1.5 text-gray-500 text-center border-b border-gray-50 truncate text-[10px]">{r.assetClass}</div>
                  <div className="px-2 py-1.5 border-b border-gray-50">
                    <div className="relative h-3.5 bg-gray-100 rounded">
                      <div className="absolute left-0 top-0 h-full bg-blue-400 rounded" style={{ width: `${(r.ratio5 / maxRatio5) * 100}%` }} />
                      <span className="relative z-10 px-1.5 text-[10px] font-mono text-gray-800 leading-none flex items-center h-full">{r.ratio5.toFixed(2)}</span>
                    </div>
                  </div>
                  <div className={`px-2 py-1.5 text-center font-mono border-b border-gray-50 ${percentileColor(r.y1)}`}>{r.y1.toFixed(1)}%</div>
                  <div className={`px-2 py-1.5 text-center font-mono border-b border-gray-50 font-semibold ${percentileColor(r.y3)}`}>{r.y3.toFixed(1)}%</div>
                  <div className={`px-2 py-1.5 text-center font-mono border-b border-gray-50 ${percentileColor(r.y5)}`}>{r.y5.toFixed(1)}%</div>
                  <div className={`px-2 py-1.5 text-center text-base border-b border-gray-50 ${dirColor}`}>{r.dir}</div>
                  <div className={`px-2 py-1.5 text-center text-[10px] border-b border-gray-50 ${zoneBg}`}>{r.zone}</div>
                  <div className="px-2 py-1.5 text-center text-gray-600 font-mono border-b border-gray-50">{r.y3Mean.toFixed(2)}</div>
                  <div className="px-2 py-1.5 text-center text-gray-600 font-mono border-b border-gray-50">{r.y3Max.toFixed(2)}</div>
                  <div className="px-2 py-1.5 text-center text-gray-600 font-mono border-b border-gray-50">{r.y3Min.toFixed(2)}</div>
                </>
              );
            })}
          </div>
        </div>
      </div>

      {/* 分资产类别解读 */}
      {parsed.assetRules.length > 0 && (
        <div className="bg-white border border-gray-100 rounded-xl overflow-hidden">
          <div className="text-xs font-semibold text-gray-700 px-3 py-2 bg-gray-50 border-b border-gray-100">
            广发三法则 · 分资产类别适用规律
          </div>
          <div className="divide-y divide-gray-50">
            {parsed.assetRules.map((r, i) => {
              const zoneBg = r.zone.includes('拥挤') ? 'bg-red-50 text-red-700' : r.zone.includes('出清') ? 'bg-green-50 text-green-700' : 'bg-gray-50 text-gray-500';
              return (
                <div key={i} className="grid grid-cols-12 gap-2 px-3 py-2 text-[11px] hover:bg-gray-50">
                  <span className="col-span-2 text-gray-800 font-medium">{r.name}</span>
                  <span className={`col-span-1 text-center text-[10px] px-1.5 py-0.5 rounded ${zoneBg}`}>{r.zone}</span>
                  <span className="col-span-2 text-gray-500">{r.assetClass}</span>
                  <span className="col-span-7 text-gray-700">{r.rule}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// 拥挤区 / 出清区横向 bar 列表
function ZoneList({ title, color, items }: { title: string; color: 'red' | 'green'; items: Array<{ name: string; y1: number; y3: number; y5: number }> }) {
  const isRed = color === 'red';
  const accent = isRed ? 'text-red-600' : 'text-green-600';
  const barBg = isRed ? 'bg-red-400' : 'bg-green-400';
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className={`text-xs font-semibold mb-2 ${accent}`}>{title}</div>
      {items.length === 0 ? (
        <div className="text-xs text-gray-400 text-center py-4">无</div>
      ) : (
        <div className="space-y-1.5">
          {items.map((it, i) => (
            <div key={i} className="grid grid-cols-12 gap-1.5 items-center text-[11px]">
              <span className="col-span-3 text-gray-800 truncate font-medium">{it.name}</span>
              <div className="col-span-7 flex gap-0.5 h-2.5">
                {[it.y1, it.y3, it.y5].map((p, j) => (
                  <div key={j} className="flex-1 relative bg-gray-100 rounded-sm overflow-hidden" title={['1年', '3年', '5年'][j] + ` ${p}%`}>
                    <div className={`absolute left-0 top-0 h-full ${barBg}`} style={{ width: `${p}%` }} />
                  </div>
                ))}
              </div>
              <span className="col-span-2 text-gray-500 font-mono text-[10px] text-right">{it.y3}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── 情绪周期专用渲染 (从 markdown 提取, 替代 raw <pre>) ─────
// 学习自 quant/dm_kun/sentiment_cycle_analyzer.py 的 stdout 输出格式
// 重点: 6 数字总览 / 连板天梯 / 烂板质量 / 周期阶段 / 情绪温度计 / 仓位评估
interface SentTop {
  zt: number; dt: number; touchZt: number; zb: number; zbRate: number; zdRatio: string;
}
interface SentTrendRow { date: string; zt: number; dt: number; zb: number; zdRatio: number; }
interface SentLianbanRow { tier: number; count: number; reps: string; }
interface SentTopStock { code: string; name: string; industry: string; tier: number; pct: number; }
interface SentIndustry { name: string; topTier: string; count: number; reps: string; }
interface SentQualityRow { label: string; emoji: string; count: number; pct: number; desc: string; isPositive: boolean; }
interface SentPhase { current: string; mode: string; tier: string; position: string; cap: string; rule: string; allPhases: Array<{ name: string; mode: string; tier: string; cap: string; rule: string; isCurrent: boolean; }>; }
interface SentTempRow { dim: string; score: number; tag: string; }
interface SentTempTotal { score: number; level: string; status: string; }
interface SentPosition { dim: string; emoji: string; weight: number; rawScore: number; weighted: number; key: string; }
interface SentPositionTotal { score: number; position: string; mood: string; }
interface ParsedSentiment {
  top: SentTop | null;
  trend: SentTrendRow[];
  lianban: SentLianbanRow[];
  topStocks: SentTopStock[];
  industries: SentIndustry[];
  quality: SentQualityRow[];
  qualityJudge: string;
  phase: SentPhase | null;
  tempRows: SentTempRow[];
  tempTotal: SentTempTotal | null;
  position: SentPosition[];
  positionTotal: SentPositionTotal | null;
}

function parseSentimentCycle(md: string): ParsedSentiment | null {
  // 1. 涨停生态总览 (6 数字)
  const top: SentTop | null = (() => {
    const zt = md.match(/涨停家数\s*\|\s*(\d+)\s*只/);
    const dt = md.match(/跌停家数\s*\|\s*(\d+)\s*只/);
    const touchZt = md.match(/触及涨停总数\s*\|\s*(\d+)\s*只/);
    const zb = md.match(/炸板数\s*\|\s*(\d+)\s*只/);
    const zbRate = md.match(/炸板率\s*\|\s*([\d.]+)%/);
    const zdRatio = md.match(/涨跌停比\s*\|\s*([\d.]+:\d+)/);
    if (!zt) return null;
    return {
      zt: +zt[1], dt: +(dt?.[1] || 0), touchZt: +(touchZt?.[1] || 0),
      zb: +(zb?.[1] || 0), zbRate: +(zbRate?.[1] || 0),
      zdRatio: zdRatio?.[1] || '0:0',
    };
  })();

  // 2. 5 日趋势 (找表头含"涨跌停比"的表)
  const trend: SentTrendRow[] = (() => {
    const lines = md.split('\n');
    let inTrend = false;
    const rows: SentTrendRow[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('日期') && l.includes('涨跌停比')) { inTrend = true; continue; }
      if (inTrend) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 5 || !cells[0].match(/\d{2}-\d{2}/)) continue;
        rows.push({ date: cells[0], zt: +cells[1] || 0, dt: +cells[2] || 0, zb: +cells[3] || 0, zdRatio: +cells[4] || 0 });
      }
    }
    return rows;
  })();

  // 3. 连板天梯 (5板/4板/3板/2板/1板)
  const lianban: SentLianbanRow[] = (() => {
    const lines = md.split('\n');
    let inLianban = false;
    const rows: SentLianbanRow[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('连板数') && l.includes('股票数')) { inLianban = true; continue; }
      if (inLianban) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 2) continue;
        const m = cells[0].match(/(\d+)板/);
        if (!m) continue;
        rows.push({ tier: +m[1], count: +cells[1].replace(/[^\d]/g, '') || 0, reps: cells[2] || '' });
      }
    }
    return rows;
  })();

  // 4. 最高连板个股
  const topStocks: SentTopStock[] = (() => {
    const lines = md.split('\n');
    let inStocks = false;
    const rows: SentTopStock[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('代码') && l.includes('连板数')) { inStocks = true; continue; }
      if (inStocks) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 5) continue;
        const code = cells[0]; if (!code.match(/^(sz|sh|bj)/)) continue;
        const tierM = cells[3].match(/(\d+)板/);
        rows.push({
          code, name: cells[1], industry: cells[2],
          tier: tierM ? +tierM[1] : 0,
          pct: parseFloat(cells[4]) || 0,
        });
      }
    }
    return rows;
  })();

  // 5. 连板行业 Top 8
  const industries: SentIndustry[] = (() => {
    const lines = md.split('\n');
    let inInd = false;
    const rows: SentIndustry[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('行业') && l.includes('最高连板') && !l.includes('代表')) { inInd = true; continue; }
      if (inInd) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 4) continue;
        if (cells[0] === '行业' || cells[0] === '---') continue;
        rows.push({ name: cells[0], topTier: cells[1] || '', count: parseInt(cells[2]) || 0, reps: cells[3] || '' });
      }
    }
    return rows;
  })();

  // 6. 烂板质量 (5 类)
  const quality: SentQualityRow[] = (() => {
    const lines = md.split('\n');
    let inQ = false;
    const rows: SentQualityRow[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('类型') && l.includes('数量') && l.includes('特征')) { inQ = true; continue; }
      if (inQ) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 4) continue;
        if (cells[0] === '类型' || cells[0] === '---') continue;
        const fullText = cells[0];
        const emojiM = fullText.match(/^([🔒⚡🕐🌊📄])\s*(.+)/);
        if (!emojiM) continue;
        const countM = cells[1].match(/(\d+)\s*只/);
        const pctM = cells[2].match(/([\d.]+)%/);
        rows.push({
          emoji: emojiM[1],
          label: emojiM[2].replace(/\*\*/g, '').trim(),
          count: countM ? +countM[1] : 0,
          pct: pctM ? +pctM[1] : 0,
          desc: cells[3] || '',
          isPositive: cells[0].includes('硬板') || cells[0].includes('分歧回封'),
        });
      }
    }
    return rows;
  })();

  // 6b. 质量判断
  const qj = md.match(/\*\*质量判断\*\*[：:]\s*([^\n]+)/);
  const qualityJudge = qj ? qj[1].trim() : '';

  // 7. 周期阶段判定 + 完整映射
  const phase: SentPhase | null = (() => {
    const lines = md.split('\n');
    // 找 "**当前阶段**" 行
    let currentLine = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes('当前阶段')) { currentLine = i; break; }
    }
    if (currentLine < 0) return null;
    // 找 "### 4.5 操作模式映射"
    const mapStart = lines.findIndex(l => l.includes('### 4.5'));
    if (mapStart < 0) return null;
    // 找 "完整映射表" 后的表头
    const headerIdx = lines.findIndex((l, i) => i > mapStart && l.includes('|') && l.includes('阶段') && l.includes('仓位档位') && l.includes('纪律'));
    if (headerIdx < 0) return null;
    const allPhases: SentPhase['allPhases'] = [];
    for (let i = headerIdx + 2; i < lines.length; i++) {
      const l = lines[i];
      if (!l.trim().startsWith('|')) break;
      const cells = l.split('|').slice(1, -1).map(s => s.trim());
      if (cells.length < 5) continue;
      const isCurrent = cells[0].includes('当前') || cells[0].includes('←');
      const cleanName = cells[0].replace(/当前|←/g, '').trim();
      allPhases.push({
        name: cleanName, mode: cells[1] || '', tier: cells[3] || '',
        cap: cells[4] || '', rule: cells[5] || '', isCurrent,
      });
    }
    // 当前阶段的 detail 从 ## 4 段 table 抓
    const detailStart = lines.findIndex(l => l.includes('### 4. 情绪周期阶段判定'));
    let mode = '', tier = '', position = '', cap = '', rule = '';
    if (detailStart > 0) {
      const seg = lines.slice(detailStart, mapStart).join('\n');
      const m = seg.match(/允许操作模式\s*\|\s*([^\n|]+)/);
      tier = seg.match(/允许板位\/介入方式\s*\|\s*([^\n|]+)/)?.[1]?.trim() || '';
      position = tier;
      cap = seg.match(/原仓位上限建议\s*\|\s*([^\n|]+)/)?.[1]?.trim() || '';
      rule = seg.match(/纪律\s*\|\s*"?([^"\n|]+)"?/)?.[1]?.trim() || '';
      mode = m?.[1]?.trim() || '';
    }
    // currentLine 抓名称
    const currM = lines[currentLine]?.match(/\*\*当前阶段\*\*\s*\|\s*([^\n|]+)/);
    const current = currM ? currM[1].trim().replace(/^.*?\*\*/, '').replace(/\*\*/g, '').trim() : '';
    return { current, mode, tier, position, cap, rule, allPhases };
  })();

  // 8. 情绪温度计 (6 维 + 总分)
  const tempRows: SentTempRow[] = (() => {
    const lines = md.split('\n');
    let inT = false;
    const rows: SentTempRow[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('维度') && l.includes('解读')) { inT = true; continue; }
      if (inT) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 3) continue;
        if (cells[0] === '维度' || cells[0] === '---') continue;
        rows.push({ dim: cells[0], score: +cells[1] || 0, tag: cells[2] || '' });
      }
    }
    return rows;
  })();

  const tempTotal: SentTempTotal | null = (() => {
    const m = md.match(/\*\*情绪温度\*\*[：:]\s*(\d+)\/100\s+([🟢🟠🔴⚪]+)/);
    const s = md.match(/\*\*状态\*\*[：:]\s*([^\n]+)/);
    if (!m) return null;
    return { score: +m[1], level: m[2], status: s?.[1]?.trim() || '' };
  })();

  // 9. 多维度仓位评估
  const position: SentPosition[] = (() => {
    const lines = md.split('\n');
    let inP = false;
    const rows: SentPosition[] = [];
    for (const l of lines) {
      if (l.includes('|') && l.includes('维度') && l.includes('权重') && l.includes('关键子指标')) { inP = true; continue; }
      if (inP) {
        if (!l.trim().startsWith('|')) break;
        const cells = l.split('|').slice(1, -1).map(s => s.trim());
        if (cells.length < 5) continue;
        if (cells[0] === '维度' || cells[0] === '---') continue;
        const dimM = cells[0].match(/^(\S+)\s+(.+)$/);
        rows.push({
          emoji: dimM?.[1] || '📊',
          dim: dimM?.[2] || cells[0],
          weight: parseFloat(cells[1]) || 0,
          rawScore: parseFloat(cells[2]) || 0,
          weighted: parseFloat(cells[3]) || 0,
          key: cells[4] || '',
        });
      }
    }
    return rows;
  })();

  const positionTotal: SentPositionTotal | null = (() => {
    const m = md.match(/\*\*加权总分\*\*\s*\|\s*\*?\*?(\d+)\/100\*?\*?/);
    const pos = md.match(/\*\*建议仓位\*\*\s*\|\s*\*?\*?(\d+)%/);
    const mood = md.match(/\(([🟢🟠🔴])\s*([^)]+)\)/);
    if (!m) return null;
    return { score: +m[1], position: pos?.[1] ? pos[1] + '%' : '', mood: mood?.[2] || '' };
  })();

  return {
    top, trend, lianban, topStocks, industries,
    quality, qualityJudge, phase,
    tempRows, tempTotal, position, positionTotal,
  };
}

// ─── 情绪周期渲染组件 ─────────────────────────────
function SentimentCycleView({ md }: { md: string }) {
  const p = useMemo(() => parseSentimentCycle(md), [md]);
  if (!p) {
    return <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">{md}</pre>;
  }

  return (
    <div className="space-y-3">
      {/* 区域 1: 6 数字卡片 */}
      {p.top && <SentTopCards top={p.top} />}

      {/* 区域 2: 5 日趋势柱状图 */}
      {p.trend.length > 0 && <SentTrendChart trend={p.trend} />}

      {/* 区域 3: 连板天梯 + 烂板质量 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {p.lianban.length > 0 && p.topStocks.length > 0 && (
          <SentLianbanPyramid tiers={p.lianban} topStocks={p.topStocks} />
        )}
        {p.quality.length > 0 && (
          <SentQualityChart quality={p.quality} judge={p.qualityJudge} />
        )}
      </div>

      {/* 区域 4: 周期阶段 + 完整映射 */}
      {p.phase && <SentPhaseTable phase={p.phase} />}

      {/* 区域 5: 情绪温度计 + 仓位评估 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {p.tempRows.length > 0 && <SentTempGauge rows={p.tempRows} total={p.tempTotal} />}
        {p.position.length > 0 && <SentPositionBars rows={p.position} total={p.positionTotal} />}
      </div>
    </div>
  );
}

// 6 数字卡片
function SentTopCards({ top }: { top: SentTop }) {
  const cards = [
    { label: '涨停家数', value: `${top.zt}`, unit: '只', color: 'red' },
    { label: '跌停家数', value: `${top.dt}`, unit: '只', color: 'green' },
    { label: '触及涨停', value: `${top.touchZt}`, unit: '只', color: 'amber' },
    { label: '炸板数', value: `${top.zb}`, unit: '只', color: 'orange' },
    { label: '炸板率', value: `${top.zbRate.toFixed(1)}`, unit: '%', color: top.zbRate >= 30 ? 'red' : top.zbRate >= 20 ? 'amber' : 'green' },
    { label: '涨跌停比', value: top.zdRatio, unit: '', color: 'blue' },
  ];
  const colorClass = (c: string) => c === 'red' ? 'text-red-600' : c === 'green' ? 'text-green-600' : c === 'amber' || c === 'orange' ? 'text-amber-600' : 'text-blue-600';
  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
      {cards.map(c => (
        <div key={c.label} className="kpi-card bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400">{c.label}</div>
          <div className={`text-2xl font-bold mt-0.5 ${colorClass(c.color)}`}>
            {c.value}
            {c.unit && <span className="text-xs text-gray-400 ml-1">{c.unit}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

// 5 日趋势柱状图 (涨停/跌停/炸板 3 组柱 + 涨跌停比折线)
function SentTrendChart({ trend }: { trend: SentTrendRow[] }) {
  if (trend.length === 0) return null;
  const maxVal = Math.max(...trend.map(r => Math.max(r.zt, r.dt, r.zb)), 1);
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-700 mb-2">近 {trend.length} 日涨停/跌停/炸板趋势</div>
      <div className="flex items-end gap-3 h-32">
        {trend.map((r, i) => (
          <div key={i} className="flex-1 flex flex-col items-center gap-1">
            <div className="flex items-end gap-1 h-full w-full justify-center">
              <div className="flex flex-col items-center" title={`涨停 ${r.zt}`}>
                <div className="text-[10px] text-red-600 font-mono">{r.zt}</div>
                <div className="w-5 bg-red-400 rounded-t" style={{ height: `${(r.zt / maxVal) * 100}%` }} />
              </div>
              <div className="flex flex-col items-center" title={`跌停 ${r.dt}`}>
                <div className="text-[10px] text-green-600 font-mono">{r.dt}</div>
                <div className="w-5 bg-green-400 rounded-t" style={{ height: `${(r.dt / maxVal) * 100}%` }} />
              </div>
              <div className="flex flex-col items-center" title={`炸板 ${r.zb}`}>
                <div className="text-[10px] text-orange-500 font-mono">{r.zb}</div>
                <div className="w-5 bg-orange-400 rounded-t" style={{ height: `${(r.zb / maxVal) * 100}%` }} />
              </div>
            </div>
            <div className="text-[10px] text-gray-500 font-mono">{r.date}</div>
            <div className="text-[9px] text-blue-500 font-mono">比 {r.zdRatio.toFixed(1)}</div>
          </div>
        ))}
      </div>
      <div className="flex justify-center gap-4 mt-2 text-[10px] text-gray-500">
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-400 rounded" />涨停</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-400 rounded" />跌停</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 bg-orange-400 rounded" />炸板</span>
      </div>
    </div>
  );
}

// 连板天梯金字塔 (5/4/3/2/1 板, 宽=股票数, 高=连板数)
function SentLianbanPyramid({ tiers, topStocks }: { tiers: SentLianbanRow[]; topStocks: SentTopStock[] }) {
  const maxCount = Math.max(...tiers.map(t => t.count), 1);
  const maxTier = Math.max(...tiers.map(t => t.tier), 1);
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-700 mb-2">🔺 连板天梯 · 最高 {maxTier} 板</div>
      <div className="space-y-1">
        {tiers.map(t => {
          const widthPct = (t.count / maxCount) * 100;
          const topStock = topStocks.find(s => s.tier === t.tier);
          return (
            <div key={t.tier} className="flex items-center gap-2">
              <div className="w-12 text-right">
                <span className="text-sm font-bold text-red-600">{t.tier}板</span>
              </div>
              <div className="flex-1 flex items-center gap-2">
                <div
                  className="h-7 rounded flex items-center px-2 text-white text-xs font-bold"
                  style={{ width: `${Math.max(widthPct, 8)}%`, background: `linear-gradient(90deg, #f87171 0%, #ef4444 100%)` }}
                >
                  {t.count > 0 && <span>{t.count} 只</span>}
                </div>
                {topStock && (
                  <span className="text-[10px] text-gray-500 truncate">→ {topStock.name} {topStock.industry}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 烂板质量 5 类 (横向 bar + 质量判断)
function SentQualityChart({ quality, judge }: { quality: SentQualityRow[]; judge: string }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-700 mb-2">🎯 涨停质量分布 ({quality.reduce((s, q) => s + q.count, 0)} 只)</div>
      {judge && <div className="text-[10px] text-gray-500 mb-2 italic">{judge}</div>}
      <div className="space-y-1.5">
        {quality.map((q, i) => {
          const barColor = q.isPositive
            ? (q.label.includes('硬板') ? 'bg-red-500' : 'bg-red-400')
            : (q.label.includes('普通') ? 'bg-gray-400' : 'bg-orange-400');
          return (
            <div key={i} className="grid grid-cols-12 gap-2 items-center text-[11px]">
              <span className="col-span-4 text-gray-700 truncate">
                <span className="mr-1">{q.emoji}</span>{q.label}
              </span>
              <div className="col-span-6 relative h-5 bg-gray-100 rounded overflow-hidden">
                <div className={`absolute left-0 top-0 h-full ${barColor} flex items-center px-2 text-white text-[10px] font-mono`}
                     style={{ width: `${Math.max(q.pct, 8)}%` }}>
                  {q.count} 只 · {q.pct.toFixed(1)}%
                </div>
              </div>
              <span className="col-span-2 text-[9px] text-gray-400 truncate">{q.desc.split(/[，。,]/)[0]}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 周期阶段判定 + 完整映射表
function SentPhaseTable({ phase }: { phase: SentPhase }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-700 mb-2">🌀 当前情绪周期阶段</div>
      <div className="bg-gradient-to-r from-amber-50 to-amber-100 border border-amber-200 rounded-lg p-3 mb-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-base font-bold text-amber-700">⚡ {phase.current || '未知'}</span>
          {phase.mode && <span className="text-[11px] text-gray-700">📍 {phase.mode}</span>}
          {phase.position && <span className="text-[11px] text-gray-700">🎯 {phase.position}</span>}
          {phase.cap && <span className="text-[11px] text-gray-700">💰 {phase.cap}</span>}
        </div>
        {phase.rule && <div className="text-[11px] text-gray-600 mt-1 italic">📜 {phase.rule}</div>}
      </div>
      <div className="text-[10px] font-medium text-gray-500 mb-1">完整映射 (高亮当前):</div>
      <div className="space-y-1">
        {phase.allPhases.map((p, i) => (
          <div key={i} className={`grid grid-cols-12 gap-2 px-2 py-1.5 rounded text-[10px] ${p.isCurrent ? 'bg-amber-100 border border-amber-300' : 'hover:bg-gray-50'}`}>
            <span className={`col-span-2 font-medium ${p.isCurrent ? 'text-amber-700' : 'text-gray-700'}`}>{p.name}{p.isCurrent && ' ←'}</span>
            <span className="col-span-3 text-gray-600 truncate">{p.mode}</span>
            <span className="col-span-2 text-gray-500">{p.tier}</span>
            <span className="col-span-2 text-gray-500">{p.cap}</span>
            <span className="col-span-3 text-gray-500 italic">{p.rule.split(/[，,]/)[0]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// 情绪温度计 (6 维横条 + 总分大字)
function SentTempGauge({ rows, total }: { rows: SentTempRow[]; total: SentTempTotal | null }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-gray-700">🌡️ 情绪温度计</div>
        {total && (
          <div className="text-right">
            <div className="text-2xl font-bold text-amber-600">{total.score}<span className="text-xs text-gray-400">/100</span></div>
            <div className="text-[10px] text-gray-500">{total.level} · {total.status}</div>
          </div>
        )}
      </div>
      <div className="space-y-1.5">
        {rows.map((r, i) => {
          const color = r.score >= 80 ? 'bg-red-500' : r.score >= 50 ? 'bg-amber-500' : r.score >= 30 ? 'bg-blue-500' : 'bg-gray-400';
          return (
            <div key={i} className="grid grid-cols-12 gap-2 items-center text-[11px]">
              <span className="col-span-3 text-gray-700 truncate">{r.dim}</span>
              <div className="col-span-7 relative h-4 bg-gray-100 rounded overflow-hidden">
                <div className={`absolute left-0 top-0 h-full ${color}`} style={{ width: `${r.score}%` }} />
                <span className="relative z-10 px-2 text-white text-[10px] font-mono leading-none flex items-center h-full" style={{ textShadow: '0 1px 1px rgba(0,0,0,0.5)' }}>{r.score}</span>
              </div>
              <span className="col-span-2 text-[9px] text-gray-500 truncate">{r.tag}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 仓位评估 5 维横条 + 总分 + 建议
function SentPositionBars({ rows, total }: { rows: SentPosition[]; total: SentPositionTotal | null }) {
  return (
    <div className="bg-white border border-gray-100 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-gray-700">💼 多维度仓位评估</div>
        {total && (
          <div className="text-right">
            <div className="text-2xl font-bold text-green-600">{total.score}<span className="text-xs text-gray-400">/100</span></div>
            <div className="text-[10px] text-gray-500">建议 {total.position} · {total.mood}</div>
          </div>
        )}
      </div>
      <div className="space-y-1.5">
        {rows.map((r, i) => {
          const color = r.rawScore >= 80 ? 'bg-green-500' : r.rawScore >= 50 ? 'bg-blue-500' : 'bg-amber-500';
          return (
            <div key={i} className="grid grid-cols-12 gap-2 items-center text-[11px]">
              <span className="col-span-3 text-gray-700 truncate">
                <span className="mr-1">{r.emoji}</span>{r.dim}
                <span className="text-[9px] text-gray-400 ml-1">w{r.weight}%</span>
              </span>
              <div className="col-span-6 relative h-4 bg-gray-100 rounded overflow-hidden">
                <div className={`absolute left-0 top-0 h-full ${color}`} style={{ width: `${r.rawScore}%` }} />
                <span className="relative z-10 px-2 text-white text-[10px] font-mono leading-none flex items-center h-full" style={{ textShadow: '0 1px 1px rgba(0,0,0,0.5)' }}>{r.rawScore} → {r.weighted}</span>
              </div>
              <span className="col-span-3 text-[9px] text-gray-500 truncate">{r.key.split(/[，,]/)[0]}</span>
            </div>
          );
        })}
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
        name === 'market-regime' ? (
          <MarketRegimeView md={cache.markdown} />
        ) : name === 'industry-crowding' ? (
          <IndustryCrowdingView md={cache.markdown} />
        ) : name === 'sentiment-cycle' ? (
          <SentimentCycleView md={cache.markdown} />
        ) : name === 'industry-enhanced' ? (
          <IndustryEnhancedView md={cache.markdown} />
        ) : name === 'theme-ladder' ? (
          <ThemeLadderView md={cache.markdown} />
        ) : name === 'stock-recommender' ? (
          <RecommenderView md={cache.markdown} />
        ) : (
          <pre className="bg-white border border-gray-100 rounded-xl p-4 text-xs font-mono whitespace-pre-wrap overflow-x-auto leading-relaxed">
            {cache.markdown}
          </pre>
        )
      ) : !loading ? (
        <div className="text-center text-gray-400 text-sm py-12">
          暂无数据, 点 🔄 重算 跑一次
        </div>
      ) : null}
    </div>
  );
}

// ─── 迷你水平 Gauge (学习自 MarketSentimentPage.AmountRatioGauge) ─────
// 跟市场数据页 AmountRatioGauge 一样: 0-2x 量程, 1.0 = 均值线 (50% 位置)
// 复用 .kpi-gauge-bar CSS 动画, 不画底部 0/均值/2x 标尺 (KPI 卡空间有限)
function MiniGauge({ ratio, color }: {
  ratio: number; color: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) {
      // CSS 公式: width = calc(min(pct, 200) / 200 * 100%)
      // ratio 0-2x → pct 0-200, ratio=1.0 → width=50% (均值线位置)
      ref.current.style.setProperty('--gauge-target', String(Math.min(Math.max(ratio, 0), 2) * 100));
    }
  }, [ratio]);
  return (
    <div className="relative h-1.5 bg-gray-100 rounded-full overflow-hidden mt-1.5" title={`${ratio.toFixed(2)}x`}>
      <div
        ref={ref}
        className="kpi-gauge-bar absolute left-0 top-0 h-full rounded-full"
        style={{ background: color }}
      />
      {/* 均值线: 50% 处 (即 ratio=1.0 时条形宽度的对应位置) */}
      <div className="absolute top-0 bottom-0 w-px bg-gray-400/60" style={{ left: '50%' }} />
    </div>
  );
}
