import { useEffect, useRef, useState, Component } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  BookOpen,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  FileText,
  Landmark,
  Loader2,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { PhaseBar, HtmlReportView, safeFetch, formatAlreadyRunningMessage } from '../components/agentShared';
import type { AgentStatus, AlreadyRunningData } from '../components/agentShared';
import { PushToWecomButton } from '../components/PushToWecomButton';

// ─── Types ────────────────────────────────────────────────────────────────────

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
  id?: number;
  has_html?: boolean;
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
  id?: number;
  has_html?: boolean;
  market_status: MarketStatus;
  intraday_pulse: string;
  theme_status: string;
  new_catalyst: boolean;
}

interface ReviewKpis {
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
}

interface MdReport {
  run_type?: string;
  run_time?: string;
  summary_time?: string;
  id?: number;
  has_html?: boolean;
  analysis_md: string;
  trade_date?: string;
  kpis?: ReviewKpis;
}

type AgentReport = FullReport | IntradayReport | MdReport;

interface HistoryItem {
  id: number | string;
  run_time: string;
  run_type: string;
  summary?: string;
  has_html?: boolean;
  data?: AgentReport;
}

type TriggerRunType = 'intraday' | 'morning' | 'evening' | 'policy' | 'research' | 'notice';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function isFullReport(r: AgentReport): r is FullReport {
  return r.run_type === 'evening' || r.run_type === 'morning';
}

// Markdown 型报告（如 review_ai 复盘 AI 总结）：只有 analysis_md，无结构化字段
function isMdReport(r: AgentReport): r is MdReport {
  return Boolean((r as MdReport).analysis_md) && !(r as IntradayReport).market_status && !(r as FullReport).core_narrative;
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

// ─── Trigger Buttons ──────────────────────────────────────────────────────────

function useTimeSlot(): 'morning' | 'intraday' | 'evening' {
  const [slot, setSlot] = useState<'morning' | 'intraday' | 'evening'>(() => {
    // 初始值用本地时间快速计算（避免白屏）
    const t = new Date().getHours() * 60 + new Date().getMinutes();
    if (t < 9 * 60 + 15) return 'morning';
    if (t < 15 * 60 + 30) return 'intraday';
    return 'evening';
  });

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function fetchSlot() {
      try {
        const res = await fetch('/api/agent/time-slot');
        const json = await res.json();
        if (json.success && json.data) {
          setSlot(json.data.slot as 'morning' | 'intraday' | 'evening');
          // 在 next_change_at 时刻自动重新请求
          const nextChange = new Date(json.data.next_change_at).getTime();
          const now = Date.now();
          const delay = Math.max(nextChange - now, 10_000); // 最少10秒后再查
          timer = setTimeout(fetchSlot, delay);
        }
      } catch {
        // 网络失败：60秒后重试
        timer = setTimeout(fetchSlot, 60_000);
      }
    }

    fetchSlot();
    return () => { if (timer) clearTimeout(timer); };
  }, []);

  return slot;
}

