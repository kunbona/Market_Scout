import { useEffect, useRef, useState, Component } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Loader2,
  Square,
  TrendingDown,
  TrendingUp,
  Users,
  Zap,
} from 'lucide-react';
import { TabHeader } from '../components/TabHeader';

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentStatus {
  running: boolean;
  phase: 'analysts' | 'chief' | null;
  phase_detail: string | null;
  last_run: string | null;
  last_run_type: string | null;
  last_error: string | null;
  pid: number | null;
}

interface MarketStatus {
  mode: string;
  emotion_score: string;
  zt_count?: number;
  dt_count?: number;
  zb_rate?: string;
  max_lianzban?: number;
  yesterday_premium?: string;
  reason: string;
}

interface VerdictSummary {
  verdict: string;
  bull_signals: string[];
  bear_signals: string[];
  key_tension: string;
}

interface Sector {
  name: string;
  stage: '萌芽' | '爆发' | '退潮' | '数据不足';
  heat_score?: number;
  density?: string;
  ma_signal?: string;
  crowding_percentile?: string;
  evidence: string;
  risk: string;
  catalyst: string;
}

interface MainTheme {
  sectors: Sector[];
  tomorrow_focus: string;
}

interface CandidateReasoning {
  narrative_position?: string;
  why_not_priced?: string;
  validation?: string;
  risk?: string;
}

interface Candidate {
  ticker: string;
  name: string;
  direction: string;
  reasoning: CandidateReasoning;
  evidence: string;
  risk_note?: string;   // 旧格式兼容
  confidence: '高' | '中' | '低';
  data_gaps: string[];
}

interface Candidates {
  T0: Candidate[];
  T1: Candidate[];
  T2: Candidate[];
  T3: Candidate[];
}

interface FullReport {
  run_type: 'evening' | 'morning';
  run_time: string;
  summary_time?: string;
  core_narrative?: string;
  market_status: MarketStatus;
  verdict_summary?: VerdictSummary;
  main_theme: MainTheme;
  candidates: Candidates;
  summary_text: string;
}

interface IntradayReport {
  run_type: 'intraday';
  run_time?: string;
  market_status: MarketStatus;
  intraday_pulse: string;
  theme_status: string;
  new_catalyst: boolean;
}

type AgentReport = FullReport | IntradayReport;

interface HistoryItem {
  id: number | string;
  run_time: string;
  run_type: string;
  summary?: string;
  data?: AgentReport;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function safeFetch<T>(path: string, options?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(path, options);
    const json = await res.json();
    if (!json.success) return null;
    return json.data as T;
  } catch {
    return null;
  }
}

function isFullReport(r: AgentReport): r is FullReport {
  return r.run_type === 'evening' || r.run_type === 'morning';
}

// ─── Badge helpers ────────────────────────────────────────────────────────────

const MODE_STYLE: Record<string, { bg: string; text: string }> = {
  正常: { bg: 'bg-green-50', text: 'text-green-700' },
  谨慎: { bg: 'bg-amber-50', text: 'text-amber-700' },
  不操作: { bg: 'bg-red-50', text: 'text-red-700' },
};

const EMOTION_STYLE: Record<string, { bg: string; text: string }> = {
  冰点: { bg: 'bg-gray-100', text: 'text-gray-400' },
  冷淡: { bg: 'bg-gray-100', text: 'text-gray-500' },   // 向后兼容旧数据
  启动: { bg: 'bg-blue-50', text: 'text-blue-600' },
  发酵: { bg: 'bg-amber-50', text: 'text-amber-600' },
  高潮: { bg: 'bg-red-50', text: 'text-red-600' },
  分歧: { bg: 'bg-orange-50', text: 'text-orange-600' },
  退潮: { bg: 'bg-green-50', text: 'text-green-600' },
};

