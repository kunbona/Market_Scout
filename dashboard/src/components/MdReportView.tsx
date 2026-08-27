import type { ReactNode } from 'react';
import { Brain, FileText, TrendingUp, Zap } from 'lucide-react';

// ─── 共享类型与渲染：Markdown 型 AI 报告（review_ai / master_view 等）共用 ───

export interface ReviewKpis {
  limit_up?: number;
  limit_down?: number;
  limit_up_trend?: Array<{ date: string; count: number }>;
  limit_up_sectors?: Array<{ sector: string; count: number }>;
  total_amount_yi?: number;
  amount_ratio?: number | null;
  main_net_yi?: number;
  retail_net_yi?: number;
  up_count?: number;
  down_count?: number;
  // master_view 附加的行业趋势 KPI
  trend_headline?: string | null;
  trend_cat_counts?: Record<string, number> | null;
  trend_upgrades?: number;
  trend_downgrades?: number;
  trend_resonance?: string[] | null;
}

export interface MdReport {
  run_type?: string;
  run_time?: string;
  summary_time?: string;
  id?: number;
  has_html?: boolean;
  analysis_md: string;
  trade_date?: string;
  kpis?: ReviewKpis;
  /** 本次分析实际使用的数据源清单 (master_view 落库的 digest_used.sources) */
  sources?: string[];
  /** 报告标题（默认"复盘 AI 总结"） */
  report_title?: string;
}

// 数据源 key → 中文标签
const SOURCE_LABEL: Record<string, string> = {
  industry_trend: '行业趋势',
  review: '当日复盘',
  dm_kun_analysis: 'DM专题×6',
  prior_ai_views: '既有AI结论',
  sector_flow_latest: '板块资金流',
  wisburg_views: '智堡海外视角',
  lhb: '龙虎榜',
  concept_flow: '概念资金流',
  lockup_calendar: '解禁日历',
};

// ─── Markdown inline 加粗解析 ────────────────────────────────────────────────

export function renderMdInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**') && p.length > 4
      ? <strong key={i} className="font-semibold text-gray-900">{p.slice(2, -2)}</strong>
      : <span key={i}>{p}</span>
  );
}

// ─── KPI Cards (图文并茂) ────────────────────────────────────────────────────

