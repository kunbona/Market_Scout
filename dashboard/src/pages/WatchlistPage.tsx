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
  Plus,
  Sparkles,
  Star,
  Target,
  Trash2,
  Upload,
  Zap,
} from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { PhaseBar, HtmlReportView, safeFetch, formatAlreadyRunningMessage } from '../components/agentShared';
import { renderMarkdown } from '../lib/markdown';
import type { AgentStatus, AlreadyRunningData } from '../components/agentShared';

// ─── Types ────────────────────────────────────────────────────────────────────

interface WatchlistItem {
  id: number;
  pool: string;
  code: string;
  name: string;
  note: string;
  added_at: string;
  sort: number;
}

// QMT 实时报价字段（/api/watchlist/quote 合并后）
interface QuoteFields {
  last_price: number | null;
  last_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  change: number | null;
  change_pct: number | null;
  has_quote: boolean;
}

interface PoolInfo {
  name: string;
  count: number;
  created_at?: string;
}

interface HistoryItem {
  id: number | string;
  run_time: string;
  run_type: string;
  summary?: string;
  has_html?: boolean;
}

// ─── Watchlist Panel ──────────────────────────────────────────────────────────

interface StockCandidate {
  code: string;
  name: string;
}

interface ImportResult {
  added: number;
  skipped_existing: number;
  failed: Array<{ raw: string; reason: string }>;
  total: number;
  pool?: string;
}

// ─── 个股情报弹窗（研报/财经快讯/政策/智堡聚合 + AI 综合简报）────────
interface IntelSources { research: any[]; news: any[]; policy: any[]; wisburg: any[] }
interface IntelJob {
  state: 'idle' | 'running' | 'done' | 'error';
  result: { markdown?: string; code?: string; name?: string; count?: number; trade_date?: string; run_time?: string } | null;
  error: string | null;
}

