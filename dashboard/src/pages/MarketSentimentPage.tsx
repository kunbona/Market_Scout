import { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Zap, ArrowUpRight, ArrowDownRight } from 'lucide-react';

// ─── Types ───────────────────────────────────────────────────────────────────

interface MarketEmotion {
  trade_date: string;
  zt_total: number;
  dt_total: number;
  zb_total: number;
  max_lianzban: number;
  zt_yesterday_premium: number;
  zb_rate: number;
}

interface LianzbanStats {
  trade_date: string;
  tier_1: number;
  tier_2: number;
  tier_3: number;
  tier_4plus: number;
  advance_1to2: number;
  advance_2to3: number;
  advance_3to4: number;
}

interface MarketPulse {
  fetch_time: string;
  zt_count: number;
  dt_count: number;
  real_zt: number | null;
  activity: number | null;
}

interface SectorDensity {
  industry: string;
  zt_count: number;
  zt_density: number;
  max_lianzban: number;
}

interface ConceptZt {
  concept: string;
  zt_count: number;
}

interface LianzbanItem {
  stock_code: string;
  stock_name: string;
  industry: string;
  lianzban_cnt: number;
  is_zb: number;
}

// ─── Formatters ──────────────────────────────────────────────────────────────

const fmt = (v: number | null | undefined, suffix = '') =>
  v != null ? `${v}${suffix}` : '--';

const fmtPct = (v: number | null | undefined, scale = 100) =>
  v != null ? `${(v * scale).toFixed(2)}%` : '--';

const fmtNum = (v: number | null | undefined, digits = 2, suffix = '') =>
  v != null ? `${v.toFixed(digits)}${suffix}` : '--';

// ─── API ─────────────────────────────────────────────────────────────────────

const BASE = '';

async function safeApiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`);
    const json = await res.json();
    if (!json.success) return null;
    return json.data as T;
  } catch {
    return null;
  }
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
  loading,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  accent?: 'red' | 'green' | 'amber' | 'blue' | 'neutral';
  loading?: boolean;
}) {
  const accentMap = {
    red: 'text-red-600',
    green: 'text-emerald-600',
    amber: 'text-amber-500',
    blue: 'text-blue-600',
    neutral: 'text-gray-900',
  };
  const iconBgMap = {
    red: 'bg-red-50 text-red-500',
    green: 'bg-emerald-50 text-emerald-500',
    amber: 'bg-amber-50 text-amber-500',
    blue: 'bg-blue-50 text-blue-500',
    neutral: 'bg-gray-100 text-gray-500',
  };
  const colorClass = accentMap[accent ?? 'neutral'];
  const iconClass = iconBgMap[accent ?? 'neutral'];

  return (
    <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500 font-medium">{label}</span>
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${iconClass}`}>
          <Icon className="w-3.5 h-3.5" />
        </div>
      </div>
      {loading ? (
        <div className="h-7 bg-gray-100 rounded animate-pulse w-2/3" />
      ) : (
        <div className={`text-2xl font-semibold tracking-tight ${colorClass}`}>{value}</div>
      )}
      {sub && (
        <div className="text-xs text-gray-500 leading-snug">{loading ? <span className="text-gray-300">…</span> : sub}</div>
      )}
    </div>
  );
}