function TriggerPanel({
  running,
  onTrigger,
}: {
  running: boolean;
  onTrigger: (type: TriggerRunType) => void;
}) {
  const slot = useTimeSlot();

  const btnBase = "flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <div className="flex flex-wrap items-center gap-3 mb-6">
      {/* 盘前分析 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('morning')}
          disabled={running}
          title={slot !== 'morning' ? '建议在 0:00–9:15 盘前时段使用' : undefined}
          className={`${btnBase} ${
            slot === 'morning'
              ? 'bg-amber-500 text-white hover:bg-amber-600'
              : 'bg-amber-100 text-amber-700 hover:bg-amber-200 ring-1 ring-amber-300'
          }`}
        >
          <Brain className="w-4 h-4" />
          盘前分析
          {slot !== 'morning' && (
            <span className="ml-1 text-xs opacity-60">（非盘前时段）</span>
          )}
        </button>
      </div>

      {/* 盘中分析 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('intraday')}
          disabled={running}
          title={slot !== 'intraday' ? '建议在 9:15–15:30 盘中时段使用' : undefined}
          className={`${btnBase} ${
            slot === 'intraday'
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-blue-100 text-blue-700 hover:bg-blue-200 ring-1 ring-blue-300'
          }`}
        >
          <Zap className="w-4 h-4" />
          盘中分析
          {slot !== 'intraday' && (
            <span className="ml-1 text-xs opacity-60">（非盘中时段）</span>
          )}
        </button>
      </div>

      {/* 盘后总结 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('evening')}
          disabled={running}
          title={slot !== 'evening' ? '建议在 15:30 后盘后时段使用' : undefined}
          className={`${btnBase} ${
            slot === 'evening'
              ? 'accent-solid'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300'
          }`}
          style={slot === 'evening' ? { boxShadow: '0 2px 8px var(--accent-glow)' } : undefined}
        >
          <TrendingUp className="w-4 h-4" />
          盘后总结
          {slot !== 'evening' && (
            <span className="ml-1 text-xs opacity-60">（非盘后时段）</span>
          )}
        </button>
      </div>

      {/* 政策解读 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('policy')}
          disabled={running}
          title="专项解读近3日政策动态（发改委/证监会/交易所/公告）"
          className={`${btnBase} bg-emerald-100 text-emerald-700 hover:bg-emerald-200 ring-1 ring-emerald-300`}
        >
          <Landmark className="w-4 h-4" />
          政策解读
        </button>
      </div>

      {/* 研报解读 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('research')}
          disabled={running}
          title="专项解读近3日券商研报：机构共识与分歧、评级变动、未来展望"
          className={`${btnBase} bg-violet-100 text-violet-700 hover:bg-violet-200 ring-1 ring-violet-300`}
        >
          <BookOpen className="w-4 h-4" />
          研报解读
        </button>
      </div>

      {/* 公告解读 */}
      <div className="relative group">
        <button
          onClick={() => onTrigger('notice')}
          disabled={running}
          title="专项解读近3日巨潮公告：分类研究、筛出高含量公告、推理影响与跟踪建议"
          className={`${btnBase} bg-orange-100 text-orange-700 hover:bg-orange-200 ring-1 ring-orange-300`}
        >
          <FileText className="w-4 h-4" />
          公告解读
        </button>
      </div>

      {running && (
        <span className="text-xs text-gray-400 flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" />
          分析进行中，请等待…
        </span>
      )}
    </div>
  );
}

// ─── Info Brief Panel (4 路信息源综合整理) ─────────────────────────────

function InfoBriefPanel({
  running,
  onTrigger,
}: {
  running: boolean;
  onTrigger: (type: 'morning' | 'intraday' | 'evening') => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-6 p-4 bg-gradient-to-r from-indigo-50 to-purple-50 rounded-2xl border border-indigo-100">
      <div className="flex items-center gap-2 mr-2">
        <div className={`w-2 h-2 rounded-full ${running ? 'bg-amber-500 animate-pulse' : 'bg-indigo-500'}`} />
        <span className="text-xs font-semibold text-indigo-700">信息情报简报</span>
        <span className="text-xs text-indigo-500">
          {running ? '正在生成中,约需 1-2 分钟...' : '4 路信息源 (快讯/政策/公告/研报) 综合整理'}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 ml-auto">
        <button
          onClick={() => onTrigger('morning')}
          disabled={running}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-indigo-700 border border-indigo-200 hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳' : '☀️'} 盘前版
        </button>
        <button
          onClick={() => onTrigger('intraday')}
          disabled={running}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-indigo-700 border border-indigo-200 hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳' : '⏰'} 盘中版
        </button>
        <button
          onClick={() => onTrigger('evening')}
          disabled={running}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white border border-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳ 生成中...' : '🌙 盘后版（推荐）'}
        </button>
      </div>
    </div>
  );
}

// ─── Strategist Panel (麦肯锡框架推理) ─────────────────────────────

