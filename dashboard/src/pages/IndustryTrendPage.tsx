import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import Plotly from 'plotly.js-basic-dist-min';
import { LayoutDashboard, ChartScatter, Table2, CandlestickChart, RefreshCw, CalendarDays, Grid3x3, TrendingUp } from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { IndustryKline } from '../components/IndustryKline';
import { apiFetch } from '../lib/api';

// ─── 类型 (payload 由 quant/industry_trend_daily.py 生成) ──────────────────

interface TrendRow { [k: string]: any; }
interface TrendChange {
  行业: string; 类型: string; 方向: 'up' | 'down';
  上期: any; 本期: any; 变化?: number; 说明: string;
}
interface TrendSummary {
  cat_counts: Record<string, number>;
  prev_cat_counts: Record<string, number>;
  upgrades: number; downgrades: number;
  resonance: string[];
  ff_worsen_top3: [string, number][];
  ff_improve_top3: [string, number][];
  day_alerts: [string, number][];
  headline: string;
}
interface TrendPayload {
  trade_date: string; prev_date: string | null;
  cols: string[]; summary: TrendSummary;
  changes: TrendChange[]; table: TrendRow[];
  _recompute?: boolean; _elapsed_sec?: number;
}
interface SeriesPoint { date: string; [k: string]: any; }

// ─── 常量 ─────────────────────────────────────────────────────────────────

const UP = '#d8392b';    // 涨/改善 红 (A股惯例)
const DOWN = '#1a9e5f';  // 跌/恶化 绿
const GRAY = '#888780';

const CAT_COLOR: Record<string, string> = {
  '强势多头': '#0F6E56', '多头': '#1D9E75', '多头回踩': '#5DCAA5',
  '筑底反转': '#534AB7', '筑底回踩': '#CE9B54',
  '震荡粘合': '#888780', '震荡': '#B4B2A9', '空头反弹': '#F0997B',
  '空头': '#D85A30', '强势空头': '#A32D2D', '数据不足': '#B4B2A9',
};
const CAT_ORDER = ['强势多头', '多头', '多头回踩', '筑底反转', '筑底回踩',
  '震荡粘合', '震荡', '空头反弹', '空头', '强势空头'];

// ─── 板块类别: 唯一来源 = 后端 quant/industry_trend_daily.py (31行业→6大类别) ──
// 前端不再硬编码映射: 页面挂载拉一次 /api/industry-trend/categories,
// 模块级 store + useSyncExternalStore 订阅, 各子页共享同一份数据。
type CatMeta = { map: Record<string, string>; order: string[] };
let _catMeta: CatMeta = { map: {}, order: [] };
const _catSubs = new Set<() => void>();
let _catFetchStarted = false;
function loadCatMeta() {
  if (_catFetchStarted) return;
  _catFetchStarted = true;
  apiFetch<CatMeta>('/api/industry-trend/categories')
    .then(d => { if (d?.map) { _catMeta = d; _catSubs.forEach(f => f()); } })
    .catch(() => { /* 拉不到映射时类别列显示 '--', 不阻塞页面 */ });
}
function useCatMeta(): CatMeta {
  useEffect(() => { loadCatMeta(); }, []);
  return useSyncExternalStore(
    cb => { _catSubs.add(cb); return () => { _catSubs.delete(cb); }; },
    () => _catMeta,
  );
}

// CAT_STYLE: 类别配色 (纯展示样式, 按类别名取色, 不属于映射数据)
const CAT_STYLE: Record<string, { color: string }> = {
  '科技TMT': { color: '#185FA5' },
  '先进制造': { color: '#534AB7' },
  '大消费': { color: '#993556' },
  '周期资源': { color: '#993C1D' },
  '金融地产': { color: '#854F0B' },
  '稳定公用': { color: '#0F6E56' },
};

// 热力图 y 轴: 行业名前加类别色点 (ECharts rich text)
const catAxisRich = (order: string[]) => order.reduce((m, c, i) => {
  m[`c${i}`] = { color: CAT_STYLE[c]?.color || '#999', fontSize: 7 };
  return m;
}, {} as Record<string, { color: string; fontSize: number }>);
const catAxisLabel = (name: string, map: Record<string, string>, order: string[]) => {
  const i = order.indexOf(map[name] || '');
  return i >= 0 ? `{c${i}|●} ${name}` : name;
};

interface CatStatItem { cat: string; n: number; score: number | null; chg: number | null; }