export function KpiCard({ label, value, unit, tone, sub }: {
  label: string; value: string; unit?: string; tone?: 'up' | 'down' | 'neutral'; sub?: string;
}) {
  const toneCls = tone === 'up' ? 'text-red-600' : tone === 'down' ? 'text-green-600' : 'text-gray-900';
  return (
    <div className="bg-white rounded-xl border border-gray-100 px-4 py-3 shadow-[var(--shadow-sm)]">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className={`text-lg font-bold leading-tight ${toneCls}`}>
        {value}{unit && <span className="text-xs font-medium text-gray-400 ml-0.5">{unit}</span>}
      </div>
      {sub && <div className="text-[11px] text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

export function fmtYi(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return '--';
  if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2);
  return v.toFixed(0);
}
export function yiUnit(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return '';
  return Math.abs(v) >= 10000 ? '万亿' : '亿';
}

export function ReviewKpiSection({ k }: { k: ReviewKpis }) {
  const up = k.up_count ?? 0;
  const down = k.down_count ?? 0;
  const total = up + down;
  const upPct = total > 0 ? Math.round((up / total) * 100) : 0;

  const sectors = (k.limit_up_sectors ?? []).filter(s => s.count > 0);
  const maxSec = sectors.length ? Math.max(...sectors.map(s => s.count)) : 1;
  const trend = (k.limit_up_trend ?? []).slice(-6);
  const maxTrend = trend.length ? Math.max(...trend.map(t => t.count), 1) : 1;
  const trendW = 320, trendH = 88;

  const hasTrend = k.trend_headline != null;

  return (
    <div className="space-y-4">
      {/* 行业趋势 headline（master_view 专属, 有则显示） */}
      {hasTrend && (
        <div className="bg-gradient-to-r from-indigo-50 to-blue-50 rounded-xl border border-indigo-100 px-5 py-4">
          <div className="flex items-center gap-2 mb-1.5">
            <TrendingUp className="w-4 h-4 text-indigo-500" />
            <span className="text-xs font-semibold text-indigo-700">行业趋势全景</span>
          </div>
          <p className="text-sm font-bold text-indigo-900">{k.trend_headline}</p>
          {(k.trend_resonance?.length ?? 0) > 0 && (
            <p className="text-xs text-indigo-500 mt-1">
              趋势共振行业：{k.trend_resonance!.join('、')}
            </p>
          )}
        </div>
      )}

      {/* KPI 卡片行 */}
      <div className="grid grid-cols-4 gap-3">
        <KpiCard label="涨停" value={k.limit_up != null ? String(k.limit_up) : '--'} unit="家" tone="up" />
        <KpiCard label="跌停" value={k.limit_down != null ? String(k.limit_down) : '--'} unit="家" tone="down" />
        <KpiCard
          label="主力净流入" value={(k.main_net_yi ?? 0) >= 0 ? `+${fmtYi(k.main_net_yi)}` : fmtYi(k.main_net_yi)}
          unit={yiUnit(k.main_net_yi)} tone={(k.main_net_yi ?? 0) >= 0 ? 'up' : 'down'} />
        <KpiCard
          label="散户净流入" value={(k.retail_net_yi ?? 0) >= 0 ? `+${fmtYi(k.retail_net_yi)}` : fmtYi(k.retail_net_yi)}
          unit={yiUnit(k.retail_net_yi)} tone={(k.retail_net_yi ?? 0) >= 0 ? 'up' : 'down'} />
        <KpiCard label="成交额" value={fmtYi(k.total_amount_yi)} unit={yiUnit(k.total_amount_yi)}
          sub={k.amount_ratio != null ? `量比 ${k.amount_ratio}` : undefined} />
        <KpiCard label="上涨家数" value={up ? String(up) : '--'} tone="up" />
        <KpiCard label="下跌家数" value={down ? String(down) : '--'} tone="down" />
        <KpiCard label="涨跌比" value={total > 0 ? `${upPct}% : ${100 - upPct}%` : '--'} tone="up" />
      </div>

      {/* 涨跌家数红绿比例条 */}
      {total > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 px-4 py-3 shadow-[var(--shadow-sm)]">
          <div className="flex justify-between text-xs mb-1.5">
            <span className="text-red-600 font-semibold">↑ 上涨 {up}</span>
            <span className="text-green-600 font-semibold">下跌 {down} ↓</span>
          </div>
          <div className="h-2.5 rounded-full overflow-hidden flex bg-gray-100">
            <div className="bg-red-500 h-full" style={{ width: `${upPct}%` }} />
            <div className="bg-green-500 h-full" style={{ width: `${100 - upPct}%` }} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {/* 涨停板块分布 */}
        {sectors.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-100 px-4 py-3 shadow-[var(--shadow-sm)]">
            <div className="flex items-center gap-1.5 mb-2.5">
              <TrendingUp className="w-3.5 h-3.5 text-red-500" />
              <span className="text-xs font-semibold text-gray-700">涨停板块分布</span>
            </div>
            <div className="space-y-1.5">
              {sectors.map(s => (
                <div key={s.sector} className="flex items-center gap-2">
                  <span className="text-[11px] text-gray-600 w-16 shrink-0 truncate">{s.sector}</span>
                  <div className="flex-1 h-3.5 bg-gray-50 rounded overflow-hidden">
                    <div className="h-full rounded bg-gradient-to-r from-red-400 to-red-500"
                         style={{ width: `${Math.max(6, (s.count / maxSec) * 100)}%` }} />
                  </div>
                  <span className="text-[11px] font-semibold text-red-600 w-6 text-right shrink-0">{s.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 近几日涨停趋势 */}
        {trend.length > 1 && (
          <div className="bg-white rounded-xl border border-gray-100 px-4 py-3 shadow-[var(--shadow-sm)]">
            <div className="flex items-center gap-1.5 mb-1">
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              <span className="text-xs font-semibold text-gray-700">近 {trend.length} 日涨停数</span>
            </div>
            <svg viewBox={`0 0 ${trendW} ${trendH}`} className="w-full" role="img">
              {trend.map((t, i) => {
                const bw = trendW / trend.length - 10;
                const x = i * (trendW / trend.length) + 5;
                const h = Math.max(4, (t.count / maxTrend) * (trendH - 26));
                const y = trendH - 16 - h;
                const isLast = i === trend.length - 1;
                return (
                  <g key={t.date}>
                    <rect x={x} y={y} width={bw} height={h} rx="3"
                          fill={isLast ? '#ef4444' : '#fca5a5'} />
                    <text x={x + bw / 2} y={y - 4} textAnchor="middle"
                          className="fill-gray-600" style={{ fontSize: 10 }}>{t.count}</text>
                    <text x={x + bw / 2} y={trendH - 4} textAnchor="middle"
                          className="fill-gray-400" style={{ fontSize: 9 }}>
                      {t.date.slice(5)}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Markdown Report View (标题 + KPI + 正文) ───────────────────────────────

export function MdReportView({ r }: { r: MdReport }) {
  const lines = (r.analysis_md || '').split('\n');
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];

  const flushList = (key: string) => {
    if (!listItems.length) return;
    blocks.push(
      <ul key={`ul-${key}`} className="space-y-1.5 list-disc pl-5">
        {listItems.map((it, i) => <li key={i} className="text-sm text-gray-700 leading-relaxed">{renderMdInline(it)}</li>)}
      </ul>
    );
    listItems = [];
  };

  lines.forEach((raw, idx) => {
    const t = raw.trim();
    if (!t) { flushList(String(idx)); return; }
    if (t.startsWith('### ')) {
      flushList(String(idx));
      blocks.push(<h4 key={idx} className="text-sm font-semibold text-gray-800 border-l-4 border-indigo-200 pl-3">{renderMdInline(t.slice(4))}</h4>);
      return;
    }
    if (t.startsWith('## ')) {
      flushList(String(idx));
      blocks.push(
        <div key={idx} className="flex items-center gap-2 pt-2">
          <Brain className="w-4 h-4 text-indigo-500" />
          <h3 className="text-base font-bold text-gray-900">{renderMdInline(t.slice(3))}</h3>
        </div>
      );
      return;
    }
    if (t.startsWith('# ')) {
      flushList(String(idx));
      blocks.push(<h3 key={idx} className="text-lg font-bold text-gray-900">{renderMdInline(t.slice(2))}</h3>);
      return;
    }
    if (/^[-*]\s+/.test(t)) { listItems.push(t.replace(/^[-*]\s+/, '')); return; }
    if (/^\d+[.、]\s+/.test(t)) { listItems.push(t.replace(/^\d+[.、]\s+/, '')); return; }
    flushList(String(idx));
    blocks.push(<p key={idx} className="text-sm text-gray-700 leading-relaxed">{renderMdInline(t)}</p>);
  });
  flushList('end');

  return (
    <div className="space-y-4">
      {(r.trade_date || r.run_time) && (
        <div className="bg-white rounded-2xl border border-gray-100 px-6 py-4 shadow-[var(--shadow-sm)]">
          <div className="flex items-center gap-2">
            <FileText className="w-4 h-4 text-indigo-500" />
            <span className="text-sm font-semibold text-gray-800">
              {r.report_title || '复盘 AI 总结'}{r.trade_date ? ` · ${r.trade_date}` : ''}
            </span>
            <span className="text-xs text-gray-400 font-mono ml-auto">{r.run_time || r.summary_time}</span>
          </div>
          {(r.sources?.length ?? 0) > 0 && (
            <div className="flex items-center flex-wrap gap-1.5 mt-2.5">
              <span className="text-[11px] text-gray-400 mr-0.5">数据源</span>
              {r.sources!.map(s => (
                <span key={s} className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${
                  s === 'wisburg_views' ? 'bg-violet-100 text-violet-700' : 'bg-indigo-50 text-indigo-600'
                }`}>
                  {SOURCE_LABEL[s] ?? s}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      {r.kpis && (r.kpis.limit_up != null || r.kpis.up_count != null || r.kpis.trend_headline != null) && (
        <ReviewKpiSection k={r.kpis} />
      )}
      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="space-y-3">{blocks}</div>
      </div>
    </div>
  );
}