function IntelModal({ code, name, onClose }: { code: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<{ sources: IntelSources; checkup: any } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [aiJob, setAiJob] = useState<IntelJob>({ state: 'idle', result: null, error: null });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr('');
    // 后端返回 {code, name, sources:{research,news,policy,wisburg}, checkup:{issues,highlights}}
    safeFetch<{ sources: IntelSources; checkup?: any }>(`/api/watchlist/intel?code=${code}&name=${encodeURIComponent(name)}`)
      .then(d => { if (d?.sources) setData({ sources: d.sources, checkup: d.checkup || {} }); else setErr('情报加载失败'); })
      .catch(() => setErr('情报加载失败'))
      .finally(() => setLoading(false));
  }, [code, name]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const j = await safeFetch<IntelJob>('/api/watchlist/intel/ai-job');
      if (j) {
        setAiJob(j);
        if (j.state !== 'running' && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    }, 3000);
  };

  const handleAi = async () => {
    setAiJob({ state: 'running', result: null, error: null });
    try {
      await fetch('/api/watchlist/intel/ai', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name }),
      });
      startPoll();
    } catch (e: any) {
      setAiJob({ state: 'error', result: null, error: e.message || 'AI 简报启动失败' });
    }
  };

  const renderGroup = (title: string, emoji: string, items: any[] | undefined, renderItem: (it: any) => ReactNode) => {
    const list = items || [];
    return (
      <div className="mb-5">
        <p className="text-xs font-semibold text-gray-500 mb-2">
          {emoji} {title}
          <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500">{list.length}</span>
        </p>
        {list.length === 0 ? (
          <p className="text-xs text-gray-300 pl-1">暂无相关信息</p>
        ) : (
          <div className="space-y-2">
            {list.map((it, i) => (
              <div key={i} className="text-xs text-gray-700 leading-relaxed bg-gray-50/60 border border-gray-50 rounded-lg px-2.5 py-2">
                {renderItem(it)}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">
              {name} <span className="text-sm font-mono text-gray-400 ml-1">{code}</span>
            </h3>
            <p className="text-xs text-gray-400 mt-0.5">情报聚合：券商研报 / 财经快讯 / 政策动态 / 智堡研究</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none shrink-0">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="py-12 text-center text-gray-400 text-sm">情报加载中…</div>
          ) : err ? (
            <div className="py-12 text-center text-red-500 text-sm">⚠️ {err}</div>
          ) : data ? (
            <>
              {/* 股池动态分析的逐股体检 */}
              {data.checkup && ((data.checkup.issues?.length || 0) > 0 || (data.checkup.highlights?.length || 0) > 0) && (
                <div className="mb-5 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
                  {(data.checkup.issues?.length || 0) > 0 && (
                    <div>
                      <p className="text-xs font-semibold text-amber-600 mb-2">⚠️ 问题提醒</p>
                      <div className="space-y-1.5">
                        {data.checkup.issues.map((t: string, i: number) => (
                          <div key={i} className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-2 leading-relaxed">
                            {t}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {(data.checkup.highlights?.length || 0) > 0 && (
                    <div>
                      <p className="text-xs font-semibold text-emerald-600 mb-2">✦ 优势亮点</p>
                      <div className="space-y-1.5">
                        {data.checkup.highlights.map((t: string, i: number) => (
                          <div key={i} className="text-xs text-emerald-800 bg-emerald-50 border border-emerald-100 rounded-lg px-2.5 py-2 leading-relaxed">
                            {t}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
                {renderGroup('券商研报', '📑', data.sources.research, it => (
                  <>
                    <span className="text-gray-900 font-medium">{it.title}</span>
                    <span className="block text-gray-400 mt-0.5">
                      {it.publish_date}
                      {it.org_name && <> · {it.org_name}</>}
                      {it.rating && <span className="text-amber-600"> · {it.rating}</span>}
                      {it.aim_price && <span className="text-blue-600"> · 目标 {it.aim_price}</span>}
                    </span>
                  </>
                ))}
                {renderGroup('财经快讯', '📰', data.sources.news, it => (
                  <>
                    <span className="text-gray-900 font-medium">{it.title}</span>
                    <span className="block text-gray-400 mt-0.5">{it.pub_time} · {it.source}</span>
                  </>
                ))}
                {renderGroup('政策动态', '📋', data.sources.policy, it => (
                  <>
                    <span className="text-gray-900 font-medium">{it.title}</span>
                    <span className="block text-gray-400 mt-0.5">{it.pub_time} · {it.source}</span>
                  </>
                ))}
                {renderGroup('智堡研究', '🏛️', data.sources.wisburg, it => (
                  <>
                    <span className="text-gray-900 font-medium">{it.title}</span>
                    <span className="block text-gray-400 mt-0.5">
                      {String(it.datetime || '').slice(0, 16)} · {it.source_type}
                    </span>
                  </>
                ))}
              </div>
            </>
          ) : null}

          {/* AI 综合简报 */}
          <div className="mt-4 border-t border-gray-100 pt-4">
            {aiJob.state === 'running' && (
              <div className="p-4 bg-indigo-50/50 border border-indigo-100 rounded-xl text-sm text-indigo-600 animate-pulse">
                🤖 claude 正在生成综合简报（约 1-3 分钟）…
              </div>
            )}
            {aiJob.state === 'done' && aiJob.result?.markdown && (
              <div>
                <p className="text-xs font-semibold text-indigo-600 mb-2">🤖 AI 综合简报</p>
                <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiJob.result.markdown) }} />
              </div>
            )}
            {aiJob.state === 'error' && aiJob.error && (
              <div className="p-4 bg-red-50 border border-red-100 rounded-xl text-sm text-red-600">
                ⚠️ AI 简报失败：{aiJob.error}
              </div>
            )}
          </div>
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-end">
          <button onClick={handleAi} disabled={aiJob.state === 'running'}
                  className="accent-solid px-4 py-2 text-sm rounded-lg disabled:opacity-50">
            {aiJob.state === 'running' ? '生成中…' : '🤖 AI 综合简报'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── 全池情报总览（一键整理：全部关注股票的情报一起看）────────────
function IntelOverviewModal({ stocks, onClose }: { stocks: WatchlistItem[]; onClose: () => void }) {
  const [results, setResults] = useState<Record<string, { code: string; name: string; sources: IntelSources; checkup: any }>>({});
  const [progress, setProgress] = useState(0);
  const total = stocks.length;
  const list = Object.values(results);
  // 每只股票展开/收起
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // AI 全池整理 job
  const [aiAll, setAiAll] = useState<IntelJob>({ state: 'idle', result: null, error: null });
  const aiAllPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // iFinD 实时体检 job
  const [realtimeJob, setRealtimeJob] = useState<{ state: string; result: any; error: string | null }>({ state: 'idle', result: null, error: null });
  const [realtimeCheckups, setRealtimeCheckups] = useState<Record<string, any>>({});
  const rtPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 影响推演 job（市场数据 → 个股 影响映射 + 情景推演）
  const [impactJob, setImpactJob] = useState<IntelJob>({ state: 'idle', result: null, error: null });
  const impactPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let done = 0;
    const map: Record<string, { code: string; name: string; sources: IntelSources; checkup: any }> = {};
    const empty: IntelSources = { research: [], news: [], policy: [], wisburg: [] };
    stocks.forEach(s => {
      safeFetch<{ sources: IntelSources; checkup?: any }>(`/api/watchlist/intel?code=${s.code}&name=${encodeURIComponent(s.name || s.code)}`)
        .then(d => {
          map[s.code] = { code: s.code, name: s.name, sources: d?.sources || empty, checkup: d?.checkup || {} };
          done++;
          setResults({ ...map });
          setProgress(done);
        })
        .catch(() => {
          map[s.code] = { code: s.code, name: s.name, sources: empty, checkup: {} };
          done++;
          setResults({ ...map });
          setProgress(done);
        });
    });
  }, [stocks]);

  const renderBrief = (label: string, items: any[], fmt: (it: any) => string, showAll: boolean) => (
    <div className="text-xs">
      <span className="font-semibold text-gray-500">{label}</span>
      {items.length === 0 ? (
        <span className="text-gray-300 ml-2">无</span>
      ) : (
        <div className="mt-1 space-y-0.5">
          {(showAll ? items : items.slice(0, 3)).map((it, i) => (
            <div key={i} className="text-gray-600 truncate" title={fmt(it)}>· {fmt(it)}</div>
          ))}
          {!showAll && items.length > 3 && <div className="text-gray-400">… 共 {items.length} 条</div>}
        </div>
      )}
    </div>
  );

  const startAiAllPoll = () => {
    if (aiAllPollRef.current) clearInterval(aiAllPollRef.current);
    aiAllPollRef.current = setInterval(async () => {
      const j = await safeFetch<IntelJob>('/api/watchlist/intel/ai-all-job');
      if (j) {
        setAiAll(j);
        if (j.state !== 'running' && aiAllPollRef.current) { clearInterval(aiAllPollRef.current); aiAllPollRef.current = null; }
      }
    }, 3000);
  };

  const handleAiAll = async () => {
    setAiAll({ state: 'running', result: null, error: null });
    try {
      await fetch('/api/watchlist/intel/ai-all', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stocks: stocks.map(s => ({ code: s.code, name: s.name })) }),
      });
      startAiAllPoll();
    } catch (e: any) {
      setAiAll({ state: 'error', result: null, error: e.message || 'AI 整理启动失败' });
    }
  };

  // iFinD 实时体检：后台逐只拿行情/公告/新闻（复用股池动态分析的数据源，不用等 agent）
  const handleRealtime = async () => {
    setRealtimeJob({ state: 'running', result: null, error: null });
    setRealtimeCheckups({});
    try {
      await fetch('/api/watchlist/intel/realtime', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stocks: stocks.map(s => ({ code: s.code, name: s.name })) }),
      });
      if (rtPollRef.current) clearInterval(rtPollRef.current);
      rtPollRef.current = setInterval(async () => {
        const j = await safeFetch<any>('/api/watchlist/intel/realtime-job');
        if (j) {
          setRealtimeJob(j);
          if (j.result?.checkups) setRealtimeCheckups(j.result.checkups);
          if (j.state !== 'running' && rtPollRef.current) { clearInterval(rtPollRef.current); rtPollRef.current = null; }
        }
      }, 2500);
    } catch (e: any) {
      setRealtimeJob({ state: 'error', result: null, error: e.message || '实时体检启动失败' });
    }
  };

  // 影响推演：市场数据(行业趋势/龙虎榜/解禁/总览结论) → 个股影响评估+情景推演
  const startImpactPoll = () => {
    if (impactPollRef.current) clearInterval(impactPollRef.current);
    impactPollRef.current = setInterval(async () => {
      const j = await safeFetch<IntelJob>('/api/watchlist/impact-job');
      if (j) {
        setImpactJob(j);
        if (j.state !== 'running' && impactPollRef.current) { clearInterval(impactPollRef.current); impactPollRef.current = null; }
      }
    }, 3000);
  };

  const handleImpact = async () => {
    setImpactJob({ state: 'running', result: null, error: null });
    try {
      await fetch('/api/watchlist/impact', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stocks: stocks.map(s => ({ code: s.code, name: s.name, note: s.note })) }),
      });
      startImpactPoll();
    } catch (e: any) {
      setImpactJob({ state: 'error', result: null, error: e.message || '影响推演启动失败' });
    }
  };

  // 打开时回看最近一次落库的推演结果
  useEffect(() => {
    safeFetch<{ analysis_md: string; run_time?: string; trade_date?: string } | null>('/api/watchlist/impact-latest')
      .then(snap => {
        if (snap?.analysis_md) {
          setImpactJob({ state: 'done', result: { markdown: snap.analysis_md, run_time: snap.run_time, trade_date: snap.trade_date }, error: null });
        }
      })
      .catch(() => {});
    return () => { if (impactPollRef.current) clearInterval(impactPollRef.current); };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">📊 全池情报总览</h3>
            <p className="text-xs text-gray-400 mt-0.5">
              {progress}/{total} 只已整理 · 券商研报 / 财经快讯 / 政策动态 / 智堡研究
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleRealtime}
              disabled={realtimeJob.state === 'running'}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                         bg-blue-100 text-blue-700 hover:bg-blue-200 ring-1 ring-blue-300
                         transition-colors disabled:opacity-50"
            >
              {realtimeJob.state === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
              {realtimeJob.state === 'running'
                ? `实时体检 ${realtimeJob.result?.done ?? 0}/${realtimeJob.result?.count ?? stocks.length}…`
                : '⚡ 实时体检'}
            </button>
            <button
              onClick={handleAiAll}
              disabled={aiAll.state === 'running'}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                         bg-indigo-100 text-indigo-700 hover:bg-indigo-200 ring-1 ring-indigo-300
                         transition-colors disabled:opacity-50"
            >
              {aiAll.state === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
              {aiAll.state === 'running' ? 'AI 整理中…' : 'AI 全池整理'}
            </button>
            <button
              onClick={handleImpact}
              disabled={impactJob.state === 'running'}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                         bg-purple-100 text-purple-700 hover:bg-purple-200 ring-1 ring-purple-300
                         transition-colors disabled:opacity-50"
            >
              {impactJob.state === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Target className="w-3.5 h-3.5" />}
              {impactJob.state === 'running' ? '影响推演中…' : '影响推演'}
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">✕</button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {progress < total && (
            <div className="mb-4">
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(progress / Math.max(total, 1)) * 100}%` }} />
              </div>
              <p className="text-xs text-gray-400 mt-1">正在拉取智堡研报（外部接口较慢）…</p>
            </div>
          )}

          {/* AI 全池整理结果 */}
          {aiAll.state === 'running' && (
            <div className="mb-4 p-4 bg-indigo-50/50 border border-indigo-100 rounded-xl text-sm text-indigo-600 animate-pulse">
              🤖 claude 正在做全池横向整理（约 1-3 分钟）…
            </div>
          )}
          {aiAll.state === 'done' && aiAll.result?.markdown && (
            <div className="mb-4 bg-indigo-50/40 border border-indigo-100 rounded-xl p-4">
              <p className="text-xs font-semibold text-indigo-600 mb-2">🤖 AI 全池整理</p>
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiAll.result.markdown) }} />
            </div>
          )}
          {aiAll.state === 'error' && aiAll.error && (
            <div className="mb-4 p-4 bg-red-50 border border-red-100 rounded-xl text-sm text-red-600">
              ⚠️ AI 整理失败：{aiAll.error}
            </div>
          )}

          {/* 影响推演结果（市场数据 → 个股 影响映射 + 情景推演） */}
          {impactJob.state === 'running' && (
            <div className="mb-4 p-4 bg-purple-50/50 border border-purple-100 rounded-xl text-sm text-purple-600 animate-pulse">
              🎯 claude 正在做市场数据 → 个股影响映射与情景推演（约 3-5 分钟）…
            </div>
          )}
          {impactJob.state === 'done' && impactJob.result?.markdown && (
            <div className="mb-4 bg-purple-50/40 border border-purple-100 rounded-xl p-4">
              <p className="text-xs font-semibold text-purple-600 mb-2">
                🎯 影响推演 — 市场数据 → 股池个股（{impactJob.result.trade_date ? `数据截面 ${impactJob.result.trade_date}` : '最新'}{impactJob.result.run_time ? ` · 生成于 ${impactJob.result.run_time}` : ''}）
              </p>
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(impactJob.result.markdown) }} />
            </div>
          )}
          {impactJob.state === 'error' && impactJob.error && (
            <div className="mb-4 p-4 bg-red-50 border border-red-100 rounded-xl text-sm text-red-600">
              ⚠️ 影响推演失败：{impactJob.error}
            </div>
          )}

          <div className="space-y-3">
            {list.map(x => {
              const s = x.sources;
              const isExp = !!expanded[x.code];
              return (
                <div key={x.code} className="border border-gray-100 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2 gap-2">
                    <span className="text-sm font-semibold text-gray-900">
                      {x.name} <span className="text-xs font-mono text-gray-400">{x.code}</span>
                    </span>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-[10px] text-gray-400">
                        研报 {s.research.length} · 快讯 {s.news.length} · 政策 {s.policy.length} · 智堡 {s.wisburg.length}
                      </span>
                      <button
                        onClick={() => setExpanded(prev => ({ ...prev, [x.code]: !prev[x.code] }))}
                        className="text-[10px] text-indigo-500 hover:text-indigo-700 font-medium shrink-0"
                      >
                        {isExp ? '收起' : '展开全部'}
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                    {renderBrief('研报', s.research, it => `${it.title}${it.rating ? `（${it.rating}）` : ''}`, isExp)}
                    {renderBrief('快讯', s.news, it => it.title, isExp)}
                    {renderBrief('政策', s.policy, it => it.title, isExp)}
                    {renderBrief('智堡', s.wisburg, it => it.title, isExp)}
                  </div>
                  {/* iFinD 实时体检（⚡ 按钮触发后显示，优先于落库） */}
                  {(() => {
                    const rt = realtimeCheckups[x.code];
                    if (!rt || ((rt.issues?.length || 0) === 0 && (rt.highlights?.length || 0) === 0)) return null;
                    return (
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-[10px] font-semibold text-blue-600">⚡ iFinD 实时体检</span>
                          {rt.change_pct != null && (
                            <span className={`text-xs font-semibold ${rt.change_pct >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                              {rt.change_pct >= 0 ? '+' : ''}{rt.change_pct.toFixed(2)}%
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                          {(rt.issues?.length || 0) > 0 && (
                            <div className="text-xs">
                              <span className="font-semibold text-amber-600">⚠️ 问题提醒</span>
                              <div className="mt-1 space-y-1">
                                {rt.issues.map((t: string, i: number) => (
                                  <div key={i} className="text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2 py-1.5 leading-relaxed">{t}</div>
                                ))}
                              </div>
                            </div>
                          )}
                          {(rt.highlights?.length || 0) > 0 && (
                            <div className="text-xs">
                              <span className="font-semibold text-emerald-600">✦ 优势亮点</span>
                              <div className="mt-1 space-y-1">
                                {rt.highlights.map((t: string, i: number) => (
                                  <div key={i} className="text-emerald-800 bg-emerald-50 border border-emerald-100 rounded-lg px-2 py-1.5 leading-relaxed">{t}</div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* 股池动态分析的逐股体检（问题提醒 / 优势亮点） */}
                  {x.checkup && ((x.checkup.issues?.length || 0) > 0 || (x.checkup.highlights?.length || 0) > 0) && (
                    <div className="mt-3 pt-3 border-t border-gray-100 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                      {(x.checkup.issues?.length || 0) > 0 && (
                        <div className="text-xs">
                          <span className="font-semibold text-amber-600">⚠️ 问题提醒</span>
                          <div className="mt-1 space-y-1">
                            {x.checkup.issues.map((t: string, i: number) => (
                              <div key={i} className="text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2 py-1.5 leading-relaxed">
                                {t}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {(x.checkup.highlights?.length || 0) > 0 && (
                        <div className="text-xs">
                          <span className="font-semibold text-emerald-600">✦ 优势亮点</span>
                          <div className="mt-1 space-y-1">
                            {x.checkup.highlights.map((t: string, i: number) => (
                              <div key={i} className="text-emerald-800 bg-emerald-50 border border-emerald-100 rounded-lg px-2 py-1.5 leading-relaxed">
                                {t}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function WatchlistPanel({
  running,
  onTrigger,
}: {
  running: boolean;
  onTrigger: (type: 'watchlist', pool?: string) => void;
}) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [pools, setPools] = useState<PoolInfo[]>([]);
  const [currentPool, setCurrentPool] = useState<string>('全部');
  const [codeInput, setCodeInput] = useState('');
  const [noteInput, setNoteInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 实时报价 (QMT tick 合并到 watchlist)
  const [quoteMap, setQuoteMap] = useState<Record<string, QuoteFields>>({});
  const [quoteUpdatedAt, setQuoteUpdatedAt] = useState<string | null>(null);
  const [bridgeState, setBridgeState] = useState<string | null>(null);
  const quoteTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 个股情报弹窗
  const [intelStock, setIntelStock] = useState<{ code: string; name: string } | null>(null);
  // 全池情报总览（一键整理）
  const [overviewOpen, setOverviewOpen] = useState(false);

  // 池管理
  const [manageOpen, setManageOpen] = useState(false);
  const [newPoolName, setNewPoolName] = useState('');

  // 联想下拉
  const [suggestions, setSuggestions] = useState<StockCandidate[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [multiCandidates, setMultiCandidates] = useState<StockCandidate[]>([]);
  const suppressSuggestRef = useRef(false);

  // 导入
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 添加 / 导入的目标池：'全部'视图下落'默认'
  const targetPool = currentPool === '全部' ? '默认' : currentPool;

  const loadPools = async () => {
    const data = await safeFetch<PoolInfo[]>('/api/pools');
    if (data) setPools(data);
  };

  const load = async (pool?: string) => {
    const p = pool ?? currentPool;
    const url = p === '全部' ? '/api/watchlist' : `/api/watchlist?pool=${encodeURIComponent(p)}`;
    const data = await safeFetch<WatchlistItem[]>(url);
    if (data) setItems(data);
    await loadPools();
  };

  useEffect(() => { load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPool]);

  // 实时报价：每 5s 拉一次，QMT 拿全 watchlist tick
  useEffect(() => {
    let cancelled = false;
    const fetchQuote = async () => {
      const url = currentPool === '全部'
        ? '/api/watchlist/quote'
        : `/api/watchlist/quote?pool=${encodeURIComponent(currentPool)}`;
      const data = await safeFetch<{
        items: Array<WatchlistItem & QuoteFields>;
        quote_time: string | null;
        quote_count: number;
        total_count: number;
        bridge_state: string | null;
      }>(url);
      if (cancelled || !data) return;
      const next: Record<string, QuoteFields> = {};
      for (const it of data.items) {
        next[it.code] = {
          last_price: it.last_price,
          last_close: it.last_close,
          open: it.open,
          high: it.high,
          low: it.low,
          volume: it.volume,
          amount: it.amount,
          change: it.change,
          change_pct: it.change_pct,
          has_quote: it.has_quote,
        };
      }
      setQuoteMap(next);
      setQuoteUpdatedAt(new Date().toISOString());
      setBridgeState(data.bridge_state);
    };
    fetchQuote();
    if (quoteTimerRef.current) clearInterval(quoteTimerRef.current);
    quoteTimerRef.current = setInterval(fetchQuote, 5000);
    return () => {
      cancelled = true;
      if (quoteTimerRef.current) {
        clearInterval(quoteTimerRef.current);
        quoteTimerRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPool, items.length]);

  const totalCount = pools.reduce((s, p) => s + p.count, 0);

  // 输入联想（debounce 300ms）
  useEffect(() => {
    if (suppressSuggestRef.current) {
      suppressSuggestRef.current = false;
      return;
    }
    const q = codeInput.trim();
    if (!q) {
      setSuggestions([]);
      setDropdownOpen(false);
      return;
    }
    const timer = setTimeout(async () => {
      const data = await safeFetch<StockCandidate[]>(`/api/watchlist/search?q=${encodeURIComponent(q)}`);
      if (data) {
        setSuggestions(data);
        setDropdownOpen(data.length > 0);
        setActiveIdx(-1);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [codeInput]);

  // ── 池管理操作 ──
  const handleCreatePool = async () => {
    const name = newPoolName.trim();
    if (!name) return;
    setError(null);
    try {
      const res = await fetch('/api/pools', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '创建失败'); return; }
      setNewPoolName('');
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const handleRenamePool = async (name: string) => {
    const newName = window.prompt(`将股池「${name}」改名为：`, name);
    if (!newName || newName.trim() === name) return;
    setError(null);
    try {
      const res = await fetch(`/api/pools/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName.trim() }),
      });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '改名失败'); return; }
      if (currentPool === name) setCurrentPool(newName.trim());
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const handleDeletePool = async (name: string) => {
    if (!window.confirm(`确定删除股池「${name}」？池内所有股票将一并删除，不可恢复。`)) return;
    setError(null);
    try {
      const res = await fetch(`/api/pools/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '删除失败'); return; }
      if (currentPool === name) setCurrentPool('全部');
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const addStock = async (payload: { code?: string; q?: string }) => {
    setAdding(true);
    setError(null);
    setMultiCandidates([]);
    try {
      const res = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, note: noteInput.trim(), pool: targetPool }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '添加失败');
        return;
      }
      if (json.data?.multiple) {
        setMultiCandidates(json.data.candidates ?? []);
        return;
      }
      setCodeInput('');
      setNoteInput('');
      setSuggestions([]);
      setDropdownOpen(false);
      await load();
    } catch {
      setError('请求失败，请检查后端服务');
    } finally {
      setAdding(false);
    }
  };

  const handleAdd = () => {
    const q = codeInput.trim();
    if (!q) return;
    addStock({ q });
  };

  const handleSelectSuggestion = (c: StockCandidate) => {
    suppressSuggestRef.current = true;
    setDropdownOpen(false);
    setSuggestions([]);
    addStock({ code: c.code });
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (dropdownOpen && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx(i => Math.min(i + 1, suggestions.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx(i => Math.max(i - 1, -1));
        return;
      }
      if (e.key === 'Escape') {
        setDropdownOpen(false);
        return;
      }
      if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        handleSelectSuggestion(suggestions[activeIdx]);
        return;
      }
    }
    if (e.key === 'Enter') handleAdd();
  };

  const handleRemove = async (item: WatchlistItem) => {
    try {
      await fetch(`/api/watchlist/${item.code}?pool=${encodeURIComponent(item.pool)}`, { method: 'DELETE' });
      await load();
    } catch {
      // graceful: ignore
    }
  };

  const submitImport = async (init: { formData?: FormData; text?: string }) => {
    setImporting(true);
    setError(null);
    try {
      if (init.formData) init.formData.append('pool', targetPool);
      const res = await fetch('/api/watchlist/import', init.formData
        ? { method: 'POST', body: init.formData }
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: init.text, pool: targetPool }),
          });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '导入失败');
        return;
      }
      setImportResult(json.data as ImportResult);
      setPasteText('');
      await load();
    } catch {
      setError('导入请求失败，请检查后端服务');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const tabCls = (active: boolean) =>
    `px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
      active ? 'bg-rose-100 text-rose-700 ring-1 ring-rose-300' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
    }`;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] mb-6">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Star className="w-4 h-4 text-rose-400" />
        <h3 className="text-sm font-semibold text-gray-800">关注股池</h3>
        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
          {currentPool === '全部' ? totalCount : items.length} 只
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setManageOpen(o => !o)}
            title="新建 / 改名 / 删除股池"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                       bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                       transition-colors"
          >
            {manageOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            管理股池
          </button>
          <button
            onClick={() => onTrigger('watchlist', currentPool === '全部' ? undefined : currentPool)}
            disabled={running || items.length === 0}
            title={items.length === 0 ? '请先添加关注股票' : `AI 逐只体检${currentPool === '全部' ? '全部股池' : `「${currentPool}」`}：问题提醒 + 优势亮点`}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                       bg-rose-100 text-rose-700 hover:bg-rose-200 ring-1 ring-rose-300
                       transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Star className="w-3.5 h-3.5" />}
            股池动态分析{currentPool !== '全部' && ` · ${currentPool}`}
          </button>
          <button
            onClick={() => setOverviewOpen(true)}
            disabled={items.length === 0}
            title="一键整理全部关注股票的情报（研报/快讯/政策/智堡）一起看"
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                       bg-indigo-100 text-indigo-700 hover:bg-indigo-200 ring-1 ring-indigo-300
                       transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Sparkles className="w-3.5 h-3.5" />
            一键整理
          </button>
        </div>
      </div>

      {/* 池切换 Tab */}
      <div className="flex items-center gap-1.5 mb-4 flex-wrap">
        <button onClick={() => setCurrentPool('全部')} className={tabCls(currentPool === '全部')}>
          全部（{totalCount}）
        </button>
        {pools.map(p => (
          <button key={p.name} onClick={() => setCurrentPool(p.name)} className={tabCls(currentPool === p.name)}>
            {p.name}（{p.count}）
          </button>
        ))}
      </div>

      {/* 池管理面板 */}
      {manageOpen && (
        <div className="mb-4 bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <input
              value={newPoolName}
              onChange={e => setNewPoolName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreatePool(); }}
              placeholder="新池名称，如 策略A（≤20字）"
              maxLength={20}
              className="w-52 px-3 py-2 rounded-lg border border-gray-200 text-sm
                         focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
            />
            <button
              onClick={handleCreatePool}
              disabled={!newPoolName.trim()}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                         bg-gray-900 text-white hover:bg-gray-700 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Plus className="w-3.5 h-3.5" />
              创建
            </button>
          </div>
          <div className="space-y-1.5">
            {pools.map(p => (
              <div key={p.name} className="flex items-center gap-2 text-sm">
                <span className="font-semibold text-gray-800">{p.name}</span>
                <span className="text-xs text-gray-400">{p.count} 只</span>
                {p.name !== '默认' && (
                  <span className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => handleRenamePool(p.name)}
                      className="px-2 py-1 rounded-md text-xs text-gray-500 hover:bg-gray-200 transition-colors"
                    >改名</button>
                    <button
                      onClick={() => handleDeletePool(p.name)}
                      className="px-2 py-1 rounded-md text-xs text-red-500 hover:bg-red-50 transition-colors"
                    >删除</button>
                  </span>
                )}
                {p.name === '默认' && <span className="ml-auto text-xs text-gray-300">内置池</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 添加表单（搜索联想） */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative">
          <input
            value={codeInput}
            onChange={e => setCodeInput(e.target.value)}
            onKeyDown={handleInputKeyDown}
            onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
            onFocus={() => { if (suggestions.length > 0) setDropdownOpen(true); }}
            placeholder="代码 / 拼音简写 / 名称，如 gzmt"
            maxLength={20}
            className="w-52 px-3 py-2 rounded-lg border border-gray-200 text-sm font-mono
                       focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
          />
          {dropdownOpen && suggestions.length > 0 && (
            <div className="absolute z-20 mt-1 w-64 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
              {suggestions.map((s, idx) => (
                <button
                  key={s.code}
                  onMouseDown={e => { e.preventDefault(); handleSelectSuggestion(s); }}
                  onMouseEnter={() => setActiveIdx(idx)}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                    idx === activeIdx ? 'bg-rose-50' : 'hover:bg-gray-50'
                  }`}
                >
                  <span className="font-semibold text-gray-900">{s.name || '未知名称'}</span>
                  <span className="text-xs font-mono text-gray-400">{s.code}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <input
          value={noteInput}
          onChange={e => setNoteInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd(); }}
          placeholder="备注（可选），如：成本 1300"
          maxLength={50}
          className="flex-1 min-w-[160px] px-3 py-2 rounded-lg border border-gray-200 text-sm
                     focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
        />
        <button
          onClick={handleAdd}
          disabled={adding || !codeInput.trim()}
          title={`添加到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                     bg-gray-900 text-white hover:bg-gray-700 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {adding ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          添加到{targetPool}
        </button>
      </div>

      {/* 多命中候选 */}
      {multiCandidates.length > 0 && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <div className="text-xs text-amber-600 font-semibold mb-2">找到多个匹配，请选择：</div>
          <div className="flex flex-wrap gap-2">
            {multiCandidates.map(c => (
              <button
                key={c.code}
                onClick={() => addStock({ code: c.code })}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                           bg-white border border-amber-200 text-gray-700 hover:bg-amber-100 transition-colors"
              >
                <span className="font-semibold">{c.name || '未知名称'}</span>
                <span className="font-mono text-gray-400">{c.code}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 导入区 */}
      <div className="flex items-center gap-2 mb-4">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.txt"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (!file) return;
            const fd = new FormData();
            fd.append('file', file);
            submitImport({ formData: fd });
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={importing}
          title={`从 CSV/TXT 文件批量导入到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                     bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                     transition-colors disabled:opacity-50"
        >
          {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          导入 CSV → {targetPool}
        </button>
        <button
          onClick={() => setPasteOpen(o => !o)}
          title={`粘贴股票列表文本批量导入到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                     bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                     transition-colors"
        >
          {pasteOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          批量粘贴导入
        </button>
      </div>

      {/* 批量粘贴导入 */}
      {pasteOpen && (
        <div className="mb-4 bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-2">
            导入到「{targetPool}」池。每行一只股票，支持 600519 / 600519.SH / sh600519 等写法；可带表头（code,note）；# 开头为注释行
          </div>
          <textarea
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            placeholder={'code,note\n600519,成本 1300\n300750.SZ\nsz002594'}
            rows={5}
            className="w-full px-3 py-2 rounded-lg border border-gray-200 text-xs font-mono
                       focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300 mb-2"
          />
          <button
            onClick={() => { if (pasteText.trim()) submitImport({ text: pasteText }); }}
            disabled={importing || !pasteText.trim()}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                       bg-gray-900 text-white hover:bg-gray-700 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            导入
          </button>
        </div>
      )}

      {/* 导入结果提示条 */}
      {importResult && (
        <div className="mb-4 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-emerald-800">
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
            <span>
              导入{importResult.pool ? `「${importResult.pool}」` : ''}完成：新增 <b>{importResult.added}</b> 只，
              跳过已存在 <b>{importResult.skipped_existing}</b> 只，
              失败 <b>{importResult.failed.length}</b> 行
              （共解析 {importResult.total} 行）
            </span>
            <button
              onClick={() => setImportResult(null)}
              className="ml-auto text-emerald-500 hover:text-emerald-700 font-semibold"
            >×</button>
          </div>
          {importResult.failed.length > 0 && (
            <div className="mt-2 space-y-1">
              {importResult.failed.map((f, i) => (
                <div key={i} className="text-xs text-red-600 font-mono truncate">
                  ✗ {f.raw} — {f.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700 mb-4">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* 股池列表 */}
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">
          {currentPool === '全部' ? '股池为空' : `「${currentPool}」池为空`}，输入代码 / 拼音简写 / 名称添加关注股票（如 gzmt → 贵州茅台）
        </p>
      ) : (
        <div className="divide-y divide-gray-50">
          {/* 表头：跟数据列对齐, ml-auto + gap-3 跟 list 行一致 */}
          <div className="flex items-center gap-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
            <span className="shrink-0">名称</span>
            <span className="shrink-0">代码</span>
            {currentPool === '全部' && <span className="shrink-0">池</span>}
            <span className="max-w-[200px] truncate">备注</span>
            <div className="ml-auto flex items-center gap-3 shrink-0">
              <span className="w-16 text-right">现价</span>
              <span className="w-16 text-right">涨跌幅</span>
              <span className="w-16 text-right">涨跌额</span>
            </div>
            <span className="w-7 shrink-0" />
          </div>
          {items.map(item => {
            const q = quoteMap[item.code];
            // A 股红涨绿跌
            const chgPct = q?.change_pct ?? null;
            const chg = q?.change ?? null;
            const lastPrice = q?.last_price ?? null;
            const isUp = (chgPct ?? 0) > 0;
            const isDown = (chgPct ?? 0) < 0;
            const chgColor = !q?.has_quote
              ? 'text-gray-300'
              : isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-gray-700';
            return (
              <div key={`${item.pool}-${item.code}`} className="flex items-center gap-3 py-2.5">
                <span className="text-sm font-semibold text-gray-900 shrink-0">
                  {item.name || <span className="text-gray-300">未知名称</span>}
                </span>
                <span className="text-xs font-mono text-gray-400 shrink-0">{item.code}</span>
                {currentPool === '全部' && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-rose-50 text-rose-500 font-medium shrink-0">
                    {item.pool}
                  </span>
                )}
                {item.note && (
                  <span className="text-xs text-gray-500 bg-gray-50 border border-gray-100 px-2 py-0.5 rounded-md truncate max-w-[200px]">
                    {item.note}
                  </span>
                )}

                {/* 实时报价三联 (QMT) */}
                <div className="ml-auto flex items-center gap-3 shrink-0 tabular-nums">
                  <span className={`text-sm font-mono font-semibold ${chgColor}`} title="现价 (QMT)">
                    {lastPrice == null ? '—' : lastPrice.toFixed(lastPrice >= 100 ? 2 : 2)}
                  </span>
                  <span className={`text-sm font-mono font-semibold w-16 text-right ${chgColor}`} title="涨跌幅 (QMT)">
                    {chgPct == null ? '—' : `${isUp ? '+' : ''}${chgPct.toFixed(2)}%`}
                  </span>
                  <span className={`text-xs font-mono w-16 text-right ${chgColor}`} title="涨跌额 (QMT)">
                    {chg == null ? '—' : `${isUp ? '+' : ''}${chg.toFixed(2)}`}
                  </span>
                </div>

                <button
                  onClick={() => setIntelStock({ code: item.code, name: item.name || item.code })}
                  title="查看情报（研报/快讯/政策/智堡聚合 + AI 简报）"
                  className="shrink-0 p-1.5 rounded-lg text-gray-300
                             hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                </button>

                <button
                  onClick={() => handleRemove(item)}
                  title={`移出「${item.pool}」池`}
                  className="shrink-0 p-1.5 rounded-lg text-gray-300
                             hover:text-red-500 hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 报价状态条 */}
      {items.length > 0 && (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-gray-400">
          <span>
            报价: {bridgeState === 'closed' ? 'QMT 连接正常' : bridgeState ? `QMT 桥 ${bridgeState}` : 'QMT 检测中...'}
          </span>
          {quoteUpdatedAt && (
            <span title={quoteUpdatedAt}>
              · 刷新于 {new Date(quoteUpdatedAt).toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
          )}
        </div>
      )}

      {/* 个股情报弹窗 */}
      {intelStock && (
        <IntelModal
          code={intelStock.code}
          name={intelStock.name}
          onClose={() => setIntelStock(null)}
        />
      )}

      {/* 全池情报总览 */}
      {overviewOpen && (
        <IntelOverviewModal
          stocks={items}
          onClose={() => setOverviewOpen(false)}
        />
      )}
    </div>
  );
}

// ─── Watchlist History Panel ──────────────────────────────────────────────────

function WatchlistHistoryPanel({ items }: { items: HistoryItem[] }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | string | null>(null);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-semibold text-gray-800">股池分析历史</span>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
            {items.length} 条
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </button>

      {open && (
        <div className="border-t border-gray-100">
          {items.length === 0 ? (
            <div className="px-6 py-8 text-center text-sm text-gray-400">暂无股池分析记录</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {items.map(item => {
                const isExpanded = expanded === item.id;
                return (
                  <div key={item.id}>
                    <button
                      onClick={() => setExpanded(isExpanded ? null : item.id)}
                      className="w-full flex items-center gap-3 px-6 py-3.5 hover:bg-gray-50 transition-colors text-left"
                    >
                      <span className="shrink-0 text-xs px-2 py-0.5 rounded font-semibold bg-rose-50 text-rose-600">
                        股池动态
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
                        {item.has_html && typeof item.id === 'number' ? (
                          <HtmlReportView reportId={item.id} />
                        ) : item.summary ? (
                          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{item.summary}</p>
                        ) : (
                          <div className="py-6 text-center text-sm text-gray-400">该记录无可用内容</div>
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

class WatchlistErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
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

function WatchlistPageInner() {
  const [status, setStatus]     = useState<AgentStatus | null>(null);
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

  const loadHistory = async () => {
    const raw = await safeFetch<Array<{ id: number | string; run_time: string; run_type: string; summary_text?: string; summary_time?: string; has_html?: boolean }>>('/api/agent/history?limit=50');
    if (raw) {
      setHistory(raw
        .filter(item => item.run_type === 'watchlist')
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
        await loadHistory();
      }
    }, 2000);
  };

  useEffect(() => {
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

  const handleTrigger = async (run_type: 'watchlist', pool?: string) => {
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
      // 已有其他管道在跑（盘中 / 盘后 / 研报 / 政策 …）：后端返回 success=true
      // 但 data.status='already_running'，给用户友好提示而不是显示别家的进度条
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
        if (s && !s.running) await loadHistory();
      }
    }, 2000);
    stopConfirmRef.current = confirm;
  };

  const isRunning = status?.running ?? false;

  const lastRunLabel = status?.last_run && status.last_run_type === 'watchlist'
    ? `${status.last_run}（股池动态）`
    : undefined;

  const latestReport = history.length > 0 ? history[0] : null;

  return (
    <div className="space-y-0">
      <TabHeader
        title="关注股池"
        subtitle="股池管理与 AI 股池动态分析"
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

      <WatchlistPanel running={isRunning} onTrigger={handleTrigger} />

      {/* 最新股池分析结果 */}
      {latestReport ? (
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Star className="w-4 h-4 text-rose-400" />
            <h3 className="text-sm font-semibold text-gray-800">最新股池分析结果</h3>
            <span className="text-xs font-mono text-gray-400">{latestReport.run_time}</span>
          </div>
          {latestReport.has_html && typeof latestReport.id === 'number' ? (
            <HtmlReportView reportId={latestReport.id} />
          ) : (
            <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
              <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{latestReport.summary ?? ''}</p>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-gray-100 p-12 shadow-[var(--shadow-sm)]
                        flex flex-col items-center justify-center gap-3 text-center mb-6">
          <Brain className="w-10 h-10 text-gray-200" />
          <p className="text-sm font-semibold text-gray-400">暂无股池分析报告</p>
          <p className="text-xs text-gray-300">点击「股池动态分析」按钮启动 AI 逐只体检</p>
        </div>
      )}

      <div className="mt-6">
        <WatchlistHistoryPanel items={history} />
      </div>
    </div>
  );
}

export function WatchlistPage() {
  return (
    <WatchlistErrorBoundary>
      <WatchlistPageInner />
    </WatchlistErrorBoundary>
  );
}