// 6 大类别统计卡: 按 score 降序, 第一名标"占优" (综合分热力图=均分+日变化; RPS=均RPS+日变化)
function CatStatCards({ items, scoreLabel }: { items: CatStatItem[]; scoreLabel: string }) {
  return (
    <div className="grid grid-cols-6 gap-1.5 mb-3">
      {items.map((s, i) => (
        <div key={s.cat}
          className={`rounded-lg px-2 py-1.5 border ${i === 0 ? 'border-red-300 bg-red-50' : 'border-gray-100 bg-gray-50'}`}>
          <div className="flex items-center gap-1 text-xs font-medium">
            <span className="w-2 h-2 rounded-full inline-block" style={{ background: CAT_STYLE[s.cat]?.color || '#999' }} />
            <span style={{ color: CAT_STYLE[s.cat]?.color || '#333' }}>{s.cat}</span>
            {i === 0 && <span className="text-[10px] text-red-600 font-semibold ml-auto">占优</span>}
          </div>
          <div className="mt-0.5 text-xs space-x-1.5 whitespace-nowrap">
            <span>{scoreLabel} <b className={numColor((s.score ?? 0) - 50)}>{fmt(s.score)}</b></span>
            {s.chg != null && <span className={numColor(s.chg)}>{s.chg > 0 ? '+' : ''}{s.chg.toFixed(1)}</span>}
            <span className="text-gray-400">{s.n}行业</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// 紧凑视图 27 列: 三维打分(趋势强度/当日动能/综合分) + 位置三列 + 当日 11 列
const COMPACT_COLS = ['分类', '趋势强度', '当日动能', '综合分', '当日涨幅%', '当日等权涨幅%', '涨家数', '跌家数', '涨家数%', '涨停数', '跌停数', '大肉%', '大面%',
  '领涨市值', '领涨股', '阶段', '250位置%', '3年位置%', '5年位置%',
  '20日涨幅', '广度%', '广度20日Δ', '量能比', '量能比5',
  '主力5日(亿)', '5日净占比%', '主力20日(亿)', '主力净占比%', '资金流Δ'];

const numColor = (v: any) =>
  v == null || Number.isNaN(Number(v)) ? 'text-gray-400'
    : Number(v) > 0 ? 'text-red-600' : Number(v) < 0 ? 'text-green-600' : 'text-gray-500';

// 61 列分组: 明细页顶部标签切换, 只显示该组列
const COL_GROUPS: { id: string; label: string; cols: string[] }[] = [
  { id: 'all', label: '全部', cols: [] },  // 空=显示全部
  { id: 'trend', label: '趋势位置', cols: ['分类', '阶段', '趋势强度', '当日动能', '综合分', '250位置%', '3年位置%', '5年位置%', '距250高%', '距250低%', '5日涨幅', '20日涨幅', '60日涨幅'] },
  { id: 'money', label: '量能资金', cols: ['分类', '综合分', '量能比', '量能比5', '主力当日(亿)', '当日净占比%', '主力5日(亿)', '5日净占比%', '主力20日(亿)', '主力净占比%', '资金流Δ'] },
  { id: 'daily', label: '当日', cols: ['分类', '综合分', '当日涨幅%', '当日等权涨幅%', '涨家数', '跌家数', '涨家数%', '涨停数', '跌停数', '大肉%', '大面%', '领涨市值', '领涨股'] },
  { id: 'bench', label: '基准对比', cols: ['分类', '综合分', '20日涨幅', 'vs科创50%', 'vs创业板指%', 'vs上证50%', 'vs沪深300%', 'vs中证1000%', 'vs深证成指%'] },
];

const fmt = (v: any, digits = 1) =>
  v == null || v === '' || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(digits);

const CHANGE_BADGE: Record<string, string> = {
  '分类跃迁': 'bg-purple-100 text-purple-800',
  '资金流恶化': 'bg-green-100 text-green-800',
  '资金流改善': 'bg-red-100 text-red-800',
  '量能拐点': 'bg-amber-100 text-amber-800',
  '新增警报': 'bg-orange-100 text-orange-800',
  '警报解除': 'bg-gray-100 text-gray-600',
  '警报反转': 'bg-red-100 text-red-800 font-semibold',
  '净占比偏离5日': 'bg-blue-100 text-blue-800',
  '净占比偏离20日': 'bg-blue-100 text-blue-800',
  '资金流Δ偏离5日': 'bg-cyan-100 text-cyan-800',
  '资金流Δ偏离20日': 'bg-cyan-100 text-cyan-800',
  '当日涨幅偏离5日': 'bg-indigo-100 text-indigo-800',
  '当日涨幅偏离20日': 'bg-indigo-100 text-indigo-800',
};

// ─── Plotly 基础配置 ──────────────────────────────────────────────────────

const PLOT_CFG = { displayModeBar: false, responsive: true };
const FONT = { family: '-apple-system, PingFang SC, sans-serif', size: 11 };

type SubTab = 'overview' | 'charts' | 'detail' | 'industry' | 'heatmap' | 'rps' | 'rotation';
const SUB_TABS: { id: SubTab; label: string; hint: string; Icon: any }[] = [
  { id: 'overview', label: '总览', hint: '当日结论 + 与上期变化', Icon: LayoutDashboard },
  { id: 'charts', label: '图表', hint: '四象限 / 量能 / 资金流', Icon: ChartScatter },
  { id: 'detail', label: '明细排序', hint: '63 列全表, 点行业看详情', Icon: Table2 },
  { id: 'industry', label: '行业详情', hint: 'K 线 + 均线 + 位置 + 时序', Icon: CandlestickChart },
  { id: 'heatmap', label: '热力图', hint: '综合分 日期×行业 热力图', Icon: Grid3x3 },
  { id: 'rps', label: 'RPS 强度', hint: '行业相对强度 RPS 日期×行业', Icon: Grid3x3 },
  { id: 'rotation', label: '类别轮动', hint: '6 大板块类别轮动时序', Icon: TrendingUp },
];

// ─── 主页面 ───────────────────────────────────────────────────────────────

export function IndustryTrendPage() {
  const [data, setData] = useState<TrendPayload | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [loading, setLoading] = useState(true);
  const [jobRunning, setJobRunning] = useState(false);
  const [jobProgress, setJobProgress] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  const [subTab, setSubTab] = useState<SubTab>('overview');
  const [selIndustry, setSelIndustry] = useState('');
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async (date?: string) => {
    setLoading(true); setError('');
    try {
      const url = date ? `/api/industry-trend/data?date=${date}` : '/api/industry-trend/latest';
      const d = await apiFetch<TrendPayload | null>(url);
      setData(d);
      if (!d && date) setError(`${date} 还没有存档，点「重算选中日期」即可生成`);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    apiFetch<string[]>('/api/industry-trend/dates').then(d => {
      setDates(d);
      const first = d.length > 0 ? d[0] : '';
      setSelectedDate(first);
      fetchData(first || undefined);
    }).catch(() => fetchData());
  }, [fetchData]);

  // 取最新日期 (照抄复盘数据页 handleSyncLatest): 从 Exodia 拿最新数据日,
  // 放进日期下拉并选中 — 不触发任何计算 (计算统一走「重算选中日期」)
  const syncLatest = async () => {
    if (syncing || jobRunning) return;
    setSyncing(true); setError('');
    try {
      const newDates = await apiFetch<string[]>('/api/industry-trend/dates');
      setDates(newDates);
      if (newDates.length > 0) {
        setSelectedDate(newDates[0]);
        await fetchData(newDates[0]);
      }
    } catch (e: any) {
      setError(e.message || '取最新日期失败');
    } finally {
      setSyncing(false);
    }
  };

  // 重算选中日期: 异步后台任务(照抄复盘 v2 模式)——先增量刷申万缓存再算,
  // 新交易日数据才完整; 按钮旁轮询显示进度, 页面不阻塞。
  const stopPolling = useCallback(() => {
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const recompute = async () => {
    if (!selectedDate || jobRunning) return;
    if (!confirm(`重算 ${selectedDate} 的行业趋势?\n先增量拉取申万指数/资金流缓存(已最新则秒级跳过), 再跑 50 列截面 + 存档 + 与上期对比。\n后台运行约 1-4 分钟, 按钮旁显示进度, 页面其它部分照常可用。`)) return;
    setError(''); setJobRunning(true); setJobProgress('启动中...');
    try {
      await apiFetch<{ started: boolean }>('/api/industry-trend/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trade_date: selectedDate, force: true }),
      });
      stopPolling();
      pollTimer.current = setInterval(async () => {
        try {
          const job = await apiFetch<{
            state: string; progress: string; error: string | null;
            result: { trade_date: string; changes: number } | null;
          }>('/api/industry-trend/job');
          setJobProgress(job.progress || '');
          if (job.state === 'done') {
            stopPolling(); setJobRunning(false); setJobProgress('');
            const newDates = await apiFetch<string[]>('/api/industry-trend/dates');
            setDates(newDates);
            const td = job.result?.trade_date || selectedDate;
            setSelectedDate(td);
            fetchData(td);
          } else if (job.state === 'error') {
            stopPolling(); setJobRunning(false); setJobProgress('');
            setError(job.error || '重算失败');
          }
        } catch { /* 单次轮询失败忽略, 3 秒后重试 */ }
      }, 3000);
    } catch (e: any) {
      setJobRunning(false); setJobProgress('');
      setError(e.message || '启动重算失败');
    }
  };

  // 明细表点行业 → 记住选中 + 自动跳到「行业详情」子页
  const onRowClick = (ind: string) => {
    setSelIndustry(prev => (prev === ind ? '' : ind));
    if (ind) setSubTab('industry');
  };

  return (
    <div className="p-4 space-y-4">
      <TabHeader title="行业趋势" />

      {/* 控制条 — 所有子页共用 */}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-gray-500">交易日</span>
        <select className="border rounded-lg px-2 py-1.5 bg-white" value={selectedDate}
          onChange={e => { setSelectedDate(e.target.value); fetchData(e.target.value); }}>
          {dates.map(d => <option key={d} value={d}>{d}</option>)}
          {!dates.length && <option value="">暂无存档</option>}
        </select>
        <button onClick={syncLatest} disabled={syncing || jobRunning}
          title="从 Exodia 数据中心读最新交易日, 放进下拉并选中 (不触发计算)"
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-60 transition-colors">
          <CalendarDays size={13} className={syncing ? 'animate-pulse text-blue-600' : 'text-gray-400'} />
          {syncing ? '取日期中…' : '取最新日期'}
        </button>
        <button onClick={recompute} disabled={jobRunning || !selectedDate}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60 transition-colors">
          <RefreshCw size={13} className={jobRunning ? 'animate-spin' : ''} />
          {jobRunning ? '重算中…' : '重算选中日期'}
        </button>
        {jobRunning && jobProgress && (
          <span className="text-xs text-blue-600 animate-pulse">{jobProgress}</span>
        )}
        {data?.prev_date && <span className="text-gray-400 text-xs">对比上期存档: {data.prev_date}</span>}
        {data && <span className="text-gray-500 text-xs ml-auto">{data.summary.headline}</span>}
      </div>

      {/* 子页切换 — 分段式控制条 */}
      <div>
        <div className="inline-flex items-center gap-1 bg-gray-100 rounded-xl p-1">
          {SUB_TABS.map(({ id, label, hint, Icon }) => (
            <button key={id} onClick={() => setSubTab(id)} title={hint}
              className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-sm transition-all ${
                subTab === id
                  ? 'bg-white shadow-sm text-gray-900 font-semibold'
                  : 'text-gray-500 hover:text-gray-800 hover:bg-white/60'}`}>
              <Icon size={14} className={subTab === id ? 'text-blue-600' : 'text-gray-400'} />
              {label}
              {id === 'industry' && selIndustry && (
                <span className="ml-0.5 px-1.5 py-px rounded-full bg-blue-100 text-blue-700 text-[10px] font-medium leading-4 max-w-[7em] truncate">
                  {selIndustry}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="text-[11px] text-gray-400 mt-1.5 pl-1">
          {SUB_TABS.find(t => t.id === subTab)?.hint}
        </div>
      </div>

      {error && <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded px-3 py-2 text-sm">{error}</div>}
      {loading && <div className="text-gray-400 text-sm py-8 text-center">加载中…</div>}

      {!loading && data && (
        <>
          {subTab === 'overview' && <OverviewTab data={data} selIndustry={selIndustry} onRowClick={onRowClick} />}
          {subTab === 'charts' && <ChartsTab data={data} />}
          {subTab === 'detail' && <DetailTab data={data} selIndustry={selIndustry} onRowClick={onRowClick} />}
          {subTab === 'industry' && (
            <IndustryDetailTab industry={selIndustry} date={selectedDate}
              onClose={() => { setSelIndustry(''); setSubTab('detail'); }} />
          )}
          {subTab === 'heatmap' && <HeatmapTab onSelect={onRowClick} />}
          {subTab === 'rps' && <RpsHeatmapTab onSelect={onRowClick} />}
          {subTab === 'rotation' && <CategoryRotationTab />}
        </>
      )}

      {!loading && data && (
        <div className="text-gray-400 text-xs">
          口径: 申万官方一级行业指数 · 广度/形态来自本地个股(后复权) · 资金流为板块级主力净额(Σ净额÷Σ成交额) ·
          量能比5=5日均额/120日均额(快档), 量能比=20日均额/120日均额(慢档) · 资金流Δ=5日净占比−20日净占比
        </div>
      )}
    </div>
  );
}

// ─── 子页①: 总览 (结论卡 + 分类分布 + 变化清单) ───────────────────────────

function OverviewTab({ data, selIndustry, onRowClick }: {
  data: TrendPayload; selIndustry: string; onRowClick: (ind: string) => void;
}) {
  const s = data.summary;
  const catCount = (m: Record<string, number> | undefined, keys: string[]) =>
    keys.reduce((a, k) => a + (m?.[k] || 0), 0);
  const bullN = catCount(s?.cat_counts, ['强势多头', '多头', '多头回踩']);
  const bottomN = catCount(s?.cat_counts, ['筑底反转', '筑底回踩']);
  const bearN = catCount(s?.cat_counts, ['空头', '强势空头']);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <MetricCard label="多/筑底/空" value={`${bullN} / ${bottomN} / ${bearN}`} sub="行业分类计数" />
        <MetricCard label="分类跃迁 vs 上期" value={`↑${s?.upgrades ?? 0} ↓${s?.downgrades ?? 0}`}
          sub={data.prev_date ? `对比 ${data.prev_date}` : '首个存档日, 无上期'} />
        <MetricCard label="四维共振 ⭐" value={(s?.resonance?.length ? s.resonance.join(' · ') : '无')}
          sub="趋势+资金+量能+广度同向" valueSmall={!!s?.resonance?.length} />
        <MetricCard label="资金流恶化之最"
          value={s?.ff_worsen_top3?.[0] ? `${s.ff_worsen_top3[0][0]} ${fmt(s.ff_worsen_top3[0][1], 2)}pp` : '--'}
          sub={`改善之最 ${s?.ff_improve_top3?.[0] ? s.ff_improve_top3[0][0] + ' ' + fmt(s.ff_improve_top3[0][1], 2) + 'pp' : '--'}`} />
        <MetricCard label="当日警报 (|净占比|≥3%)"
          value={`${s?.day_alerts?.length ?? 0} 个`}
          sub={(s?.day_alerts || []).slice(0, 3).map(a => `${a[0]}${a[1] > 0 ? '+' : ''}${fmt(a[1])}%`).join(' ') || '无'} />
      </div>

      <Card title="分类分布 (上期 → 本期)">
        <div className="flex flex-wrap gap-2">
          {CAT_ORDER.map(cat => {
            const cur = s?.cat_counts?.[cat] || 0, prev = s?.prev_cat_counts?.[cat] || 0;
            const diff = cur - prev;
            const empty = !cur && !prev;   // 0家也显示(灰色弱化), 保持10档完整可见
            return (
              <span key={cat} className="px-2 py-1 rounded text-xs"
                style={empty
                  ? { background: '#f5f5f4', color: '#a8a29e' }
                  : { background: (CAT_COLOR[cat] || '#999') + '18', color: CAT_COLOR[cat] || '#666' }}>
                {cat}: {prev} → {cur}
                {diff !== 0 && <b className={diff > 0 ? 'text-red-600' : 'text-green-600'}> ({diff > 0 ? '+' : ''}{diff})</b>}
              </span>
            );
          })}
        </div>
      </Card>

      <Card title={`变化清单 vs 上期 (${data.prev_date || '—'}) — 只列有变化的行`}>
        {data.changes.length === 0 ? (
          <div className="text-gray-400 text-sm py-3 text-center">
            {data.prev_date ? '本期与上期无显著变化' : '首个存档日, 下期起自动生成对比'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 text-left border-b">
                <th className="py-1.5 pr-3">行业</th><th className="pr-3">变化</th>
                <th className="pr-3">上期 → 本期</th><th>关注理由</th>
              </tr></thead>
              <tbody>
                {data.changes.map((c, i) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-1.5 pr-3 font-medium">
                      <button onClick={() => onRowClick(c.行业)}
                        className={`hover:text-blue-600 hover:underline cursor-pointer ${selIndustry === c.行业 ? 'text-blue-600 font-semibold' : ''}`}>
                        {c.行业}
                      </button>
                    </td>
                    <td className="pr-3"><span className={`px-1.5 py-0.5 rounded ${CHANGE_BADGE[c.类型] || 'bg-gray-100'}`}>{c.类型}</span></td>
                    <td className={`pr-3 ${c.方向 === 'up' ? 'text-red-600' : 'text-green-600'}`}>
                      {typeof c.上期 === 'number' ? fmt(c.上期, 2) : c.上期} → {typeof c.本期 === 'number' ? fmt(c.本期, 2) : c.本期}
                    </td>
                    <td className="text-gray-500">{c.说明}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// ─── 子页②: 图表 (四象限 + 量能快慢档 + 资金流Δ排行) ─────────────────────

function ChartsTab({ data }: { data: TrendPayload }) {
  const quadRef = useRef<HTMLDivElement>(null);
  const deltaRef = useRef<HTMLDivElement>(null);
  const volRef = useRef<HTMLDivElement>(null);

  // ── 图②: 四象限印证矩阵 (x=20日涨幅, y=主力净占比) ──
  useEffect(() => {
    if (!quadRef.current || !data?.table?.length) return;
    const rows = data.table;
    const traces = Object.keys(CAT_COLOR)
      .map(cat => {
        const sub = rows.filter(r => r['分类'] === cat);
        if (!sub.length) return null;
        return {
          x: sub.map(r => r['20日涨幅']), y: sub.map(r => r['主力净占比%']),
          text: sub.map(r => `${r['行业']} | ${r['分类']} | Δ${fmt(r['资金流Δ'], 2)}pp`),
          customdata: sub.map(r => r['行业']),
          mode: 'markers' as const, name: cat,
          marker: {
            color: CAT_COLOR[cat],
            size: sub.map(r => Math.max(7, Math.min(30, Math.sqrt(Math.abs(Number(r['主力20日(亿)']) || 10)) * 1.6))),
            opacity: 0.82, line: { width: 0.5, color: '#fff' },
          },
          hovertemplate: '<b>%{text}</b><br>20日涨幅 %{x:.1f}% | 主力净占比 %{y:.1f}%<extra></extra>',
        };
      }).filter(Boolean);
    const layout = {
      font: FONT, margin: { l: 46, r: 12, t: 24, b: 36 },
      showlegend: true, legend: { font: { size: 9 }, orientation: 'h' as const, y: -0.14 },
      xaxis: { title: '20日涨幅 %', zeroline: true, zerolinecolor: '#999', gridcolor: '#eee' },
      yaxis: { title: '主力净占比 % (20日)', zeroline: true, zerolinecolor: '#999', gridcolor: '#eee' },
      annotations: [
        { x: 0.98, y: 0.96, xref: 'paper' as const, yref: 'paper' as const, text: '真涨区', showarrow: false, font: { size: 10, color: '#b91c1c' } },
        { x: 0.02, y: 0.96, xref: 'paper' as const, yref: 'paper' as const, text: '虚涨警惕', showarrow: false, font: { size: 10, color: '#996600' } },
        { x: 0.02, y: 0.04, xref: 'paper' as const, yref: 'paper' as const, text: '主跌区', showarrow: false, font: { size: 10, color: '#166534' } },
        { x: 0.98, y: 0.04, xref: 'paper' as const, yref: 'paper' as const, text: '吸筹观察', showarrow: false, font: { size: 10, color: '#534AB7' } },
      ],
      height: 340,
    };
    Plotly.react(quadRef.current, traces as any, layout as any, PLOT_CFG);
  }, [data]);

  // ── 图③: 资金流Δ 排行 (横向条形) ──
  useEffect(() => {
    if (!deltaRef.current || !data?.table?.length) return;
    const rows = [...data.table].filter(r => r['资金流Δ'] != null)
      .sort((a, b) => Number(a['资金流Δ']) - Number(b['资金流Δ']));
    Plotly.react(deltaRef.current, [{
      x: rows.map(r => r['资金流Δ']), y: rows.map(r => r['行业']),
      type: 'bar', orientation: 'h',
      marker: { color: rows.map(r => Number(r['资金流Δ']) >= 0 ? UP : DOWN) },
      text: rows.map(r => fmt(r['资金流Δ'], 2)), textposition: 'outside',
      textfont: { size: 8 },
      hovertemplate: '%{y}: %{x:+.2f}pp<extra></extra>',
    }], {
      font: FONT, height: 620, margin: { l: 76, r: 40, t: 24, b: 30 },
      xaxis: { title: '资金流Δ pp (5日净占比−20日净占比)', gridcolor: '#eee' },
      yaxis: { automargin: true, tickfont: { size: 9 } },
    } as any, PLOT_CFG);
  }, [data]);

  // ── 图④: 量能快慢档散点 (x=量能比20日, y=量能比5, 对角线) ──
  useEffect(() => {
    if (!volRef.current || !data?.table?.length) return;
    const rows = data.table.filter(r => r['量能比'] != null && r['量能比5'] != null);
    const xs = rows.map(r => Number(r['量能比'])), ys = rows.map(r => Number(r['量能比5']));
    const lo = Math.min(...xs, ...ys) - 0.05, hi = Math.max(...xs, ...ys) + 0.05;
    Plotly.react(volRef.current, [{
      x: xs, y: ys, mode: 'markers',
      text: rows.map(r => `${r['行业']}: 慢${fmt(r['量能比'], 2)} 快${fmt(r['量能比5'], 2)}`),
      marker: {
        color: rows.map(r => Number(r['量能比5']) > Number(r['量能比']) ? UP : (Number(r['量能比5']) >= 1.2 ? '#534AB7' : GRAY)),
        size: 9, opacity: 0.8,
      },
      hovertemplate: '<b>%{text}</b><extra></extra>',
    }], {
      font: FONT, height: 340, margin: { l: 46, r: 12, t: 24, b: 36 },
      showlegend: false,
      xaxis: { title: '量能比 (20日, 慢档)', range: [lo, hi], gridcolor: '#eee' },
      yaxis: { title: '量能比5 (快档)', range: [lo, hi], gridcolor: '#eee' },
      shapes: [
        { type: 'line', x0: lo, x1: hi, y0: lo, y1: hi, line: { color: '#999', width: 1, dash: 'dot' } },
        { type: 'line', x0: 1.2, x1: 1.2, y0: lo, y1: hi, line: { color: '#ddd', width: 1 } },
        { type: 'line', x0: lo, x1: hi, y0: 1.2, y1: 1.2, line: { color: '#ddd', width: 1 } },
      ],
      annotations: [
        { x: hi - 0.02, y: hi - 0.03, xref: 'x' as const, yref: 'y' as const, text: '放量启动', showarrow: false, font: { size: 10, color: '#b91c1c' } },
        { x: lo + 0.02, y: lo + 0.03, xref: 'x' as const, yref: 'y' as const, text: '缩量退潮', showarrow: false, font: { size: 10, color: '#166534' } },
      ],
    } as any, PLOT_CFG);
  }, [data]);

  useEffect(() => {
    return () => {
      [quadRef, deltaRef, volRef].forEach(r => { if (r.current) Plotly.purge(r.current); });
    };
  }, []);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="四象限印证矩阵 — 价格涨幅 × 主力净占比 (点大小=主力20日净额)">
          <div ref={quadRef} className="w-full" />
        </Card>
        <Card title="量能快慢档 — 快档(量能比5) vs 慢档(量能比) — 对角线上方=放量启动">
          <div ref={volRef} className="w-full" />
        </Card>
      </div>
      <Card title="资金流Δ 排行 — 5日净占比−20日净占比 (红=改善/资金返场, 绿=恶化/隐藏抛售)">
        <div ref={deltaRef} className="w-full" />
      </Card>
    </div>
  );
}

// ─── 子页③: 明细排序 (全量明细表, 点行跳行业详情) ────────────────────────

function DetailTab({ data, selIndustry, onRowClick }: {
  data: TrendPayload; selIndustry: string; onRowClick: (ind: string) => void;
}) {
  const [showAllCols, setShowAllCols] = useState(false);
  const [colGroup, setColGroup] = useState('all');
  const [sortKey, setSortKey] = useState('');
  const [sortDir, setSortDir] = useState(1);
  const catMeta = useCatMeta();

  // 在所有列组合的"行业"后注入前端派生列"类别"(31行业→6大板块类别)
  const withCategory = (cols: string[]) =>
    cols.includes('类别') ? cols : ['行业', '类别', ...cols.filter(c => c !== '行业')];

  const tableCols = useMemo(() => {
    if (colGroup !== 'all') {
      const g = COL_GROUPS.find(x => x.id === colGroup);
      return g ? withCategory(['行业', ...g.cols]) : withCategory(['行业', ...COMPACT_COLS]);
    }
    return withCategory(showAllCols ? data.cols : ['行业', ...COMPACT_COLS]);
  }, [data, showAllCols, colGroup]);

  // 类别统计: 各类别 行业数/平均综合分/平均当日涨幅/涨家数 → 顶部占优排行
  const catStats = useMemo(() => {
    const agg: Record<string, { n: number; scoreSum: number; scoreN: number; daySum: number; dayN: number; up: number }> = {};
    for (const r of data.table) {
      const cat = catMeta.map[r['行业']];
      if (!cat) continue;
      const a = agg[cat] ||= { n: 0, scoreSum: 0, scoreN: 0, daySum: 0, dayN: 0, up: 0 };
      a.n++;
      const s = Number(r['综合分']);
      if (!Number.isNaN(s)) { a.scoreSum += s; a.scoreN++; }
      const d = Number(r['当日等权涨幅%']);
      if (!Number.isNaN(d)) { a.daySum += d; a.dayN++; if (d > 0) a.up++; }
    }
    const list = catMeta.order.map(cat => {
      const a = agg[cat] || { n: 0, scoreSum: 0, scoreN: 0, daySum: 0, dayN: 0, up: 0 };
      return { cat, n: a.n, avgScore: a.scoreN ? a.scoreSum / a.scoreN : null, avgDay: a.dayN ? a.daySum / a.dayN : null, up: a.up };
    });
    // 按平均综合分降序: 第一名=最近占优的类别
    return list.sort((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));
  }, [data, catMeta]);

  // 排序后的表格行 (未点排序时保持存档原序: 分类 → 趋势强度)
  const sortedRows = useMemo(() => {
    if (!sortKey) return data.table;
    const catIdx = (v: any) => CAT_ORDER.indexOf(String(v));
    return [...data.table].sort((a, b) => {
      const va = a[sortKey], vb = b[sortKey];
      if (sortKey === '分类') return (catIdx(va) - catIdx(vb)) * sortDir;
      if (sortKey === '类别') {
        return (catMeta.order.indexOf(String(va)) - catMeta.order.indexOf(String(vb))) * sortDir;
      }
      const na = Number(va), nb = Number(vb);
      const aOk = va != null && va !== '' && !Number.isNaN(na);
      const bOk = vb != null && vb !== '' && !Number.isNaN(nb);
      if (aOk && bOk) return (na - nb) * sortDir;
      if (aOk !== bOk) return (aOk ? -1 : 1) * sortDir;   // 空值沉底
      return String(va ?? '').localeCompare(String(vb ?? ''), 'zh') * sortDir;
    });
  }, [data, sortKey, sortDir, catMeta]);

  const toggleSort = (col: string) => {
    if (sortKey === col) {
      if (sortDir === 1) setSortDir(-1);
      else { setSortKey(''); setSortDir(1); }   // 再点一次取消排序
    } else { setSortKey(col); setSortDir(1); }
  };

  const cellColor = (col: string, v: any) => {
    if (['20日涨幅', '5日涨幅', '广度20日Δ', '主力5日(亿)', '主力20日(亿)', '主力当日(亿)',
      '5日净占比%', '主力净占比%', '资金流Δ', '当日涨幅%', '当日等权涨幅%', '涨家数', '跌家数', '涨家数%', '涨停数', '跌停数',
      '大肉%', '大面%'].includes(col)) return numColor(v);
    // 三维打分: 高分红(强) / 低分绿(弱), 综合分加粗
    if (['趋势强度', '当日动能', '综合分'].includes(col)) {
      const n = Number(v);
      if (Number.isNaN(n)) return 'text-gray-400';
      const base = n >= 70 ? 'text-red-600' : n >= 50 ? 'text-amber-600' : 'text-green-600';
      return col === '综合分' ? base + ' font-semibold' : base;
    }
    if (['当日净占比%'].includes(col)) {
      const n = Number(v);
      if (Number.isNaN(n)) return 'text-gray-400';
      return Math.abs(n) >= 3 ? (n > 0 ? 'text-red-600 font-bold' : 'text-green-600 font-bold') : 'text-gray-400';
    }
    // 位置列: 高位红(热) / 中位黄 / 低位绿(机会), 与 K 线图分位条同色
    if (['250位置%', '3年位置%', '5年位置%'].includes(col)) {
      const n = Number(v);
      if (v == null || Number.isNaN(n)) return 'text-gray-400';
      return n >= 70 ? 'text-red-600 font-semibold' : n >= 30 ? 'text-amber-600' : 'text-green-600';
    }
    return '';
  };

  return (
    <Card title={`全量明细 (${data.table.length} 行业 × ${data.cols.length} 列) — 点击行看 K 线 + 时序; 点表头排序`}>
      {/* 类别占优统计: 6 大板块平均综合分排行, 第一名标"占优" */}
      <div className="grid grid-cols-6 gap-1.5 mb-3">
        {catStats.map((s, i) => (
          <div key={s.cat}
            className={`rounded-lg px-2 py-1.5 border ${i === 0 ? 'border-red-300 bg-red-50' : 'border-gray-100 bg-gray-50'}`}>
            <div className="flex items-center gap-1 text-xs font-medium">
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: CAT_STYLE[s.cat]?.color || '#999' }} />
              <span style={{ color: CAT_STYLE[s.cat]?.color || '#333' }}>{s.cat}</span>
              {i === 0 && <span className="text-[10px] text-red-600 font-semibold ml-auto">占优</span>}
            </div>
            <div className="mt-0.5 text-xs space-x-1.5 whitespace-nowrap">
              <span>综合分 <b className={numColor((s.avgScore ?? 0) - 50)}>{fmt(s.avgScore)}</b></span>
              <span className={numColor(s.avgDay)}>{fmt(s.avgDay, 2)}%</span>
              <span className="text-gray-400">{s.up}/{s.n}涨</span>
            </div>
          </div>
        ))}
      </div>
      {/* 分组标签: 切换列组合 */}
      <div className="flex items-center gap-1 mb-2 text-xs">
        <span className="text-gray-400 mr-1">分组:</span>
        {COL_GROUPS.map(g => (
          <button key={g.id} onClick={() => setColGroup(g.id)}
            className={`px-2.5 py-1 rounded-md transition-colors ${colGroup === g.id
              ? 'bg-blue-600 text-white font-medium'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
            {g.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3 mb-2 text-xs">
        <label className="flex items-center gap-1 cursor-pointer">
          <input type="checkbox" checked={showAllCols} onChange={e => setShowAllCols(e.target.checked)}
            disabled={colGroup !== 'all'} />
          展开全部 {data.cols.length} 列
        </label>
        <span className="text-gray-400">默认精简 27 列; 分组标签切换列组合; 点表头升序→降序→取消; 综合分加粗着色; 当日净占比≥3%加粗</span>
      </div>
      <div className="overflow-x-auto">
        <table className="text-xs whitespace-nowrap">
          <thead><tr className="text-gray-500 border-b">
            {tableCols.map(c => (
              <th key={c} onClick={() => toggleSort(c)}
                className={`py-1.5 px-2 text-left sticky top-0 bg-white cursor-pointer select-none hover:text-gray-800 ${sortKey === c ? 'text-gray-900 font-semibold' : ''}`}>
                {c}
                <span className="ml-0.5 inline-block w-2 text-[9px] align-middle">
                  {sortKey === c ? (sortDir > 0 ? '▲' : '▼') : '⇅'}
                </span>
              </th>
            ))}
          </tr></thead>
          <tbody>
            {sortedRows.map(r => (
              <tr key={r['行业']} onClick={() => onRowClick(r['行业'])}
                className={`border-b border-gray-100 cursor-pointer hover:bg-blue-50 ${selIndustry === r['行业'] ? 'bg-blue-50' : ''}`}>
                {tableCols.map(c => {
                  // 类别列: 映射来自后端 (quant/industry_trend_daily.py)
                  if (c === '类别') {
                    const cat = catMeta.map[r['行业']] || '--';
                    return (
                      <td key={c} className="py-1 px-2">
                        <span className="inline-flex items-center gap-1" style={{ color: CAT_STYLE[cat]?.color || '#666' }}>
                          <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: CAT_STYLE[cat]?.color || '#999' }} />
                          {cat}
                        </span>
                      </td>
                    );
                  }
                  return (
                    <td key={c} className={`py-1 px-2 ${cellColor(c, r[c])} ${c === '资金流Δ' ? 'font-medium' : ''}`}>
                      {c === '分类' ? <span style={{ color: CAT_COLOR[r[c]] || '#666' }}>{r[c]}</span>
                        : typeof r[c] === 'number' ? (Number.isInteger(r[c]) ? r[c] : fmt(r[c], 2)) : (r[c] ?? '--')}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─── 子页⑤: 综合分热力图 (日期 × 行业, ECharts heatmap) ────────────────────

function HeatmapTab({ onSelect }: { onSelect: (ind: string) => void }) {
  const [data, setData] = useState<{ dates: string[]; industries: string[]; scores: (number | null)[][]; ma?: Record<string, { ma5?: number; ma20?: number; ma60?: number }> } | null>(null);
  const [loading, setLoading] = useState(true);
  // 点击格子选中的行业 (弹出分数+均线面板, 而非直接跳详情)
  const [sel, setSel] = useState<{ ind: string; dt: string; score: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const catMeta = useCatMeta();

  useEffect(() => {
    apiFetch<{ dates: string[]; industries: string[]; scores: (number | null)[][]; ma?: Record<string, { ma5?: number; ma20?: number; ma60?: number }> }>(
      '/api/industry-trend/heatmap?days=40')
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // 异动: 最新日 vs 昨日 且 偏离5日均值
  const alerts = useMemo(() => {
    if (!data || data.dates.length < 2) return new Set<string>();
    const n = data.dates.length - 1;
    const prev = n - 1;
    const out = new Set<string>();
    data.industries.forEach((ind, i) => {
      const now = data.scores[i]?.[n];
      const yesterday = data.scores[i]?.[prev];
      const last5 = data.scores[i]?.slice(Math.max(0, n - 4), n + 1).filter(v => v != null) || [];
      const avg5 = last5.length >= 3 ? last5.reduce((a, b) => a + b, 0) / last5.length : null;
      if (now == null || yesterday == null || avg5 == null) return;
      const dayChg = now - yesterday;
      const dev5 = now - avg5;
      // 双条件同时满足才标箭头: 日环比|>=10 且 偏离5日均|>=6 (避免箭头泛滥)
      if (Math.abs(dayChg) >= 10 && Math.abs(dev5) >= 6) {
        out.add(`${ind}|${dayChg > 0 ? 'up' : 'down'}`);
      }
    });
    return out;
  }, [data]);

  // 类别统计: 最新日各类别平均综合分 + 较前一日变化
  const catStats = useMemo<CatStatItem[]>(() => {
    if (!data || data.dates.length < 2) return [];
    const n = data.dates.length - 1, prev = n - 1;
    const agg: Record<string, { n: number; sum: number; chgSum: number; chgN: number }> = {};
    data.industries.forEach((ind, i) => {
      const cat = catMeta.map[ind];
      if (!cat) return;
      const a = agg[cat] ||= { n: 0, sum: 0, chgSum: 0, chgN: 0 };
      a.n++;
      const now = data.scores[i]?.[n], y = data.scores[i]?.[prev];
      if (now != null) a.sum += now;
      if (now != null && y != null) { a.chgSum += now - y; a.chgN++; }
    });
    return catMeta.order.map(cat => {
      const a = agg[cat] || { n: 0, sum: 0, chgSum: 0, chgN: 0 };
      return { cat, n: a.n, score: a.n ? a.sum / a.n : null, chg: a.chgN ? a.chgSum / a.chgN : null };
    }).sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }, [data, catMeta]);

  useEffect(() => {
    if (!ref.current || !data?.dates?.length) return;
    // ECharts heatmap: x=日期, y=行业, value=综合分
    const hmData: [number, number, number][] = [];
    data.scores.forEach((row, i) => {
      row.forEach((v, j) => {
        if (v != null) hmData.push([j, i, v]);
      });
    });
    const option = {
      tooltip: {
        formatter: (p: any) => {
          const ind = data.industries[p.data[1]];
          const dt = data.dates[p.data[0]];
          const cat = catMeta.map[ind];
          const ma = data.ma?.[ind] || {};
          const maLine = [
            ma.ma5 != null ? `5日均 ${ma.ma5.toFixed(1)}` : null,
            ma.ma20 != null ? `20日均 ${ma.ma20.toFixed(1)}` : null,
            ma.ma60 != null ? `60日均 ${ma.ma60.toFixed(1)}` : null,
          ].filter(Boolean).join(' · ');
          return `${ind} | ${dt} | <b>${cat || '--'}</b><br/>综合分: <b>${p.data[2].toFixed(1)}</b>` +
            (maLine ? `<br/>${maLine}` : '');
        },
      },
      grid: { left: 78, right: 60, top: 10, bottom: 40 },
      xAxis: {
        type: 'category' as const,
        data: data.dates.map(d => d.slice(5)),
        axisLabel: { fontSize: 8, rotate: 60, interval: 1 },
      },
      yAxis: {
        type: 'category' as const,
        data: data.industries,
        axisLabel: {
          fontSize: 9,
          rich: catAxisRich(catMeta.order),
          formatter: (name: string) => catAxisLabel(name, catMeta.map, catMeta.order),
        },
        inverse: true,
      },
      visualMap: {
        min: 0, max: 100, calculable: true, orient: 'vertical' as const,
        right: 0, top: 'center',
        inRange: { color: ['#1a9e5f', '#f5f5f4', '#d8392b'] },  // 绿(弱)→灰(中)→红(强)
        textStyle: { fontSize: 9 },
      },
      series: [{
        type: 'heatmap' as const,
        data: hmData,
        label: {
          show: true,
          fontSize: 7,
          formatter: (p: any) => {
            const ind = data.industries[p.data[1]];
            const v = p.data[2].toFixed(0);
            // 最新日异动格子: 大箭头+数字(箭头 12px 醒目)
            if (p.data[0] === data.dates.length - 1) {
              if (alerts.has(`${ind}|up`)) return `{arrowUp|▲}${v}`;
              if (alerts.has(`${ind}|down`)) return `{arrowDn|▼}${v}`;
            }
            return v;
          },
          color: '#333',
          rich: {
            // 亮红/亮绿 + 白描边: 与同色系渐变底色区分, 任何背景都醒目
            arrowUp: { color: '#ff2b2b', fontSize: 13, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
            arrowDn: { color: '#00c853', fontSize: 13, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
          },
        },
        emphasis: { itemStyle: { shadowBlur: 4, shadowColor: 'rgba(0,0,0,0.3)' } },
      }],
    };
    import('echarts').then(echarts => {
      const chart = echarts.init(ref.current!);
      chart.setOption(option);
      chart.on('click', (p: any) => {
        if (p.data) {
          // 点击格子: 选中行业 + 记录当日分数, 弹出均线面板 (不再直接跳详情)
          setSel({ ind: data.industries[p.data[1]], dt: data.dates[p.data[0]], score: p.data[2] });
        }
      });
      const ro = new ResizeObserver(() => chart.resize());
      ro.observe(ref.current!);
      return () => { ro.disconnect(); chart.dispose(); };
    });
  }, [data, alerts, catMeta]);

  if (loading) return <div className="text-gray-400 text-sm py-12 text-center">加载热力图…</div>;
  if (!data?.dates?.length) return <div className="text-gray-400 text-sm py-12 text-center">暂无历史数据</div>;

  // 点击选中行业后展示的"分数 + 均线"面板
  const selMa = sel ? (data.ma?.[sel.ind] || {}) : {};

  return (
    <Card title={`综合分热力图 — 最近 ${data.dates.length} 个交易日 × ${data.industries.length} 行业 (点击格子看分数与均线)`}>
      <CatStatCards items={catStats} scoreLabel="综合分" />
      <div className="text-xs text-gray-400 mb-2">
        红=强(综合分≥70) · 灰=中(50-70) · 绿=弱(&lt;50) · 行业按最新日综合分降序 · 综合分=SI 80% + 当日动能 20% · y轴色点=板块类别
      </div>
      <div ref={ref} style={{ width: '100%', height: 520 }} />

      {sel && (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3 flex items-center gap-6 flex-wrap">
          <div className="text-sm font-semibold text-gray-800">
            {sel.ind}
            <span className="ml-2 text-xs font-normal text-gray-400">{sel.dt}</span>
          </div>
          <div className="text-sm">
            当日综合分 <span className="font-bold text-gray-900 text-base">{sel.score.toFixed(1)}</span>
          </div>
          {([['ma5', '5日均'], ['ma20', '20日均'], ['ma60', '60日均']] as const).map(([k, label]) => {
            const v = selMa[k];
            const diff = v != null ? sel.score - v : null;
            return (
              <div key={k} className="text-sm text-gray-600">
                {label} <span className="font-semibold text-gray-800">{v != null ? v.toFixed(1) : '—'}</span>
                {diff != null && (
                  <span className={`ml-1 text-xs ${diff >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {diff >= 0 ? '+' : ''}{diff.toFixed(1)}
                  </span>
                )}
              </div>
            );
          })}
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => { onSelect(sel.ind); setSel(null); }}
              className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700">
              查看行业详情 →
            </button>
            <button
              onClick={() => setSel(null)}
              className="px-3 py-1 text-xs rounded border border-gray-300 text-gray-500 hover:bg-gray-100">
              关闭
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

// ─── 子页⑥: RPS 热力图 (行业相对强度, 日期 × 行业) ────────────────────────

function RpsHeatmapTab({ onSelect }: { onSelect: (ind: string) => void }) {
  const [data, setData] = useState<{ dates: string[]; industries: string[]; rps: Record<string, (number | null)[][]>; vol: (number | null)[][] } | null>(null);
  const [period, setPeriod] = useState<'当日' | '5日' | '20日'>('5日');
  const [loading, setLoading] = useState(true);
  const ref = useRef<HTMLDivElement>(null);
  const catMeta = useCatMeta();

  useEffect(() => {
    apiFetch<{ dates: string[]; industries: string[]; rps: Record<string, (number | null)[][]>; vol: (number | null)[][] }>(
      '/api/industry-trend/rps-heatmap?days=40')
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // 异动判断: 最新日 vs 昨日 且 偏离5日均值, 量比>1.5加粗
  const alerts = useMemo(() => {
    if (!data || data.dates.length < 2) return new Set<string>();
    const n = data.dates.length - 1;   // 最新日索引
    const prev = n - 1;                // 昨日索引
    const matrix = data.rps[period] || [];
    const volMatrix = data.vol || [];
    const out = new Set<string>();
    data.industries.forEach((ind, i) => {
      const now = matrix[i]?.[n];
      const yesterday = matrix[i]?.[prev];
      // 5日均值: 最近5天(含当日)的RPS均值
      const last5 = matrix[i]?.slice(Math.max(0, n - 4), n + 1).filter(v => v != null) || [];
      const avg5 = last5.length >= 3 ? last5.reduce((a, b) => a + b, 0) / last5.length : null;
      const vol = volMatrix[i]?.[n];
      if (now == null || yesterday == null || avg5 == null) return;
      const dayChg = now - yesterday;      // 日环比
      const dev5 = now - avg5;             // 偏离5日均值
      // 双条件同时满足才标箭头, 阈值按周期分档(波动性不同):
      // 当日: 环比±35 且 偏离±25 | 5日: 环比±15 且 偏离±12 | 20日: 环比±15 且 偏离±12
      const th = period === '当日' ? { chg: 35, dev: 25 } : { chg: 15, dev: 12 };
      if (Math.abs(dayChg) >= th.chg && Math.abs(dev5) >= th.dev) {
        const strong = vol != null && vol > 1.5;
        out.add(`${ind}|${dayChg > 0 ? 'up' : 'down'}${strong ? '|strong' : ''}`);
      }
    });
    return out;
  }, [data, period]);

  // 类别统计: 最新日各类别平均 RPS(当前周期) + 较前一日变化
  const catStats = useMemo<CatStatItem[]>(() => {
    if (!data || data.dates.length < 2) return [];
    const n = data.dates.length - 1, prev = n - 1;
    const matrix = data.rps[period] || [];
    const agg: Record<string, { n: number; sum: number; sn: number; chgSum: number; chgN: number }> = {};
    data.industries.forEach((ind, i) => {
      const cat = catMeta.map[ind];
      if (!cat) return;
      const a = agg[cat] ||= { n: 0, sum: 0, sn: 0, chgSum: 0, chgN: 0 };
      a.n++;
      const now = matrix[i]?.[n], y = matrix[i]?.[prev];
      if (now != null) { a.sum += now; a.sn++; }
      if (now != null && y != null) { a.chgSum += now - y; a.chgN++; }
    });
    return catMeta.order.map(cat => {
      const a = agg[cat] || { n: 0, sum: 0, sn: 0, chgSum: 0, chgN: 0 };
      return { cat, n: a.n, score: a.sn ? a.sum / a.sn : null, chg: a.chgN ? a.chgSum / a.chgN : null };
    }).sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }, [data, period, catMeta]);

  useEffect(() => {
    if (!ref.current || !data?.dates?.length) return;
    const matrix = data.rps[period] || [];
    const hmData: [number, number, number][] = [];
    matrix.forEach((row, i) => {
      row.forEach((v, j) => {
        if (v != null) hmData.push([j, i, v]);
      });
    });
    const option = {
      tooltip: {
        formatter: (p: any) => {
          const ind = data.industries[p.data[1]];
          const dt = data.dates[p.data[0]];
          const cat = catMeta.map[ind];
          return `${ind} | ${dt} | <b>${cat || '--'}</b><br/>${period} RPS: <b>${p.data[2].toFixed(1)}</b>`;
        },
      },
      grid: { left: 78, right: 60, top: 10, bottom: 40 },
      xAxis: {
        type: 'category' as const,
        data: data.dates.map(d => d.slice(5)),
        axisLabel: { fontSize: 8, rotate: 60, interval: 1 },
      },
      yAxis: {
        type: 'category' as const,
        data: data.industries,
        axisLabel: {
          fontSize: 9,
          rich: catAxisRich(catMeta.order),
          formatter: (name: string) => catAxisLabel(name, catMeta.map, catMeta.order),
        },
        inverse: true,
      },
      visualMap: {
        min: 0, max: 100, calculable: true, orient: 'vertical' as const,
        right: 0, top: 'center',
        inRange: { color: ['#1a9e5f', '#f5f5f4', '#d8392b'] },  // 绿(弱)→灰(中)→红(强)
        textStyle: { fontSize: 9 },
      },
      series: [{
        type: 'heatmap' as const,
        data: hmData,
        label: {
          show: true,
          fontSize: 7,
          formatter: (p: any) => {
            const ind = data.industries[p.data[1]];
            const v = p.data[2].toFixed(0);
            // 最新日异动格子: 大箭头+数字(箭头 12px, 量比>1.5 14px)
            if (p.data[0] === data.dates.length - 1) {
              const upStrong = alerts.has(`${ind}|up|strong`);
              const up = alerts.has(`${ind}|up`);
              const dnStrong = alerts.has(`${ind}|down|strong`);
              const dn = alerts.has(`${ind}|down`);
              if (upStrong) return `{arrowUpStrong|▲}${v}`;
              if (up) return `{arrowUp|▲}${v}`;
              if (dnStrong) return `{arrowDnStrong|▼}${v}`;
              if (dn) return `{arrowDn|▼}${v}`;
            }
            return v;
          },
          color: '#333',
          rich: {
            // 亮红/亮绿 + 白描边: 与同色系渐变底色区分, 任何背景都醒目
            arrowUp: { color: '#ff2b2b', fontSize: 13, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
            arrowUpStrong: { color: '#ff2b2b', fontSize: 15, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
            arrowDn: { color: '#00c853', fontSize: 13, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
            arrowDnStrong: { color: '#00c853', fontSize: 15, fontWeight: 'bold' as const, textBorderColor: '#ffffff', textBorderWidth: 2 },
          },
        },
        emphasis: { itemStyle: { shadowBlur: 4, shadowColor: 'rgba(0,0,0,0.3)' } },
      }],
    };
    import('echarts').then(echarts => {
      const chart = echarts.init(ref.current!);
      chart.setOption(option);
      chart.on('click', (p: any) => {
        if (p.data) onSelect(data.industries[p.data[1]]);
      });
      const ro = new ResizeObserver(() => chart.resize());
      ro.observe(ref.current!);
      return () => { ro.disconnect(); chart.dispose(); };
    });
  }, [data, period, alerts, catMeta]);

  if (loading) return <div className="text-gray-400 text-sm py-12 text-center">加载 RPS 热力图…</div>;
  if (!data?.dates?.length) return <div className="text-gray-400 text-sm py-12 text-center">暂无历史数据</div>;

  return (
    <Card title={`行业 RPS(相对强度)热力图 — 最近 ${data.dates.length} 个交易日 × ${data.industries.length} 行业 (点击行业看详情)`}>
      <CatStatCards items={catStats} scoreLabel={`${period}RPS`} />
      {/* 周期切换 */}
      <div className="flex items-center gap-1 mb-3 text-xs">
        <span className="text-gray-400 mr-1">周期:</span>
        {(['当日', '5日', '20日'] as const).map(p => (
          <button key={p} onClick={() => setPeriod(p)}
            className={`px-2.5 py-1 rounded-md transition-colors ${period === p
              ? 'bg-blue-600 text-white font-medium'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
            {p} RPS
          </button>
        ))}
      </div>
      <div className="text-xs text-gray-400 mb-2">
        RPS = 行业{period}累计涨幅在全市场 31 个行业中的排名百分位(0-100) · 红=强(RPS≥80) · 灰=中(50-80) · 绿=弱(&lt;50) · 行业按最新日 5日RPS 降序 · y轴色点=板块类别
      </div>
      <div ref={ref} style={{ width: '100%', height: 520 }} />
    </Card>
  );
}

// ─── 子页⑦: 类别轮动 (6 大板块类别 时序) ──────────────────────────────────

interface RotationPayload {
  dates: string[]; categories: string[];
  metrics: Record<string, Record<string, (number | null)[]>>;
  cat_counts: Record<string, number>;
}
const ROT_METRICS = ['综合分', '5日RPS', '20日RPS', '当日涨幅%'] as const;

function CategoryRotationTab() {
  const [data, setData] = useState<RotationPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [metric, setMetric] = useState<typeof ROT_METRICS[number]>('综合分');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiFetch<RotationPayload>('/api/industry-trend/category-rotation?days=60')
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  // 6 类别折线: 线色=类别色, 最新值最高的类别加粗
  useEffect(() => {
    if (!ref.current || !data?.dates?.length) return;
    const m = data.metrics[metric] || {};
    const last = (c: string) => {
      const arr = m[c] || [];
      return arr[arr.length - 1] ?? -Infinity;
    };
    const top = data.categories.reduce((a, b) => (last(a) >= last(b) ? a : b));
    // x 必须传完整日期 + tickformat: MM-DD 字符串会被 Plotly 误判成 date 轴且年份解析错乱→断线
    const traces = data.categories.map(c => ({
      x: data.dates,
      y: m[c] || [],
      name: c,
      mode: 'lines' as const,
      line: { color: CAT_STYLE[c]?.color || '#999', width: c === top ? 3 : 1.5 },
      hovertemplate: '%{x|%m-%d}<br>' + c + ': <b>%{y}</b><extra></extra>',
    }));
    const layout = {
      margin: { l: 45, r: 15, t: 35, b: 30 },
      legend: { orientation: 'h' as const, y: 1.12, font: { size: 11 } },
      yaxis: { title: { text: metric, font: { size: 11 } }, zeroline: metric === '当日涨幅%', gridcolor: '#eee' },
      xaxis: { tickformat: '%m-%d', tickfont: { size: 9 }, tickangle: -45, nticks: 12 },
      font: FONT, height: 400,
    };
    Plotly.react(ref.current, traces as any, layout as any, PLOT_CFG);
  }, [data, metric]);

  // 最近 12 个交易日占优排名: 行=日期(新在上), 列=名次1-6
  const rankRows = useMemo(() => {
    if (!data?.dates?.length) return [];
    const m = data.metrics[metric] || {};
    const last12 = data.dates.slice(-12).reverse();
    return last12.map(d => {
      const j = data.dates.indexOf(d);
      const ranked = data.categories
        .map(c => ({ cat: c, v: (m[c] || [])[j] }))
        .filter(x => x.v != null)
        .sort((a, b) => (b.v as number) - (a.v as number));
      return { date: d, ranked };
    });
  }, [data, metric]);

  const RANK_BG = ['bg-red-100 text-red-800', 'bg-red-50 text-red-700', 'bg-orange-50 text-orange-700',
    'bg-gray-50 text-gray-600', 'bg-green-50 text-green-700', 'bg-green-100 text-green-800'];

  if (loading) return <div className="text-gray-400 text-sm py-12 text-center">加载类别轮动…</div>;
  if (!data?.dates?.length) return <div className="text-gray-400 text-sm py-12 text-center">暂无历史数据</div>;

  return (
    <Card title={`类别轮动 — 6 大板块 × 最近 ${data.dates.length} 个交易日 (指标可切换)`}>
      <div className="flex items-center gap-1 mb-3 text-xs">
        <span className="text-gray-400 mr-1">指标:</span>
        {ROT_METRICS.map(mt => (
          <button key={mt} onClick={() => setMetric(mt)}
            className={`px-2.5 py-1 rounded-md transition-colors ${metric === mt
              ? 'bg-blue-600 text-white font-medium'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
            {mt}
          </button>
        ))}
      </div>
      <div className="text-xs text-gray-400 mb-2">
        折线=各类别内行业均值 · 加粗=当前占优类别 · 综合分/当日涨幅 为类别内行业平均; RPS = 行业内排名百分位(0-100)再按类别平均 · 50 为强弱分界
      </div>
      <div ref={ref} />
      {/* 最近 12 日占优排名表 */}
      <div className="mt-4">
        <div className="text-xs font-medium text-gray-600 mb-1.5">最近 12 个交易日占优排名 (1=最强 → 6=最弱, 看排名变化=轮动)</div>
        <div className="overflow-x-auto">
          <table className="text-xs whitespace-nowrap">
            <thead><tr className="text-gray-500 border-b">
              <th className="py-1 px-2 text-left">日期</th>
              {[1, 2, 3, 4, 5, 6].map(i => <th key={i} className="py-1 px-2 text-left">#{i}</th>)}
            </tr></thead>
            <tbody>
              {rankRows.map(r => (
                <tr key={r.date} className="border-b border-gray-100">
                  <td className="py-1 px-2 text-gray-600">{r.date.slice(5)}</td>
                  {r.ranked.map((x, i) => (
                    <td key={x.cat} className={`py-1 px-2 rounded ${RANK_BG[i] || ''}`}>
                      {x.cat} <span className="text-[10px] opacity-70">{(x.v as number).toFixed(0)}</span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

// ─── 子页④: 行业详情 (K 线 + 均线 + 位置 + 时序) ──────────────────────────

function IndustryDetailTab({ industry, date, onClose }: {
  industry: string; date: string; onClose: () => void;
}) {
  const [series, setSeries] = useState<SeriesPoint[] | null>(null);
  const seriesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSeries(null);
    if (!industry) return;
    apiFetch<SeriesPoint[]>(`/api/industry-trend/series?industry=${encodeURIComponent(industry)}&days=60`)
      .then(setSeries)
      .catch(() => setSeries([]));
  }, [industry]);

  // 行业时序 (从存档聚合)
  useEffect(() => {
    if (!seriesRef.current || !series?.length) return;
    const d = series.map(p => p.date.slice(5));
    Plotly.react(seriesRef.current, [
      { x: d, y: series.map(p => p['资金流Δ']), name: '资金流Δ pp', type: 'scatter' as const, mode: 'lines+markers', line: { color: UP, width: 2 } },
      { x: d, y: series.map(p => p['量能比5']), name: '量能比5', type: 'scatter' as const, mode: 'lines+markers', yaxis: 'y2', line: { color: '#534AB7', width: 1.5 } },
      { x: d, y: series.map(p => p['广度%']), name: '广度%', type: 'scatter' as const, mode: 'lines+markers', yaxis: 'y2', line: { color: '#CE9B54', width: 1.5, dash: 'dot' } },
    ], {
      font: FONT, height: 260, margin: { l: 46, r: 46, t: 10, b: 30 },
      showlegend: true, legend: { orientation: 'h' as const, y: 1.12, font: { size: 9 } },
      xaxis: { gridcolor: '#eee' },
      yaxis: { title: '资金流Δ', gridcolor: '#eee', zeroline: true, zerolinecolor: '#ccc' },
      yaxis2: { overlaying: 'y', side: 'right', title: '量能比5 / 广度%', gridcolor: 'transparent' },
    } as any, PLOT_CFG);
    return () => { if (seriesRef.current) Plotly.purge(seriesRef.current); };
  }, [series]);

  if (!industry) {
    return (
      <div className="bg-white border rounded-lg p-10 text-center text-sm text-gray-400">
        还没有选中行业 — 去「明细排序」子页点一个行业行, 这里就会展示它的 K 线(均线+1/3/5年位置)和关键指标时序。
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card title={`K 线 — ${industry} (申万官方指数 · MA5/20/60/120 · 位置分位条点一下切换周期高低点线)`}>
        <IndustryKline industry={industry} date={date} onClose={onClose} />
      </Card>
      <Card title={`行业时序 — ${industry} (资金流Δ / 量能比5 / 广度%, 从历史存档聚合)`}>
        {series && series.length > 1 ? (
          <div ref={seriesRef} className="w-full" />
        ) : (
          <div className="text-gray-400 text-sm py-3 text-center">
            {series && series.length === 1 ? '存档只有 1 天, 无时序可画 — 回填更多历史日期后自动可用' : '加载中…'}
          </div>
        )}
      </Card>
    </div>
  );
}

// ─── 小组件 ───────────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, valueSmall }: { label: string; value: string; sub?: string; valueSmall?: boolean }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`font-medium ${valueSmall ? 'text-sm' : 'text-lg'} text-gray-900 leading-tight`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border rounded-lg p-3">
      <div className="text-sm font-medium text-gray-700 mb-2">{title}</div>
      {children}
    </div>
  );
}