function StrategistPanel({
  running,
  onTrigger,
  hasInfoBrief,
}: {
  running: boolean;
  onTrigger: (type: 'morning' | 'intraday' | 'evening') => void;
  hasInfoBrief: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-6 p-4 bg-gradient-to-r from-slate-50 to-amber-50 rounded-2xl border border-slate-200">
      <div className="flex items-center gap-2 mr-2">
        <div className={`w-2 h-2 rounded-full ${running ? 'bg-amber-500 animate-pulse' : 'bg-slate-700'}`} />
        <span className="text-xs font-semibold text-slate-800">战略推理 (Strategist)</span>
        <span className="text-xs text-slate-500">
          {running ? '正在生成中,约需 1-2 分钟...' : hasInfoBrief ? '基于最近一次信息情报简报 + 麦肯锡 6 框架' : '需先跑信息情报简报'}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 ml-auto">
        <button
          onClick={() => onTrigger('morning')}
          disabled={running || !hasInfoBrief}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳' : '☀️'} 盘前版
        </button>
        <button
          onClick={() => onTrigger('intraday')}
          disabled={running || !hasInfoBrief}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳' : '⏰'} 盘中版
        </button>
        <button
          onClick={() => onTrigger('evening')}
          disabled={running || !hasInfoBrief}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-white border border-slate-800 hover:bg-slate-900 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳ 生成中...' : '🌙 盘后版（推荐）'}
        </button>
      </div>
    </div>
  );
}

// ─── Review AI Panel (复盘 AI 总结 手动触发) ────────────────────────────────