function TierBar({
  label,
  count,
  max,
  color,
  loading,
}: {
  label: string;
  count: number | undefined;
  max: number;
  color: string;
  loading?: boolean;
}) {
  const pct = max > 0 && count != null ? Math.min((count / max) * 100, 100) : 0;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">{label}</span>
        {loading ? (
          <div className="h-4 w-8 bg-gray-100 rounded animate-pulse" />
        ) : (
          <span className="text-sm font-semibold text-gray-900">{count ?? '--'}</span>
        )}
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${color}`}
          style={{ width: loading ? '0%' : `${pct}%` }}
        />
      </div>
    </div>
  );
}

function SectionHeader({ title, badge }: { title: string; badge?: string }) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
      <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
      {badge && (
        <span className="text-xs text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full border border-gray-200">
          {badge}
        </span>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export function MarketSentimentPage() {
  const [emotion, setEmotion] = useState<MarketEmotion | null>(null);
  const [lbStats, setLbStats] = useState<LianzbanStats | null>(null);
  const [pulse, setPulse] = useState<MarketPulse | null>(null);
  const [sectors, setSectors] = useState<SectorDensity[] | null>(null);
  const [concepts, setConcepts] = useState<ConceptZt[] | null>(null);
  const [chains, setChains] = useState<LianzbanItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [adData, setAdData] = useState<any>(null);
  const [toStats, setToStats] = useState<any>(null);
  const [mcData, setMcData] = useState<any>(null);
  const [sfaData, setSfaData] = useState<any[]>([]);
  const [vbData, setVbData] = useState<any[]>([]);

  useEffect(() => {
    setLoading(true);
    Promise.allSettled([
      safeApiFetch<MarketEmotion>('/api/market-emotion'),
      safeApiFetch<MarketPulse[]>('/api/market-pulse?n=1'),
      safeApiFetch<SectorDensity[]>('/api/sector-zt-density'),
      safeApiFetch<ConceptZt[]>('/api/concept-zt-density?top_n=15'),
      safeApiFetch<LianzbanItem[]>('/api/lianzban-chain'),
      safeApiFetch<any>('/api/advance-decline'),
      safeApiFetch<any>('/api/turnover-stats'),
      safeApiFetch<any>('/api/market-cap-dist'),
      safeApiFetch<any[]>('/api/sector-flow-accel'),
      safeApiFetch<any[]>('/api/volume-breakout'),
      safeApiFetch<LianzbanStats[]>('/api/lianzban-stats?days=30'),
    ]).then(([e, p, s, c, l, ad, to, mc, sfa, vb, lb]) => {
      // 空对象 {} 视为无数据，统一转 null，避免字段访问得到 undefined
      const nonEmpty = (v: any) => (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length > 0 ? v : null);
      if (e.status === 'fulfilled') setEmotion(nonEmpty(e.value) as MarketEmotion | null);
      if (p.status === 'fulfilled') {
        const arr = p.value;
        if (Array.isArray(arr) && arr.length > 0) setPulse(arr[0]);
      }
      if (s.status === 'fulfilled') {
        const raw = s.value ?? [];
        setSectors([...raw].sort((a, b) => b.zt_count - a.zt_count));
      }
      if (c.status === 'fulfilled') setConcepts(c.value ?? []);
      if (l.status === 'fulfilled') {
        const raw = l.value ?? [];
        setChains([...raw].sort((a, b) => b.lianzban_cnt - a.lianzban_cnt));
      }
      if (ad.status === 'fulfilled') setAdData(nonEmpty(ad.value));
      if (to.status === 'fulfilled') setToStats(nonEmpty(to.value));
      if (mc.status === 'fulfilled') setMcData(nonEmpty(mc.value));
      if (sfa.status === 'fulfilled') setSfaData(sfa.value ?? []);
      if (vb.status === 'fulfilled') setVbData(vb.value ?? []);
      if (lb.status === 'fulfilled') {
        const arr = lb.value;
        if (Array.isArray(arr) && arr.length > 0) setLbStats(arr[arr.length - 1]);
      }
      setLoading(false);
    });
  }, []);

  // Derived display values
  const premiumPct = emotion ? (emotion.zt_yesterday_premium * 100).toFixed(2) : null;
  const premiumPositive = emotion ? emotion.zt_yesterday_premium >= 0 : null;
  const zbRatePct = emotion ? (emotion.zb_rate * 100).toFixed(1) : null;
  const advance1to2Pct = lbStats ? Math.round(lbStats.advance_1to2 * 100) : null;

  const tierMax = lbStats
    ? Math.max(lbStats.tier_1, lbStats.tier_2, lbStats.tier_3, lbStats.tier_4plus, 1)
    : 1;

  const sortedChains = chains ?? [];
  const sortedSectors = sectors ?? [];
  const sortedConcepts = concepts ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="pb-4 border-b border-gray-200">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-semibold text-gray-900 mb-1">市场情绪</h2>
            <div className="flex items-center gap-3 text-sm text-gray-500">
              <span>A股情绪面板</span>
              {emotion?.trade_date && (
                <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs font-mono">
                  {emotion.trade_date}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── 无数据提示 Banner ── */}
      {!loading && !emotion && !adData && !toStats && !mcData && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
          <span className="text-lg leading-none mt-0.5">⚠️</span>
          <div className="space-y-1">
            <div className="font-medium">本地日线数据尚未生成</div>
            <div className="text-amber-700 leading-relaxed">
              情绪分析区依赖本地量价数据（<code className="bg-amber-100 px-1 rounded text-xs">QUANT_DATA_ROOT</code>），
              请先确认路径已正确配置，然后点击上方「⚡ 计算今日数据」按钮生成指标。
            </div>
          </div>
        </div>
      )}

      {/* ── Row 1: 关键指标卡片 ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          icon={TrendingUp}
          label="涨停 / 跌停"
          value={
            emotion
              ? `${emotion.zt_total} / ${emotion.dt_total}`
              : '--'
          }
          sub={`非一字涨停：${fmt(pulse?.real_zt)}只`}
          accent="red"
          loading={loading}
        />
        <StatCard
          icon={premiumPositive === false ? ArrowDownRight : ArrowUpRight}
          label="昨日涨停溢价"
          value={premiumPct != null ? `${premiumPositive ? '+' : ''}${premiumPct}%` : '--'}
          sub={premiumPositive === null ? undefined : premiumPositive ? '溢价为正，情绪延续' : '溢价转负，注意风险'}
          accent={premiumPositive === null ? 'neutral' : premiumPositive ? 'red' : 'green'}
          loading={loading}
        />
        <StatCard
          icon={Zap}
          label="最高连板"
          value={emotion ? `${emotion.max_lianzban}板` : '--'}
          sub={`1→2晋级率：${advance1to2Pct != null ? `${advance1to2Pct}%` : '--'}`}
          accent="amber"
          loading={loading}
        />
        <StatCard
          icon={TrendingDown}
          label="炸板率"
          value={zbRatePct != null ? `${zbRatePct}%` : '--'}
          sub={`炸板数：${fmt(emotion?.zb_total)}`}
          accent="blue"
          loading={loading}
        />
      </div>

      {/* ── Row 1b: 新增 KPI 卡片 ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {/* 活跃度 A/D */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-4">
          <div className="text-xs text-gray-500 mb-1">活跃度（A/D比）</div>
          <div className="text-2xl text-gray-900 mb-1" style={{color: adData ? (adData.ad_ratio >= 1.5 ? '#16a34a' : adData.ad_ratio >= 0.8 ? '#f59e0b' : '#ef4444') : undefined}}>
            {loading ? '--' : adData ? adData.ad_ratio?.toFixed(2) : '--'}
          </div>
          <div className="text-xs text-gray-600">涨{adData?.advance_count ?? '--'} / 跌{adData?.decline_count ?? '--'}</div>
        </div>

        {/* 成交额/MA20 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-4">
          <div className="text-xs text-gray-500 mb-1">全市场成交额/MA20</div>
          <div className="text-2xl text-gray-900 mb-1" style={{color: adData ? (adData.amount_ratio >= 1.2 ? '#16a34a' : adData.amount_ratio >= 0.8 ? '#f59e0b' : '#ef4444') : undefined}}>
            {loading ? '--' : fmtNum(adData?.total_amount, 0, '亿')}
          </div>
          <div className="text-xs text-gray-600">{loading ? '' : fmtNum(adData?.amount_ratio, 2, 'x')}</div>
        </div>

        {/* 涨停换手中位 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-4">
          <div className="text-xs text-gray-500 mb-1">涨停换手中位</div>
          <div className="text-2xl text-gray-900 mb-1">
            {loading ? '--' : fmtNum(toStats?.median_to, 1, '%')}
          </div>
          <div className="text-xs text-gray-600">
            {loading ? '' : toStats ? (() => { const t = (toStats.high_count||0)+(toStats.mid_count||0)+(toStats.low_count||0); return t > 0 ? `高换手占${(toStats.high_count/t*100).toFixed(0)}%` : '--'; })() : '--'}
          </div>
        </div>

        {/* 市值偏好 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-4">
          <div className="text-xs text-gray-500 mb-1">涨停市值偏好（中/小）</div>
          <div className="text-2xl text-gray-900 mb-1">
            {loading ? '--' : fmtNum(mcData?.mid_pct != null ? mcData.mid_pct * 100 : null, 0, '%')}
            {!loading && mcData?.small_pct != null && <span className="text-base text-gray-400"> / {(mcData.small_pct * 100).toFixed(0)}%</span>}
          </div>
        </div>
      </div>

      {/* ── Row 2: 连板梯队 + 次级指标 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 连板梯队 */}
        <div className="widget-card bg-white rounded-xl border border-gray-100 overflow-hidden">
          <SectionHeader title="连板梯队" badge="今日" />
          <div className="p-5 space-y-4">
            <TierBar
              label="首板"
              count={lbStats?.tier_1}
              max={tierMax}
              color="bg-red-400"
              loading={loading}
            />
            <TierBar
              label="2板"
              count={lbStats?.tier_2}
              max={tierMax}
              color="bg-orange-400"
              loading={loading}
            />
            <TierBar
              label="3板"
              count={lbStats?.tier_3}
              max={tierMax}
              color="bg-amber-400"
              loading={loading}
            />
            <TierBar
              label="4板+"
              count={lbStats?.tier_4plus}
              max={tierMax}
              color="bg-yellow-300"
              loading={loading}
            />
            {/* 晋级率行 */}
            {!loading && lbStats && (
              <div className="pt-2 border-t border-gray-100 grid grid-cols-3 gap-2">
                {[
                  { label: '1→2', val: lbStats.advance_1to2 },
                  { label: '2→3', val: lbStats.advance_2to3 },
                  { label: '3→4', val: lbStats.advance_3to4 },
                ].map((r) => (
                  <div key={r.label} className="text-center">
                    <div className="text-xs text-gray-400 mb-1">{r.label} 晋级率</div>
                    <div className="text-sm font-semibold text-gray-700">
                      {fmtPct(r.val, 100)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 换手率分层 */}
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm text-gray-900 mb-4 font-medium">
            涨停换手分层
            <span className="text-xs text-gray-400 font-normal ml-2">中位 {toStats?.median_to?.toFixed(1) ?? '--'}%</span>
          </h3>
          {loading ? <div className="text-sm text-gray-400">--</div> : toStats ? (
            [['低(&lt;5%)', toStats.low_count, '#22c55e'], ['中(5-20%)', toStats.mid_count, '#f59e0b'], ['高(≥20%)', toStats.high_count, '#ef4444']].map(([label, count, color], i) => {
              const total = (toStats.low_count||0) + (toStats.mid_count||0) + (toStats.high_count||0);
              const pct = total > 0 ? (count as number) / total * 100 : 0;
              return (
                <div key={i} className="flex items-center gap-3 py-1.5">
                  <span className="text-xs text-gray-500 w-20" dangerouslySetInnerHTML={{__html: label as string}} />
                  <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                    <div className="h-1.5 rounded-full transition-all" style={{width:`${pct}%`, background: color as string}} />
                  </div>
                  <span className="text-xs font-mono text-gray-700 w-6 text-right">{count as number}</span>
                </div>
              );
            })
          ) : <div className="text-sm text-gray-400">暂无数据</div>}
        </div>
      </div>

      {/* ── Row 3: 行业密度 + 概念热度 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 行业涨停密度 */}
        <div className="widget-card bg-white rounded-xl border border-gray-100 overflow-hidden">
          <SectionHeader title="行业涨停密度" badge={`共 ${sortedSectors.length} 个行业`} />
          {loading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-8 bg-gray-50 rounded animate-pulse" />
              ))}
            </div>
          ) : sortedSectors.length === 0 ? (
            <div className="p-6 text-center text-sm text-gray-400">暂无数据</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {sortedSectors.map((item, idx) => (
                <div
                  key={item.industry}
                  className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors group"
                >
                  <span className="text-xs text-gray-400 w-5 shrink-0 text-right">{idx + 1}</span>
                  <span className="text-sm text-gray-800 flex-1 truncate">{item.industry}</span>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-xs text-gray-500">
                      {(item.zt_density * 100).toFixed(1)}%
                    </span>
                    <span className="text-sm font-semibold text-red-600 w-8 text-right">
                      {item.zt_count}只
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 概念涨停热度 */}
        <div className="widget-card bg-white rounded-xl border border-gray-100 overflow-hidden">
          <SectionHeader title="概念涨停热度" badge="Top 15" />
          {loading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-8 bg-gray-50 rounded animate-pulse" />
              ))}
            </div>
          ) : sortedConcepts.length === 0 ? (
            <div className="p-6 text-center text-sm text-gray-400">暂无数据</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {sortedConcepts.map((item, idx) => {
                const maxCnt = sortedConcepts[0]?.zt_count ?? 1;
                const barPct = (item.zt_count / maxCnt) * 100;
                return (
                  <div
                    key={item.concept}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-xs text-gray-400 w-5 shrink-0 text-right">{idx + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm text-gray-800 truncate">{item.concept}</span>
                        <span className="text-sm font-semibold text-blue-600 ml-2 shrink-0">
                          {item.zt_count}只
                        </span>
                      </div>
                      <div className="h-1 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-blue-300 rounded-full"
                          style={{ width: `${barPct}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 4: 连板链条 ── */}
      <div className="widget-card bg-white rounded-xl border border-gray-100 overflow-hidden">
        <SectionHeader
          title="连板链条"
          badge={loading ? '加载中…' : `共 ${sortedChains.length} 只`}
        />
        {loading ? (
          <div className="p-5 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-9 bg-gray-50 rounded animate-pulse" />
            ))}
          </div>
        ) : sortedChains.length === 0 ? (
          <div className="p-6 text-center text-sm text-gray-400">暂无数据</div>
        ) : (
          <div className="p-4">
            {/* Group by lianzban_cnt descending */}
            <div className="space-y-2">
              {sortedChains.map((item) => {
                const isZb = item.is_zb === 1;
                const label = `连${item.lianzban_cnt}${isZb ? '(炸)' : ''}`;
                return (
                  <div
                    key={item.stock_code}
                    className="flex items-center gap-3 p-2 rounded-lg hover:bg-gray-50 transition-colors group"
                  >
                    <span
                      className={`shrink-0 px-2 py-1 rounded text-xs font-semibold border font-mono ${
                        isZb
                          ? 'bg-orange-50 text-orange-600 border-orange-200'
                          : item.lianzban_cnt >= 5
                          ? 'bg-red-50 text-red-600 border-red-200'
                          : item.lianzban_cnt >= 3
                          ? 'bg-amber-50 text-amber-600 border-amber-200'
                          : 'bg-gray-50 text-gray-600 border-gray-200'
                      }`}
                    >
                      {label}
                    </span>
                    <span className="text-sm font-medium text-gray-900 w-24 truncate">
                      {item.stock_name}
                    </span>
                    <span className="text-xs text-gray-400 flex-1 truncate">{item.industry}</span>
                    <span className="text-xs text-gray-400 shrink-0 font-mono">{item.stock_code}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── Row 5: 涨停市值分布 ── */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="text-sm text-gray-900 mb-4 font-medium">涨停市值分布</h3>
        {loading ? <div className="text-sm text-gray-400">--</div> : mcData ? (
          <div className="grid grid-cols-3 gap-3">
            {([['&lt;50亿', mcData.small_count, mcData.small_pct, '#ef4444'], ['50-300亿', mcData.mid_count, mcData.mid_pct, '#f97316'], ['≥300亿', mcData.large_count, mcData.large_pct, '#3b82f6']] as [string,number,number,string][]).map(([label,count,pct,color],i) => (
              <div key={i} className="text-center p-3 border border-gray-100 rounded-lg">
                <div className="text-xs text-gray-400 mb-1" dangerouslySetInnerHTML={{__html:label}} />
                <div className="text-xl font-bold" style={{color}}>{count ?? '--'}</div>
                <div className="text-xs text-gray-400">{pct != null ? `${(pct*100).toFixed(0)}%` : '--'}</div>
              </div>
            ))}
          </div>
        ) : <div className="text-sm text-gray-400">暂无数据</div>}
      </div>

      {/* ── Row 6: 机构资金加速度 + 成交异动 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">机构资金加速度 <span className="text-xs text-gray-400 font-normal">3d/20d</span></h3>
          </div>
          <div className="p-4 space-y-1">
            {loading ? <div className="text-sm text-gray-400">--</div> : sfaData.length > 0 ? sfaData.slice(0,10).map((r,i) => {
              const col = (r.acceleration??0) >= 1.5 ? '#16a34a' : (r.acceleration??0) >= 1.0 ? '#f59e0b' : '#ef4444';
              return (
                <div key={i} className="flex items-center justify-between py-1.5 hover:bg-gray-50 rounded px-2">
                  <span className="text-sm text-gray-900">{r.industry}</span>
                  <span className="text-sm font-mono font-semibold" style={{color:col}}>{r.acceleration != null ? r.acceleration.toFixed(2)+'x' : '--'}</span>
                </div>
              );
            }) : <div className="text-sm text-gray-400 text-center py-4">暂无数据</div>}
          </div>
        </div>

        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">成交异动 <span className="text-xs text-gray-400 font-normal">5d/20d放量</span></h3>
          </div>
          <div className="p-4 space-y-1">
            {loading ? <div className="text-sm text-gray-400">--</div> : vbData.length > 0 ? vbData.slice(0,15).map((r,i) => {
              const col = (r.ratio_5_20??0) >= 3 ? '#ef4444' : (r.ratio_5_20??0) >= 2 ? '#f59e0b' : '#6b7280';
              return (
                <div key={i} className="flex items-center gap-2 py-1.5 hover:bg-gray-50 rounded px-2">
                  <span className="text-sm text-gray-900 flex-1 truncate">{r.stock_name}</span>
                  <span className="text-xs text-gray-400 shrink-0">{r.industry}</span>
                  <span className="text-sm font-mono font-semibold shrink-0" style={{color:col}}>{r.ratio_5_20 != null ? r.ratio_5_20.toFixed(1)+'x' : '--'}</span>
                  <span className="text-xs text-gray-400 w-14 text-right shrink-0">{r.amount_5d != null ? (r.amount_5d/1e8).toFixed(2)+'亿' : '--'}</span>
                </div>
              );
            }) : <div className="text-sm text-gray-400 text-center py-4">暂无数据</div>}
          </div>
        </div>
      </div>

      {/* ── Bottom padding ── */}
      <div className="h-4" />
    </div>
  );
}