const STAGE_STYLE: Record<string, { bg: string; text: string; border: string }> = {
  萌芽:   { bg: 'bg-blue-50',   text: 'text-blue-600',  border: 'border-blue-200' },
  爆发:   { bg: 'bg-red-50',    text: 'text-red-600',   border: 'border-red-200' },
  退潮:   { bg: 'bg-gray-50',   text: 'text-gray-500',  border: 'border-gray-200' },
  数据不足: { bg: 'bg-gray-50', text: 'text-gray-400',  border: 'border-gray-100' },
};

const CONFIDENCE_STYLE: Record<string, { bg: string; text: string }> = {
  高: { bg: 'bg-red-50', text: 'text-red-600' },
  中: { bg: 'bg-amber-50', text: 'text-amber-600' },
  低: { bg: 'bg-gray-100', text: 'text-gray-500' },
};


function Badge({ text, style }: { text: string; style?: { bg: string; text: string } }) {
  const s = style ?? { bg: 'bg-gray-100', text: 'text-gray-500' };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold ${s.bg} ${s.text}`}>
      {text}
    </span>
  );
}

// ─── Phase Progress Bar ───────────────────────────────────────────────────────

const PHASES: Array<{ key: 'analysts' | 'chief'; label: string; icon: React.ElementType }> = [
  { key: 'analysts', label: '分析师', icon: Users },
  { key: 'chief',    label: '裁决',   icon: Brain },
];

function PhaseBar({
  status,
  onStop,
}: {
  status: AgentStatus;
  onStop: () => void;
}) {
  const currentIdx = status.phase ? PHASES.findIndex(p => p.key === status.phase) : -1;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-[var(--shadow-sm)] mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Loader2 className="w-4 h-4 text-indigo-500 animate-spin" />
          <span className="text-sm font-semibold text-gray-800">
            {status.phase_detail ?? 'AI 分析进行中…'}
          </span>
        </div>
        <button
          onClick={onStop}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                     bg-gray-100 text-gray-600 hover:bg-red-50 hover:text-red-600
                     border border-gray-200 hover:border-red-200 transition-colors"
        >
          <Square className="w-3 h-3" />
          停止
        </button>
      </div>

      <div className="flex items-center gap-2">
        {PHASES.map((phase, idx) => {
          const Icon = phase.icon;
          const isPast    = idx < currentIdx;
          const isCurrent = idx === currentIdx;

          return (
            <div key={phase.key} className="flex items-center gap-2 flex-1">
              <div className="flex flex-col items-center flex-1">
                <div
                  className={`w-full h-1.5 rounded-full transition-all duration-500 ${
                    isPast    ? 'bg-indigo-500' :
                    isCurrent ? 'bg-indigo-400 animate-pulse' :
                    'bg-gray-200'
                  }`}
                />
                <div className={`flex items-center gap-1 mt-1.5 text-xs font-medium ${
                  isCurrent ? 'text-indigo-600' :
                  isPast    ? 'text-gray-400' :
                  'text-gray-300'
                }`}>
                  <Icon className="w-3 h-3" />
                  {phase.label}
                </div>
              </div>
              {idx < PHASES.length - 1 && (
                <div className={`w-3 h-px mt-[-12px] flex-shrink-0 ${
                  idx < currentIdx ? 'bg-indigo-400' : 'bg-gray-200'
                }`} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Trigger Buttons ──────────────────────────────────────────────────────────

function TriggerPanel({
  running,
  onTrigger,
}: {
  running: boolean;
  onTrigger: (type: 'intraday' | 'evening') => void;
}) {
  return (
    <div className="flex items-center gap-3 mb-6">
      <button
        onClick={() => onTrigger('intraday')}
        disabled={running}
        className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold
                   bg-blue-600 text-white hover:bg-blue-700
                   disabled:opacity-50 disabled:cursor-not-allowed
                   transition-colors shadow-sm"
      >
        <Zap className="w-4 h-4" />
        盘中分析
      </button>
      <button
        onClick={() => onTrigger('evening')}
        disabled={running}
        className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold
                   accent-solid disabled:opacity-50 disabled:cursor-not-allowed
                   transition-colors"
        style={{ boxShadow: '0 2px 8px var(--accent-glow)' }}
      >
        <Brain className="w-4 h-4" />
        盘后总结
      </button>
      {running && (
        <span className="text-xs text-gray-400 flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" />
          分析进行中，请等待…
        </span>
      )}
    </div>
  );
}

// ─── Market Status Card ───────────────────────────────────────────────────────

function MarketStatusCard({ ms }: { ms: MarketStatus }) {
  const modeStyle = MODE_STYLE[ms.mode] ?? { bg: 'bg-gray-100', text: 'text-gray-600' };
  const emotionStyle = EMOTION_STYLE[ms.emotion_score] ?? { bg: 'bg-gray-100', text: 'text-gray-600' };

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800">市场状态</h3>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <Badge text={ms.mode} style={modeStyle} />
        <Badge text={ms.emotion_score} style={emotionStyle} />
      </div>

      {(ms.zt_count != null || ms.dt_count != null || ms.max_lianzban != null) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          {ms.zt_count != null && (
            <div className="bg-red-50 rounded-xl p-3 text-center">
              <div className="text-xl font-bold text-red-600">{ms.zt_count}</div>
              <div className="text-xs text-gray-500 mt-0.5">涨停</div>
            </div>
          )}
          {ms.dt_count != null && (
            <div className="bg-green-50 rounded-xl p-3 text-center">
              <div className="text-xl font-bold text-green-600">{ms.dt_count}</div>
              <div className="text-xs text-gray-500 mt-0.5">跌停</div>
            </div>
          )}
          {ms.zb_rate != null && (
            <div className="bg-amber-50 rounded-xl p-3 text-center">
              <div className="text-xl font-bold text-amber-600">{ms.zb_rate}</div>
              <div className="text-xs text-gray-500 mt-0.5">炸板率</div>
            </div>
          )}
          {ms.max_lianzban != null && (
            <div className="bg-gray-50 rounded-xl p-3 text-center">
              <div className="text-xl font-bold text-gray-700">{ms.max_lianzban}</div>
              <div className="text-xs text-gray-500 mt-0.5">最高连板</div>
            </div>
          )}
        </div>
      )}

      {ms.yesterday_premium != null && ms.yesterday_premium !== 'data_gap' && (
        <div className="text-xs text-gray-500 mb-3">
          昨日涨停溢价：<span className="font-semibold text-gray-700">{ms.yesterday_premium}</span>
        </div>
      )}

      <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 rounded-xl px-4 py-3">
        {ms.reason}
      </p>
    </div>
  );
}

// ─── Verdict Summary Card ─────────────────────────────────────────────────────

function VerdictCard({ d }: { d: VerdictSummary }) {
  const verdictStyle: Record<string, { bg: string; text: string }> = {
    偏多: { bg: 'bg-red-50', text: 'text-red-700' },
    偏空: { bg: 'bg-green-50', text: 'text-green-700' },
    中性: { bg: 'bg-amber-50', text: 'text-amber-700' },
    观望: { bg: 'bg-gray-100', text: 'text-gray-600' },
  };
  const vs = verdictStyle[d.verdict] ?? { bg: 'bg-gray-100', text: 'text-gray-600' };

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800">多空裁决</h3>
        <span className={`ml-auto text-xs font-semibold px-2.5 py-1 rounded-lg ${vs.bg} ${vs.text}`}>
          {d.verdict}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        {d.bull_signals?.length > 0 && (
          <div className="bg-red-50 rounded-xl p-3">
            <div className="text-xs text-red-400 mb-2 font-medium flex items-center gap-1">
              <TrendingUp className="w-3 h-3" /> 做多信号
            </div>
            <ul className="space-y-1">
              {d.bull_signals.map((s, i) => (
                <li key={i} className="text-xs text-red-800 leading-relaxed">· {s}</li>
              ))}
            </ul>
          </div>
        )}
        {d.bear_signals?.length > 0 && (
          <div className="bg-green-50 rounded-xl p-3">
            <div className="text-xs text-green-500 mb-2 font-medium flex items-center gap-1">
              <TrendingDown className="w-3 h-3" /> 观望信号
            </div>
            <ul className="space-y-1">
              {d.bear_signals.map((s, i) => (
                <li key={i} className="text-xs text-green-900 leading-relaxed">· {s}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {d.key_tension && (
        <div className="bg-indigo-50 rounded-xl px-4 py-3">
          <div className="text-xs text-indigo-400 mb-1 font-medium">核心分歧</div>
          <p className="text-sm text-indigo-900 leading-relaxed">{d.key_tension}</p>
        </div>
      )}
    </div>
  );
}

// ─── Main Theme Card ──────────────────────────────────────────────────────────

function MainThemeCard({ t }: { t: MainTheme }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 mb-4">
        <Zap className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800">主线板块</h3>
        <span className="ml-auto text-xs text-gray-400">{t.sectors.length} 个板块</span>
      </div>

      {t.sectors.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">暂无主线板块数据</p>
      ) : (
        <div className="space-y-3 mb-4">
          {t.sectors.map((s, i) => {
            const ss = STAGE_STYLE[s.stage] ?? STAGE_STYLE['退潮'];
            return (
              <div key={i} className={`rounded-xl border p-4 ${ss.bg} ${ss.border}`}>
                <div className="flex items-center gap-2 mb-2 flex-wrap">
                  <span className={`text-sm font-semibold ${ss.text}`}>{s.name}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${ss.bg} ${ss.text} border ${ss.border}`}>
                    {s.stage}
                  </span>
                  {s.heat_score != null && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-white/70 text-gray-600 border border-gray-200 font-mono">
                      热度 {s.heat_score}
                    </span>
                  )}
                  {s.density && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-white/70 text-gray-600 border border-gray-200 font-mono">
                      密度 {s.density}
                    </span>
                  )}
                  {s.ma_signal && (
                    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${
                      s.ma_signal.includes('加速') ? 'bg-red-50 text-red-500 border-red-200' :
                      s.ma_signal.includes('减速') ? 'bg-green-50 text-green-500 border-green-200' :
                      'bg-gray-50 text-gray-500 border-gray-200'
                    }`}>
                      {s.ma_signal}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-600 leading-relaxed mb-1">{s.evidence}</p>
                {s.catalyst && (
                  <p className="text-xs text-gray-500">催化剂：{s.catalyst}</p>
                )}
                {s.risk && (
                  <p className="text-xs text-gray-400 mt-1">风险：{s.risk}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {t.tomorrow_focus && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <div className="text-xs text-amber-500 font-semibold mb-1">明日关注</div>
          <p className="text-sm text-amber-900 leading-relaxed">{t.tomorrow_focus}</p>
        </div>
      )}
    </div>
  );
}

// ─── Candidates Card ─────────────────────────────────────────────────────────

const TIER_LABELS: Record<string, string> = {
  T0: 'T+0 短线',
  T1: 'T+1 次日',
  T2: 'T+2 两日',
  T3: 'T+3 波段',
};

function CandidatesCard({ c }: { c: Candidates }) {
  const [activeTab, setActiveTab] = useState<keyof Candidates>('T0');
  const tabs = (['T0', 'T1', 'T2', 'T3'] as const).filter(t => c[t].length > 0);
  const stocks = c[activeTab] ?? [];

  if (tabs.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-2 mb-4">
          <CheckCircle2 className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-semibold text-gray-800">候选股票</h3>
        </div>
        <p className="text-sm text-gray-400 text-center py-6">本次分析暂无候选股票</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 mb-4">
        <CheckCircle2 className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800">候选股票</h3>
      </div>

      <div className="flex gap-1.5 mb-5 bg-gray-100 p-1 rounded-xl">
        {tabs.map(t => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className={`flex-1 py-1.5 px-3 rounded-lg text-xs font-semibold transition-colors ${
              activeTab === t
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {TIER_LABELS[t] ?? t}
            <span className={`ml-1 ${activeTab === t ? 'text-indigo-500' : 'text-gray-400'}`}>
              ({c[t].length})
            </span>
          </button>
        ))}
      </div>

      {stocks.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">当前分组无候选股票</p>
      ) : (
        <div className="space-y-3">
          {stocks.map((s, i) => {
            const cs = CONFIDENCE_STYLE[s.confidence] ?? CONFIDENCE_STYLE['低'];
            return (
              <div key={i} className="border border-gray-100 rounded-xl p-4 hover:bg-gray-50 transition-colors">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-bold text-gray-900">{s.name}</span>
                    <span className="text-xs font-mono text-gray-400">{s.ticker}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 font-medium shrink-0">
                      {s.direction}
                    </span>
                  </div>
                  <Badge text={`置信度：${s.confidence}`} style={cs} />
                </div>
                {s.evidence && (
                  <p className="text-xs text-gray-600 leading-relaxed mb-2">{s.evidence}</p>
                )}
                {s.reasoning?.narrative_position && (
                  <p className="text-xs text-indigo-600 leading-relaxed mb-1">
                    叙事位置：{s.reasoning.narrative_position}
                  </p>
                )}
                {s.reasoning?.why_not_priced && (
                  <p className="text-xs text-amber-700 leading-relaxed mb-1">
                    定价缺口：{s.reasoning.why_not_priced}
                  </p>
                )}
                {/* 优先 reasoning.risk，向后兼容 risk_note */}
                {(s.reasoning?.risk || s.risk_note) && (
                  <p className="text-xs text-red-400 leading-relaxed flex items-start gap-1">
                    <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                    {s.reasoning?.risk ?? s.risk_note}
                  </p>
                )}
                {(s.data_gaps ?? []).length > 0 && (
                  <p className="text-xs text-gray-400 mt-1">数据缺口：{(s.data_gaps ?? []).join('、')}</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Summary Card ─────────────────────────────────────────────────────────────

function SummaryCard({ text }: { text: string }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 mb-3">
        <Brain className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800">全局摘要</h3>
      </div>
      <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{text}</p>
    </div>
  );
}

// ─── Intraday Report ──────────────────────────────────────────────────────────

function IntradayReportView({ r }: { r: IntradayReport }) {
  return (
    <div className="space-y-4">
      <MarketStatusCard ms={r.market_status} />

      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-4 h-4 text-amber-400" />
          <h3 className="text-sm font-semibold text-gray-800">盘感摘要</h3>
        </div>
        <div className="bg-amber-50 border border-amber-100 rounded-2xl px-6 py-5">
          <p className="text-base text-amber-900 leading-relaxed font-medium">{r.intraday_pulse}</p>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-gray-400 mb-1">主线状态</div>
            <div className="text-sm font-semibold text-gray-800">{r.theme_status || '--'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-400 mb-1">新催化剂</div>
            <div className={`text-sm font-semibold ${r.new_catalyst ? 'text-red-600' : 'text-gray-500'}`}>
              {r.new_catalyst ? '有新催化剂' : '无'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Full Report ──────────────────────────────────────────────────────────────

function FullReportView({ r }: { r: FullReport }) {
  const safeCandidates: Candidates = {
    T0: r.candidates?.T0 ?? [],
    T1: r.candidates?.T1 ?? [],
    T2: r.candidates?.T2 ?? [],
    T3: r.candidates?.T3 ?? [],
  };
  const safeTheme: MainTheme = {
    sectors: r.main_theme?.sectors ?? [],
    tomorrow_focus: r.main_theme?.tomorrow_focus ?? '',
  };
  return (
    <div className="space-y-4">
      {r.core_narrative && (
        <div className="bg-indigo-600 rounded-2xl px-6 py-4 shadow-[var(--shadow-sm)]">
          <div className="text-xs text-indigo-200 mb-1.5 font-medium">核心叙事</div>
          <p className="text-sm text-white leading-relaxed font-medium">{r.core_narrative}</p>
        </div>
      )}
      {r.market_status && <MarketStatusCard ms={r.market_status} />}
      {r.verdict_summary && <VerdictCard d={r.verdict_summary} />}
      <MainThemeCard t={safeTheme} />
      <CandidatesCard c={safeCandidates} />
      {r.summary_text && <SummaryCard text={r.summary_text} />}
    </div>
  );
}

// ─── History Panel ────────────────────────────────────────────────────────────

function HistoryPanel({ items }: { items: HistoryItem[] }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | string | null>(null);
  const [detailCache, setDetailCache] = useState<Record<string | number, AgentReport>>({});
  const [loadingId, setLoadingId] = useState<number | string | null>(null);

  const RUN_TYPE_LABEL: Record<string, string> = {
    intraday: '盘中',
    evening:  '盘后',
    morning:  '早盘前',
    auction:  '竞价',
    closing:  '收盘后',
  };

  const handleToggle = async (id: number | string) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (detailCache[id]) return;
    setLoadingId(id);
    const data = await safeFetch<AgentReport>(`/api/agent/history/${id}`);
    if (data) setDetailCache(prev => ({ ...prev, [id]: data }));
    setLoadingId(null);
  };

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-semibold text-gray-800">历史记录</span>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
            {items.length} 条
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </button>

      {open && (
        <div className="border-t border-gray-100">
          {items.length === 0 ? (
            <div className="px-6 py-8 text-center text-sm text-gray-400">暂无历史记录</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {items.map(item => {
                const isExpanded = expanded === item.id;
                const detail = detailCache[item.id];
                const isLoading = loadingId === item.id;
                return (
                  <div key={item.id}>
                    <button
                      onClick={() => handleToggle(item.id)}
                      className="w-full flex items-center gap-3 px-6 py-3.5 hover:bg-gray-50 transition-colors text-left"
                    >
                      <span className={`shrink-0 text-xs px-2 py-0.5 rounded font-semibold ${
                        item.run_type === 'intraday'
                          ? 'bg-blue-50 text-blue-600'
                          : 'bg-indigo-50 text-indigo-600'
                      }`}>
                        {RUN_TYPE_LABEL[item.run_type] ?? item.run_type}
                      </span>
                      <span className="shrink-0 text-xs font-mono text-gray-500">{item.run_time}</span>
                      {item.summary && (
                        <span className="text-xs text-gray-400 truncate flex-1">{item.summary}</span>
                      )}
                      <span className="ml-auto shrink-0">
                        {isExpanded
                          ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                          : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
                      </span>
                    </button>

                    {isExpanded && (
                      <div className="px-6 pb-6 pt-4 bg-gray-50 border-t border-gray-100">
                        {isLoading ? (
                          <div className="py-8 flex items-center justify-center gap-2 text-sm text-gray-400">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            加载中…
                          </div>
                        ) : detail ? (
                          isFullReport(detail)
                            ? <FullReportView r={detail} />
                            : <IntradayReportView r={detail} />
                        ) : (
                          <div className="py-6 text-center text-sm text-gray-400">加载失败，请重试</div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Error Boundary ───────────────────────────────────────────────────────────

class AgentErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null };
  static getDerivedStateFromError(e: Error) { return { error: e.message }; }
  render() {
    if (this.state.error) {
      return (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-8 text-center">
          <AlertTriangle className="w-8 h-8 text-red-400 mx-auto mb-3" />
          <p className="text-sm font-semibold text-red-700 mb-1">页面渲染出错</p>
          <p className="text-xs text-red-500 font-mono">{this.state.error}</p>
          <button
            className="mt-4 px-4 py-2 text-xs bg-red-100 text-red-700 rounded-lg hover:bg-red-200"
            onClick={() => this.setState({ error: null })}
          >重试</button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ─── Main Page ────────────────────────────────────────────────────────────────

function AgentPageInner() {
  const [status, setStatus]     = useState<AgentStatus | null>(null);
  const [report, setReport]     = useState<AgentReport | null>(null);
  const [history, setHistory]   = useState<HistoryItem[]>([]);
  const [error, setError]       = useState<string | null>(null);

  const pollRef         = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopConfirmRef  = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const loadLatest = async () => {
    const data = await safeFetch<AgentReport>('/api/agent/latest');
    if (data) setReport(data);
  };

  const loadHistory = async () => {
    const raw = await safeFetch<Array<{ id: number | string; run_time: string; run_type: string; summary_text?: string; summary_time?: string }>>('/api/agent/history');
    if (raw) {
      setHistory(raw.map(item => ({
        id: item.id,
        run_time: item.run_time,
        run_type: item.run_type,
        summary: item.summary_text,
      })));
    }
  };

  const loadStatus = async () => {
    const data = await safeFetch<AgentStatus>('/api/agent/status');
    if (data) setStatus(data);
    return data;
  };

  const startPolling = () => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      const s = await loadStatus();
      if (s && !s.running) {
        stopPoll();
        await loadLatest();
        await loadHistory();
      }
    }, 2000);
  };

  useEffect(() => {
    loadLatest();
    loadHistory();
    loadStatus().then(s => {
      if (s?.running) startPolling();
    });
    return () => {
      stopPoll();
      if (stopConfirmRef.current) clearInterval(stopConfirmRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleTrigger = async (run_type: 'intraday' | 'evening') => {
    setError(null);
    try {
      const res = await fetch('/api/agent/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_type }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '启动失败');
        return;
      }
      const newStatus = await loadStatus();
      if (newStatus?.running) startPolling();
    } catch {
      setError('请求失败，请检查后端服务');
    }
  };

  const handleStop = async () => {
    if (stopConfirmRef.current) {
      clearInterval(stopConfirmRef.current);
      stopConfirmRef.current = null;
    }
    try {
      await fetch('/api/agent/stop', { method: 'POST' });
    } catch {
      // graceful: ignore errors
    }
    let attempts = 0;
    const confirm = setInterval(async () => {
      attempts++;
      const s = await loadStatus();
      if ((s && !s.running) || attempts >= 8) {
        clearInterval(confirm);
        stopPoll();
        if (s && !s.running) await loadLatest();
      }
    }, 2000);
    stopConfirmRef.current = confirm;
  };

  const isRunning = status?.running ?? false;

  const RUN_TYPE_LABEL_STATUS: Record<string, string> = {
    intraday: '盘中',
    evening:  '盘后',
    morning:  '早盘前',
    auction:  '竞价',
    closing:  '收盘后',
  };
  const lastRunLabel = status?.last_run
    ? `${status.last_run}${status.last_run_type ? `（${RUN_TYPE_LABEL_STATUS[status.last_run_type] ?? status.last_run_type}）` : ''}`
    : undefined;

  return (
    <div className="space-y-0">
      <TabHeader
        title="AI 智能分析"
        subtitle="多 Agent 协作分析 A 股市场"
        lastUpdate={lastRunLabel}
      />

      {error && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-800 mb-6">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {status?.last_error && !isRunning && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800 mb-6">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-amber-500" />
          <div>
            <div className="font-semibold mb-0.5">上次运行出现错误</div>
            <div className="text-amber-700">{status.last_error}</div>
          </div>
        </div>
      )}

      {isRunning && status && (
        <PhaseBar status={status} onStop={handleStop} />
      )}

      <TriggerPanel running={isRunning} onTrigger={handleTrigger} />

      {report ? (
        isFullReport(report)
          ? <FullReportView r={report} />
          : <IntradayReportView r={report} />
      ) : (
        <div className="bg-white rounded-2xl border border-gray-100 p-12 shadow-[var(--shadow-sm)]
                        flex flex-col items-center justify-center gap-3 text-center mb-6">
          <Brain className="w-10 h-10 text-gray-200" />
          <p className="text-sm font-semibold text-gray-400">暂无分析报告</p>
          <p className="text-xs text-gray-300">点击「盘中分析」或「盘后总结」按钮启动 AI 分析</p>
        </div>
      )}

      <div className="mt-6">
        <HistoryPanel items={history} />
      </div>
    </div>
  );
}

export function AgentPage() {
  return (
    <AgentErrorBoundary>
      <AgentPageInner />
    </AgentErrorBoundary>
  );
}