function ReviewAiPanel({ onDone }: { onDone: () => void }) {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState('');
  const [failMsg, setFailMsg] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const stopPoll = () => {
    if (timerRef.current) { window.clearInterval(timerRef.current); timerRef.current = null; }
  };
  useEffect(() => stopPoll, []);

  const startPoll = () => {
    stopPoll();
    timerRef.current = window.setInterval(async () => {
      const job = await safeFetch<{ state: string; progress?: string; error?: string | null }>('/api/agent/review_ai/job');
      if (!job) return;
      setProgress(job.progress || '');
      if (job.state !== 'running') {
        stopPoll();
        setRunning(false);
        if (job.state === 'error') setFailMsg(job.error || '生成失败，请看后端日志');
        else setFailMsg(null);
        onDone();
      }
    }, 3000);
  };

  const trigger = async () => {
    setFailMsg(null);
    setRunning(true);
    setProgress('启动中...');
    try {
      const resp = await fetch('/api/agent/review_ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const json = await resp.json().catch(() => null);
      if (!resp.ok || !json?.success) {
        setRunning(false);
        setFailMsg(json?.error ?? `HTTP ${resp.status}`);
        return;
      }
      startPoll();
    } catch {
      setRunning(false);
      setFailMsg('请求失败，请检查后端服务');
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3 mb-6 p-4 bg-gradient-to-r from-indigo-50 to-purple-50 rounded-2xl border border-indigo-200">
      <div className="flex items-center gap-2 mr-2">
        <div className={`w-2 h-2 rounded-full ${running ? 'bg-indigo-500 animate-pulse' : 'bg-indigo-700'}`} />
        <span className="text-xs font-semibold text-indigo-900">复盘 AI 总结</span>
        <span className="text-xs text-indigo-500">
          {running ? (progress || '正在生成中, 约需 1 分钟...') : '9 维度复盘 + DM-kun 6 专题 → AI 交叉总结'}
        </span>
        {failMsg && <span className="text-xs text-red-500">⚠️ {failMsg}</span>}
      </div>
      <div className="flex items-center gap-2 ml-auto">
        <button
          onClick={trigger}
          disabled={running}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white border border-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? '⏳ 生成中...' : '🧠 生成复盘 AI 总结'}
        </button>
      </div>
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

// ─── Markdown Report (review_ai 等) ──────────────────────────────────────────

function renderMdInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**') && p.length > 4
      ? <strong key={i} className="font-semibold text-gray-900">{p.slice(2, -2)}</strong>
      : <span key={i}>{p}</span>
  );
}

// ─── KPI Cards (review_ai 图文并茂) ─────────────────────────────────────────

function KpiCard({ label, value, unit, tone, sub }: {
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

function fmtYi(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return '--';
  if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2);
  return v.toFixed(0);
}
function yiUnit(v?: number | null): string {
  if (v == null || Number.isNaN(v)) return '';
  return Math.abs(v) >= 10000 ? '万亿' : '亿';
}

function ReviewKpiSection({ k }: { k: ReviewKpis }) {
  const up = k.up_count ?? 0;
  const down = k.down_count ?? 0;
  const total = up + down;
  const upPct = total > 0 ? Math.round((up / total) * 100) : 0;

  const sectors = (k.limit_up_sectors ?? []).filter(s => s.count > 0);
  const maxSec = sectors.length ? Math.max(...sectors.map(s => s.count)) : 1;
  const trend = (k.limit_up_trend ?? []).slice(-6);
  const maxTrend = trend.length ? Math.max(...trend.map(t => t.count), 1) : 1;
  const trendW = 320, trendH = 88;

  return (
    <div className="space-y-4">
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

function MdReportView({ r }: { r: MdReport }) {
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
        <div className="bg-white rounded-2xl border border-gray-100 px-6 py-4 shadow-[var(--shadow-sm)] flex items-center gap-2">
          <FileText className="w-4 h-4 text-indigo-500" />
          <span className="text-sm font-semibold text-gray-800">
            复盘 AI 总结{r.trade_date ? ` · ${r.trade_date}` : ''}
          </span>
          <span className="text-xs text-gray-400 font-mono ml-auto">{r.run_time || r.summary_time}</span>
        </div>
      )}
      {r.kpis && (r.kpis.limit_up != null || r.kpis.up_count != null) && (
        <ReviewKpiSection k={r.kpis} />
      )}
      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="space-y-3">{blocks}</div>
      </div>
    </div>
  );
}

// ─── Intraday Report ──────────────────────────────────────────────────────────

function IntradayReportView({ r }: { r: IntradayReport }) {
  return (
    <div className="space-y-4">
      {r.market_status && <MarketStatusCard ms={r.market_status} />}

      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-4 h-4 text-amber-400" />
          <h3 className="text-sm font-semibold text-gray-800">盘感摘要</h3>
        </div>
        <div className="bg-amber-50 border border-amber-100 rounded-2xl px-6 py-5">
          <p className="text-base text-amber-900 leading-relaxed font-medium">{r.intraday_pulse ?? '--'}</p>
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
    policy:   '政策解读',
    research: '研报解读',
    notice:   '公告解读',
    watchlist: '股池动态',
    review_ai: '复盘 AI 总结',
  };

  const handleToggle = async (item: HistoryItem) => {
    const id = item.id;
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (detailCache[id]) return;
    // HTML 报告直接用 iframe，无需加载完整 JSON
    if (item.has_html) {
      setDetailCache(prev => ({ ...prev, [id]: { run_type: item.run_type as AgentReport['run_type'], id: id as number, has_html: true } as AgentReport }));
      return;
    }
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
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => handleToggle(item)}
                      className="w-full flex items-center gap-3 px-6 py-3.5 hover:bg-gray-50 transition-colors text-left cursor-pointer"
                    >
                      <span className={`shrink-0 text-xs px-2 py-0.5 rounded font-semibold ${
                        item.run_type === 'intraday'
                          ? 'bg-blue-50 text-blue-600'
                          : item.run_type === 'policy'
                            ? 'bg-emerald-50 text-emerald-600'
                            : item.run_type === 'research'
                              ? 'bg-violet-50 text-violet-600'
                              : item.run_type === 'notice'
                                ? 'bg-orange-50 text-orange-600'
                                : item.run_type === 'watchlist'
                                  ? 'bg-rose-50 text-rose-600'
                                  : item.run_type === 'info_brief'
                                    ? 'bg-gradient-to-r from-indigo-100 to-purple-100 text-indigo-700'
                                    : item.run_type === 'strategist'
                                      ? 'bg-gradient-to-r from-slate-100 to-amber-100 text-slate-700'
                                      : 'bg-indigo-50 text-indigo-600'
                      }`}>
                        {RUN_TYPE_LABEL[item.run_type] ?? item.run_type}
                      </span>
                      <span className="shrink-0 text-xs font-mono text-gray-500">{item.run_time}</span>
                      {item.summary && (
                        <span className="text-xs text-gray-400 truncate flex-1">{item.summary}</span>
                      )}
                      <span
                        onClick={e => e.stopPropagation()}
                        className="shrink-0"
                      >
                        <PushToWecomButton
                          mode="agent-report"
                          rowId={item.id as number}
                          label="推"
                        />
                      </span>
                      <span className="ml-1 shrink-0">
                        {isExpanded
                          ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                          : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
                      </span>
                    </div>

                    {isExpanded && (
                      <div className="px-6 pb-6 pt-4 bg-gray-50 border-t border-gray-100">
                        {isLoading ? (
                          <div className="py-8 flex items-center justify-center gap-2 text-sm text-gray-400">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            加载中…
                          </div>
                        ) : detail ? (
                          detail.has_html && detail.id
                            ? <HtmlReportView reportId={detail.id} />
                            : isMdReport(detail)
                              ? <MdReportView r={detail} />
                              : isFullReport(detail)
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
  const [infoBriefLoading, setInfoBriefLoading] = useState(false);
  const [strategistLoading, setStrategistLoading] = useState(false);

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
    // 关注股池分析有自己的页面（关注股池），AI 智能分析页不显示
    if (data && (data as { run_type?: string }).run_type === 'watchlist') {
      setReport(null);
      return;
    }
    if (data) setReport(data);
  };

  const loadHistory = async () => {
    const raw = await safeFetch<Array<{ id: number | string; run_time: string; run_type: string; summary_text?: string; summary_time?: string; has_html?: boolean }>>('/api/agent/history');
    if (raw) {
      setHistory(raw
        // 关注股池分析有自己的页面（关注股池），AI 智能分析页不显示
        .filter(item => item.run_type !== 'watchlist')
        .map(item => ({
          id: item.id,
          run_time: item.run_time,
          run_type: item.run_type,
          summary: item.summary_text,
          has_html: item.has_html,
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

  const handleTrigger = async (run_type: TriggerRunType, pool?: string) => {
    setError(null);
    try {
      const res = await fetch('/api/agent/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pool ? { run_type, pool } : { run_type }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '启动失败');
        return;
      }
      // 已有其他管道在跑：后端返回 success=true 但 data.status='already_running'，
      // 给用户友好提示而不是显示别家的进度条
      if (json.data?.status === 'already_running') {
        setError(formatAlreadyRunningMessage(json.data as AlreadyRunningData));
        return;
      }
      const newStatus = await loadStatus();
      if (newStatus?.running) startPolling();
    } catch {
      setError('请求失败，请检查后端服务');
    }
  };

  const handleInfoBrief = async (run_type: 'morning' | 'intraday' | 'evening') => {
    setError(null);
    setInfoBriefLoading(true);
    try {
      const res = await fetch('/api/info_brief/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_type }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '信息情报简报启动失败');
        return;
      }
      // 成功, 刷新历史列表
      await loadLatest();
      await loadHistory();
    } catch {
      setError('请求失败，请检查后端服务');
    } finally {
      setInfoBriefLoading(false);
    }
  };

  const handleStrategist = async (run_type: 'morning' | 'intraday' | 'evening') => {
    setError(null);
    setStrategistLoading(true);
    try {
      const res = await fetch('/api/strategist/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_type }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '战略推理启动失败');
        return;
      }
      // 战略推理依赖 info_brief, 后端可能返回 skipped, 检查
      if (json.data?.skipped) {
        setError(json.data?.reason ?? '战略推理被跳过 (无 info_brief 历史)');
        return;
      }
      await loadLatest();
      await loadHistory();
    } catch {
      setError('请求失败，请检查后端服务');
    } finally {
      setStrategistLoading(false);
    }
  };

  // 检查最近是否有 info_brief 记录 (用 history 简单判断)
  const hasInfoBrief = history.some(h => h.run_type === 'info_brief');

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
    policy:   '政策解读',
    research: '研报解读',
    notice:   '公告解读',
    watchlist: '股池动态',
  };
  const lastRunLabel = status?.last_run && status.last_run_type !== 'watchlist'
    ? `${status.last_run}${status.last_run_type ? `（${RUN_TYPE_LABEL_STATUS[status.last_run_type] ?? status.last_run_type}）` : ''}`
    : (status?.last_run ?? undefined);

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

      <InfoBriefPanel running={isRunning || infoBriefLoading} onTrigger={handleInfoBrief} />

      <StrategistPanel running={isRunning || strategistLoading} onTrigger={handleStrategist} hasInfoBrief={hasInfoBrief} />

      <ReviewAiPanel onDone={() => { loadLatest(); loadHistory(); }} />

      {report ? (
        report.has_html && report.id
          ? <HtmlReportView reportId={report.id} />
          : isMdReport(report)
            ? <MdReportView r={report} />
            : isFullReport(report)
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
