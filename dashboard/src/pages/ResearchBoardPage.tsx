import { useState, useEffect, useRef } from 'react';
import { Plus, Search, Trash2, Play, RefreshCw, ChevronDown, ChevronUp, ChevronLeft, BookOpen, BarChart2, AlertTriangle, TrendingUp, Layers, Download, Upload, FileText } from 'lucide-react';
import { AnalysisViewer, type AnalysisTab } from '../components/AnalysisViewer';

const BASE = '';

async function rbFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, opts);
  const json = await res.json();
  if (!json.success) throw new Error(json.error ?? 'API error');
  return json.data as T;
}

// ── 类型 ─────────────────────────────────────────────────────────────────

interface RbProject {
  id: number;
  name: string;
  keywords: string[];
  dimensions: string[];
  days_back: number;
  qtype_filter: number[];
  status: string;
  report_count: number;
  created_at: string;
  updated_at: string;
}

interface AnalysisProgress {
  status: string;
  phase: string;
  message: string;
  done_dimensions: string[];
  total_dimensions: number;
  step: number;
  total_steps: number;
  steps: string[];
}

interface KeyStock {
  name: string;
  code: string;
  why: string;
  rating: string;
  target_price: string;
  risk: string;
  priority: string;
}

interface ChainSegment {
  segment: string;
  key_players: string[];
  bottleneck: string;
  opportunity: string;
}

interface RiskItem {
  risk: string;
  probability: string;
  impact: string;
  timeline: string;
}

interface DimensionResult {
  dimension?: string;
  key_findings?: { point: string; detail: string; confidence: string }[];
  data_points?: { metric: string; value: string; source: string; date: string }[];
  stock_mentions?: { name: string; code: string; context: string; rating: string; target_price: string }[];
  risks?: string[];
  summary?: string;
  error?: string;
  raw_text?: string;
}

interface BoardResult {
  // New format (rb_analyzer.py)
  tabs?: AnalysisTab[];
  sub_modules?: string[];
  extra_dimensions?: string[];
  dimensions?: string[];
  // Legacy fields (analyzer.py)
  project_name?: string;
  generated_at?: string;
  report_count?: number;
  executive_summary?: string;
  investment_thesis?: {
    bull_case: string;
    bear_case: string;
    time_horizon: string;
    verdict: string;
  };
  key_stocks?: KeyStock[];
  industry_chain?: ChainSegment[];
  risk_matrix?: RiskItem[];
  data_freshness?: string;
}

// ── 默认维度 ───────────────────────────────────────────────────────────────

// 默认维度和 blueprint.py DEFAULT_DIMENSIONS 保持同步
// 总览 tab 由 Claude 自动生成，不需要单独列为维度
const DEFAULT_DIMENSIONS = [
  '成本构成与降本路径',
  '竞争格局与核心标的',
  '替代风险分析',
  '估值与盈利预测',
  '产业里程碑与催化剂',
];
const DEFAULT_DAYS = 180;
const QTYPE_LABELS: Record<number, string> = { 0: '个股', 1: '行业', 2: '宏观', 3: '策略' };

const STATUS_COLOR: Record<string, string> = {
  idle: 'text-gray-500',
  fetching: 'text-blue-600',
  analyzing: 'text-indigo-600',
  done: 'text-green-600',
  error: 'text-red-500',
};

const STATUS_LABEL: Record<string, string> = {
  idle: '待机',
  fetching: '抓取中',
  analyzing: '分析中',
  done: '已完成',
  error: '出错',
};

const PRIORITY_COLOR: Record<string, string> = {
  核心标的: 'bg-red-50 text-red-700 border-red-200',
  次要标的: 'bg-amber-50 text-amber-700 border-amber-200',
  观察标的: 'bg-blue-50 text-blue-700 border-blue-200',
};

const RISK_COLOR: Record<string, string> = {
  高: 'text-red-600',
  中: 'text-amber-600',
  低: 'text-green-600',
};

const CONFIDENCE_DOT: Record<string, string> = {
  high: 'bg-green-500',
  medium: 'bg-amber-400',
  low: 'bg-gray-400',
};

// ── 子组件 ────────────────────────────────────────────────────────────────

function Badge({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${className}`}>
      {children}
    </span>
  );
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

function SectionTitle({ icon: Icon, title, count }: { icon: React.ElementType; title: string; count?: number }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon className="w-4 h-4 text-indigo-500 flex-shrink-0" />
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      {count !== undefined && (
        <span className="ml-auto text-xs text-gray-400">{count} 条</span>
      )}
    </div>
  );
}

// 单个维度展开面板
function DimensionPanel({ name, result }: { name: string; result: DimensionResult }) {
  const [open, setOpen] = useState(false);
  const hasError = !!result.error;
  const findings = result.key_findings ?? [];
  const dataPoints = result.data_points ?? [];
  const stocks = result.stock_mentions ?? [];

  return (
    <Card className="overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
      >
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${hasError ? 'bg-red-400' : 'bg-indigo-500'}`} />
        <span className="text-sm font-medium text-gray-800 flex-1">{name}</span>
        {result.summary && (
          <span className="text-xs text-gray-400 truncate max-w-xs hidden sm:block">{result.summary?.slice(0, 40)}…</span>
        )}
        {open ? <ChevronUp className="w-4 h-4 text-gray-400 flex-shrink-0" /> : <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />}
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 space-y-4 pt-3">
          {hasError && (
            <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{result.error}</p>
          )}

          {result.summary && (
            <p className="text-sm text-gray-700 leading-relaxed bg-indigo-50 rounded-lg px-3 py-2">{result.summary}</p>
          )}

          {findings.length > 0 && (
            <div>
              <SectionTitle icon={TrendingUp} title="核心发现" count={findings.length} />
              <div className="space-y-2">
                {findings.map((f, i) => (
                  <div key={i} className="flex gap-2 items-start">
                    <span className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${CONFIDENCE_DOT[f.confidence] ?? 'bg-gray-400'}`} />
                    <div>
                      <p className="text-sm font-medium text-gray-800">{f.point}</p>
                      <p className="text-xs text-gray-500 mt-0.5">{f.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {dataPoints.length > 0 && (
            <div>
              <SectionTitle icon={BarChart2} title="数据点" count={dataPoints.length} />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {dataPoints.map((d, i) => (
                  <div key={i} className="bg-gray-50 rounded-lg px-3 py-2">
                    <p className="text-xs text-gray-500">{d.metric}</p>
                    <p className="text-sm font-semibold text-gray-900">{d.value}</p>
                    <p className="text-xs text-gray-400">{d.source} · {d.date}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {stocks.length > 0 && (
            <div>
              <SectionTitle icon={BookOpen} title="提及标的" count={stocks.length} />
              <div className="flex flex-wrap gap-2">
                {stocks.map((s, i) => (
                  <div key={i} className="bg-white border border-gray-200 rounded-lg px-3 py-1.5 text-xs">
                    <span className="font-semibold text-gray-800">{s.name}</span>
                    {s.code && <span className="ml-1 text-gray-400">{s.code}</span>}
                    {s.rating && <Badge className="ml-1.5 bg-blue-50 text-blue-700 border-blue-200">{s.rating}</Badge>}
                    {s.target_price && <span className="ml-1 text-green-600">→{s.target_price}</span>}
                    {s.context && <p className="text-gray-500 mt-0.5">{s.context}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.risks && result.risks.length > 0 && (
            <div>
              <SectionTitle icon={AlertTriangle} title="风险" count={result.risks.length} />
              <ul className="space-y-1">
                {result.risks.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                    <span className="text-amber-500 mt-0.5">▸</span>{r}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// 看板主体
function BoardView({ result }: { result: BoardResult }) {
  // New format: render tabs via AnalysisViewer
  if (result.tabs && result.tabs.length > 0) {
    return (
      <div className="flex flex-col h-full gap-3">
        {/* 元信息栏 — 紧凑单行 */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-sm font-semibold text-gray-900">{result.project_name}</span>
          <span className="text-xs text-gray-400">
            {result.report_count} 篇研报 · {result.generated_at}
          </span>
        </div>
        {/* iframe 区域撑满剩余高度 */}
        <div className="flex-1 min-h-0">
          <AnalysisViewer
            tabs={result.tabs}
            frameHeight="100%"
          />
        </div>
      </div>
    );
  }

  // Legacy JSON format
  return (
    <div className="space-y-5">
      {/* 概览 */}
      <Card className="p-4">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">{result.project_name}</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              基于 {result.report_count} 篇研报 · 生成于 {result.generated_at}
              {result.data_freshness && ` · ${result.data_freshness}`}
            </p>
          </div>
          {result.investment_thesis?.verdict && (
            <Badge className={
              result.investment_thesis.verdict.includes('积极') ? 'bg-green-50 text-green-700 border-green-200' :
              result.investment_thesis.verdict.includes('回避') ? 'bg-red-50 text-red-700 border-red-200' :
              'bg-amber-50 text-amber-700 border-amber-200'
            }>
              {result.investment_thesis.verdict}
            </Badge>
          )}
        </div>
        {result.executive_summary && (
          <p className="text-sm text-gray-700 leading-relaxed">{result.executive_summary}</p>
        )}
      </Card>

      {/* 多空逻辑 */}
      {result.investment_thesis && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Card className="p-4 border-l-4 border-green-400">
            <p className="text-xs font-semibold text-green-600 mb-2">多方逻辑</p>
            <p className="text-sm text-gray-700">{result.investment_thesis.bull_case}</p>
          </Card>
          <Card className="p-4 border-l-4 border-red-400">
            <p className="text-xs font-semibold text-red-500 mb-2">空方逻辑</p>
            <p className="text-sm text-gray-700">{result.investment_thesis.bear_case}</p>
          </Card>
          {result.investment_thesis.time_horizon && (
            <Card className="p-3 sm:col-span-2 bg-gray-50">
              <span className="text-xs text-gray-500">建议持有周期：</span>
              <span className="text-sm font-medium text-gray-800 ml-1">{result.investment_thesis.time_horizon}</span>
            </Card>
          )}
        </div>
      )}

      {/* 重点标的 */}
      {result.key_stocks && result.key_stocks.length > 0 && (
        <Card className="p-4">
          <SectionTitle icon={TrendingUp} title="重点关注标的" count={result.key_stocks.length} />
          <div className="space-y-3">
            {result.key_stocks.map((s, i) => (
              <div key={i} className="flex gap-3 items-start border-b border-gray-100 pb-3 last:border-0 last:pb-0">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-gray-900 text-sm">{s.name}</span>
                    {s.code && <span className="text-xs text-gray-400">{s.code}</span>}
                    {s.priority && (
                      <Badge className={PRIORITY_COLOR[s.priority] ?? 'bg-gray-50 text-gray-600 border-gray-200'}>
                        {s.priority}
                      </Badge>
                    )}
                    {s.rating && <Badge className="bg-blue-50 text-blue-700 border-blue-200">{s.rating}</Badge>}
                    {s.target_price && <span className="text-xs text-green-600 font-medium">目标价 {s.target_price}</span>}
                  </div>
                  <p className="text-xs text-gray-600 mt-1">{s.why}</p>
                  {s.risk && <p className="text-xs text-amber-600 mt-0.5">⚠ {s.risk}</p>}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* 产业链 */}
      {result.industry_chain && result.industry_chain.length > 0 && (
        <Card className="p-4">
          <SectionTitle icon={Layers} title="产业链环节" count={result.industry_chain.length} />
          <div className="space-y-3">
            {result.industry_chain.map((seg, i) => (
              <div key={i} className="bg-gray-50 rounded-lg p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-semibold text-gray-800">{seg.segment}</span>
                  {seg.key_players?.length > 0 && (
                    <div className="flex gap-1 flex-wrap">
                      {seg.key_players.map((p, j) => (
                        <Badge key={j} className="bg-white text-gray-700 border-gray-300">{p}</Badge>
                      ))}
                    </div>
                  )}
                </div>
                {seg.bottleneck && <p className="text-xs text-red-600">核心壁垒：{seg.bottleneck}</p>}
                {seg.opportunity && <p className="text-xs text-green-600 mt-0.5">{seg.opportunity}</p>}
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* 各维度（仅旧格式有，新格式维度在 tabs 里） */}
      {result.dimensions && !Array.isArray(result.dimensions) && Object.keys(result.dimensions as Record<string, DimensionResult>).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-indigo-400" />
            各维度深度分析
          </h3>
          <div className="space-y-2">
            {Object.entries(result.dimensions as Record<string, DimensionResult>).map(([name, dim]) => (
              <DimensionPanel key={name} name={name} result={dim} />
            ))}
          </div>
        </div>
      )}

      {/* 风险矩阵 */}
      {result.risk_matrix && result.risk_matrix.length > 0 && (
        <Card className="p-4">
          <SectionTitle icon={AlertTriangle} title="风险矩阵" count={result.risk_matrix.length} />
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-100">
                  <th className="text-left pb-2 font-medium">风险</th>
                  <th className="text-left pb-2 font-medium">概率</th>
                  <th className="text-left pb-2 font-medium">影响</th>
                  <th className="text-left pb-2 font-medium">时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.risk_matrix.map((r, i) => (
                  <tr key={i} className="py-1.5">
                    <td className="py-1.5 pr-3 text-gray-700">{r.risk}</td>
                    <td className={`py-1.5 pr-3 font-medium ${RISK_COLOR[r.probability] ?? 'text-gray-600'}`}>{r.probability}</td>
                    <td className={`py-1.5 pr-3 font-medium ${RISK_COLOR[r.impact] ?? 'text-gray-600'}`}>{r.impact}</td>
                    <td className="py-1.5 text-gray-500">{r.timeline}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

// ── 新建项目表单 ──────────────────────────────────────────────────────────

function NewProjectForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState('');
  const [keywords, setKeywords] = useState('');
  const [daysBack, setDaysBack] = useState(String(DEFAULT_DAYS));
  const [dimensions, setDimensions] = useState(DEFAULT_DIMENSIONS.join('\n'));
  const [qtypes, setQtypes] = useState<number[]>([0, 1, 2, 3]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const toggleQtype = (q: number) => {
    setQtypes(prev => prev.includes(q) ? prev.filter(x => x !== q) : [...prev, q]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setErr('请填写项目名称'); return; }
    setLoading(true);
    setErr('');
    try {
      await rbFetch('/api/rb/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          keywords: keywords.split(/[,，\n]/).map(k => k.trim()).filter(Boolean),
          dimensions: dimensions.split('\n').map(d => d.trim()).filter(Boolean),
          days_back: parseInt(daysBack) || DEFAULT_DAYS,
          qtype_filter: qtypes,
        }),
      });
      onCreated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">项目名称 *</label>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="如：人型机器人赛道研究"
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">搜索关键词（逗号或换行分隔）</label>
        <input
          value={keywords}
          onChange={e => setKeywords(e.target.value)}
          placeholder="人型机器人, 人形机器人, humanoid"
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <p className="text-xs text-gray-400 mt-1">留空则抓取所选类型下全部研报</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1">时间范围（天）</label>
          <input
            type="number"
            value={daysBack}
            onChange={e => setDaysBack(e.target.value)}
            min={7} max={730}
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-2">研报类型</label>
          <div className="flex flex-wrap gap-2">
            {([0, 1, 2, 3] as const).map(q => (
              <button
                key={q}
                type="button"
                onClick={() => toggleQtype(q)}
                className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                  qtypes.includes(q)
                    ? 'bg-indigo-600 text-white border-indigo-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:border-indigo-400'
                }`}
              >
                {QTYPE_LABELS[q]}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">分析维度（每行一个）</label>
        <textarea
          value={dimensions}
          onChange={e => setDimensions(e.target.value)}
          rows={5}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400 font-mono"
        />
      </div>

      {err && <p className="text-xs text-red-500">{err}</p>}

      <button
        type="submit"
        disabled={loading}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-xl transition-colors disabled:opacity-60"
      >
        <Plus className="w-4 h-4" />
        {loading ? '创建中...' : '创建项目'}
      </button>
    </form>
  );
}

// ── 项目卡片 ───────────────────────────────────────────────────────────────

function ProjectCard({
  project,
  isSelected,
  onSelect,
  onDelete,
  onFetch,
  onAnalyze,
}: {
  project: RbProject;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onFetch: (downloadPdf: boolean) => void;
  onAnalyze: () => void;
}) {
  const [fetchState, setFetchState] = useState<{ status: string; message: string }>({ status: 'idle', message: '' });
  const [analyzeProgress, setAnalyzeProgress] = useState<AnalysisProgress | null>(null);
  // 抓取和分析用独立 ref，互不干扰
  const fetchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const analyzePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopFetchPoll = () => { if (fetchPollRef.current) { clearInterval(fetchPollRef.current); fetchPollRef.current = null; } };
  const stopAnalyzePoll = () => { if (analyzePollRef.current) { clearInterval(analyzePollRef.current); analyzePollRef.current = null; } };

  const pollFetch = (onDone?: () => void) => {
    stopFetchPoll();
    fetchPollRef.current = setInterval(async () => {
      try {
        const d = await rbFetch<{ status: string; message: string }>(`/api/rb/projects/${project.id}/fetch-status`);
        setFetchState(d);
        if (d.status === 'done' || d.status === 'error') {
          stopFetchPoll();
          onDone?.();
        }
      } catch { stopFetchPoll(); onDone?.(); }
    }, 1000);
  };

  const pollAnalyze = (onDone?: () => void) => {
    stopAnalyzePoll();
    let failCount = 0;
    analyzePollRef.current = setInterval(async () => {
      try {
        const d = await rbFetch<AnalysisProgress>(`/api/rb/projects/${project.id}/analyze-status`);
        failCount = 0;
        setAnalyzeProgress(d);
        if (d.status === 'done' || d.status === 'error') {
          stopAnalyzePoll();
          onDone?.();
        }
      } catch {
        failCount++;
        if (failCount >= 3) {
          stopAnalyzePoll();
          setAnalyzeProgress(prev => ({
            status: 'error',
            phase: prev?.phase ?? '分析中',
            message: '后端无响应（连续 3 次请求失败），进程可能已崩溃。请检查后端日志后重新分析。',
            done_dimensions: prev?.done_dimensions ?? [],
            total_dimensions: prev?.total_dimensions ?? 0,
            step: prev?.step ?? 0,
            total_steps: prev?.total_steps ?? 5,
            steps: prev?.steps ?? [],
          }));
        }
      }
    }, 1500);
  };

  // 清理
  useEffect(() => () => { stopFetchPoll(); stopAnalyzePoll(); }, []);

  // mount 时：若后端仍在运行（刷新/tab 切换后回来），自动恢复进度轮询
  useEffect(() => {
    if (project.status === 'analyzing') {
      pollAnalyze(() => onAnalyze());
    } else if (project.status === 'fetching') {
      pollFetch(() => onFetch(true));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFetch = async (downloadPdf: boolean) => {
    try {
      await rbFetch(`/api/rb/projects/${project.id}/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ download_pdf: downloadPdf, max_pdf: 100 }),
      });
      // 完成后回调 onFetch 刷新项目列表（更新 report_count 显示）
      pollFetch(() => onFetch(downloadPdf));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败');
    }
  };

  const handleAnalyze = async () => {
    try {
      await rbFetch(`/api/rb/projects/${project.id}/analyze`, { method: 'POST' });
      pollAnalyze(() => onAnalyze());
      onAnalyze();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败');
    }
  };

  const isFetching = fetchState.status === 'running';
  const isAnalyzing = analyzeProgress?.status === 'analyzing';
  const statusLabel = STATUS_COLOR[project.status] ?? 'text-gray-500';

  return (
    <Card className={`p-4 cursor-pointer transition-shadow hover:shadow-md ${isSelected ? 'ring-2 ring-indigo-500' : ''}`}>
      <div onClick={onSelect} className="mb-3">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-semibold text-gray-900 leading-tight">{project.name}</h3>
          <span className={`text-xs font-medium flex-shrink-0 ${statusLabel}`}>{STATUS_LABEL[project.status] ?? project.status}</span>
        </div>
        <div className="flex flex-wrap gap-1 mt-2">
          {project.keywords.slice(0, 4).map((k, i) => (
            <Badge key={i} className="bg-indigo-50 text-indigo-700 border-indigo-200">{k}</Badge>
          ))}
          {project.keywords.length > 4 && (
            <Badge className="bg-gray-50 text-gray-500 border-gray-200">+{project.keywords.length - 4}</Badge>
          )}
        </div>
        <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
          <span>{project.report_count} 篇研报</span>
          <span>{project.days_back} 天</span>
          <span>{project.dimensions.length} 维度</span>
        </div>
      </div>

      {/* 进度条 */}
      {analyzeProgress && (analyzeProgress.status === 'analyzing' || analyzeProgress.status === 'error') && (() => {
        const isError = analyzeProgress.status === 'error';
        const steps = analyzeProgress.steps?.length ? analyzeProgress.steps : ['准备数据', 'Claude 拆解维度', 'Kimi 并行分析', '生成研究背景', '完成'];
        const totalSteps = steps.length;
        const currentStep = analyzeProgress.step ?? 0;
        const isKimiPhase = currentStep === 3; // "Kimi 并行分析" 是第3步
        const dimTotal = analyzeProgress.total_dimensions;
        const dimDone = analyzeProgress.done_dimensions.length;

        // 总进度：步骤粒度。Kimi 阶段内部再细分维度子进度
        let pct: number;
        if (isError) {
          pct = 0;
        } else if (isKimiPhase && dimTotal > 0) {
          const stepBase = (currentStep - 1) / totalSteps * 100;
          const stepSize = 1 / totalSteps * 100;
          pct = stepBase + (dimDone / dimTotal) * stepSize;
        } else {
          pct = Math.min((currentStep - 1) / totalSteps * 100, 95);
        }

        return (
          <div className={`mb-3 rounded-lg px-3 pt-2.5 pb-2 text-xs ${isError ? 'bg-red-50 border border-red-100' : 'bg-indigo-50 border border-indigo-100'}`}>
            {/* 步骤指示器 */}
            {!isError && (
              <div className="flex items-center gap-0 mb-2">
                {steps.map((s, i) => {
                  const stepNum = i + 1;
                  const done = stepNum < currentStep;
                  const active = stepNum === currentStep;
                  return (
                    <div key={s} className="flex items-center min-w-0" style={{ flex: i < steps.length - 1 ? '1' : 'none' }}>
                      <div className="flex flex-col items-center flex-shrink-0" style={{ minWidth: 0 }}>
                        <div
                          className="w-5 h-5 rounded-full flex items-center justify-center font-bold transition-all duration-300"
                          style={{
                            background: done ? '#4f46e5' : active ? '#6366f1' : '#c7d2fe',
                            color: done || active ? '#fff' : '#818cf8',
                            fontSize: 9,
                            boxShadow: active ? '0 0 0 3px #c7d2fe' : 'none',
                          }}
                        >
                          {done ? '✓' : stepNum}
                        </div>
                        <span
                          className="mt-0.5 text-center leading-tight"
                          style={{
                            fontSize: 9,
                            color: done ? '#4f46e5' : active ? '#6366f1' : '#a5b4fc',
                            fontWeight: active ? 700 : 400,
                            maxWidth: 52,
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {s}
                        </span>
                      </div>
                      {i < steps.length - 1 && (
                        <div
                          className="flex-1 mx-1 transition-all duration-500"
                          style={{
                            height: 2,
                            background: done ? '#4f46e5' : '#c7d2fe',
                            borderRadius: 1,
                            marginBottom: 14,
                          }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* 总进度条 */}
            {!isError && (
              <div className="h-1 bg-indigo-200 rounded-full overflow-hidden mb-1.5">
                <div
                  className="h-full bg-indigo-600 transition-all duration-500"
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}

            {/* 当前消息 */}
            {analyzeProgress.message && (
              <p className={`leading-relaxed break-all mt-1 ${isError ? 'text-red-600' : 'text-indigo-500'}`}>
                {isError ? '✗ ' : ''}{analyzeProgress.message}
              </p>
            )}

            {/* 已完成维度 tag（仅 Kimi 阶段） */}
            {!isError && dimDone > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {analyzeProgress.done_dimensions.map(dim => (
                  <span key={dim} className="px-1.5 py-0.5 rounded font-medium" style={{ background: '#e0e7ff', color: '#3730a3' }}>
                    ✓ {dim}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })()}

      {isFetching && (
        <div className="mb-3 bg-blue-50 rounded-lg px-3 py-2 text-xs text-blue-600">
          <RefreshCw className="w-3 h-3 inline mr-1 animate-spin" />
          {fetchState.message || '正在抓取...'}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => handleFetch(true)}
          disabled={isFetching || isAnalyzing}
          title="抓取元数据 + 下载 PDF 全文"
          className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
        >
          <Search className="w-3.5 h-3.5" />
          抓取全文
        </button>
        <button
          onClick={handleAnalyze}
          disabled={isFetching || isAnalyzing || project.report_count === 0}
          title="启动 Kimi 分析所有维度"
          className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          <Play className="w-3.5 h-3.5" />
          分析
        </button>
        <button
          onClick={e => { e.stopPropagation(); window.location.href = `/api/rb/projects/${project.id}/export`; }}
          disabled={isFetching || isAnalyzing}
          title="导出项目（ZIP）"
          className="p-1.5 text-gray-400 hover:text-indigo-500 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={e => { e.stopPropagation(); window.location.href = `/api/rb/projects/${project.id}/export-html`; }}
          disabled={isFetching || isAnalyzing || project.status !== 'done'}
          title="导出为单页 HTML（离线查看）"
          className="p-1.5 text-gray-400 hover:text-indigo-500 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
        >
          <FileText className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          disabled={isFetching || isAnalyzing}
          title="删除项目"
          className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </Card>
  );
}

// ── 主页面 ─────────────────────────────────────────────────────────────────

export function ResearchBoardPage() {
  const [projects, setProjects] = useState<RbProject[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [boardResult, setBoardResult] = useState<BoardResult | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [loadingResult, setLoadingResult] = useState(false);
  const [err, setErr] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState('');
  const importInputRef = useRef<HTMLInputElement>(null);

  const loadProjects = async () => {
    try {
      const data = await rbFetch<RbProject[]>('/api/rb/projects');
      setProjects(data);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    }
  };

  const loadResult = async (pid: number) => {
    setLoadingResult(true);
    setBoardResult(null);
    try {
      const data = await rbFetch<{ data?: BoardResult; summary?: BoardResult }>(`/api/rb/projects/${pid}/result`);
      // New format: data.data.tabs; legacy: data.summary
      setBoardResult((data.data ?? data.summary ?? null) as BoardResult | null);
    } catch {
      setBoardResult(null);
    } finally {
      setLoadingResult(false);
    }
  };

  const handleSelect = (pid: number) => {
    setSelectedId(pid);
    loadResult(pid);
  };

  const handleDelete = async (pid: number) => {
    if (!confirm('确定删除该项目及所有分析结果？')) return;
    try {
      await rbFetch(`/api/rb/projects/${pid}`, { method: 'DELETE' });
      setProjects(prev => prev.filter(p => p.id !== pid));
      if (selectedId === pid) { setSelectedId(null); setBoardResult(null); }
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败');
    }
  };

  useEffect(() => { loadProjects(); }, []);

  // 分析完成后自动刷新结果
  const handleAnalyzeStarted = (pid: number) => {
    const poll = setInterval(async () => {
      try {
        const prog = await rbFetch<AnalysisProgress>(`/api/rb/projects/${pid}/analyze-status`);
        if (prog.status === 'done') {
          clearInterval(poll);
          loadProjects();
          if (selectedId === pid) loadResult(pid);
        } else if (prog.status === 'error') {
          clearInterval(poll);
          loadProjects();
        }
      } catch { clearInterval(poll); }
    }, 2000);
  };

  const handleImport = async (file: File) => {
    setImporting(true);
    setImportMsg('导入中…');
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/rb/projects/import', { method: 'POST', body: form });
      const json = await res.json();
      if (!res.ok || !json.success) throw new Error(json.error || '导入失败');
      const { project_name, report_count } = json.data;
      setImportMsg(`已导入「${project_name}」（${report_count} 篇研报）`);
      await loadProjects();
      setTimeout(() => setImportMsg(''), 4000);
    } catch (e) {
      setImportMsg(e instanceof Error ? e.message : '导入失败');
      setTimeout(() => setImportMsg(''), 4000);
    } finally {
      setImporting(false);
      if (importInputRef.current) importInputRef.current.value = '';
    }
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左侧：项目列表（可折叠） */}
      <div
        className={`flex-shrink-0 flex flex-col border-r border-gray-200 bg-gray-50 transition-all duration-200 ${
          sidebarOpen ? 'w-80' : 'w-0 overflow-hidden border-r-0'
        }`}
      >
        <div className="p-4 border-b border-gray-200 bg-white">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-gray-900">投研项目</h2>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => importInputRef.current?.click()}
                disabled={importing}
                title="导入项目（.zip）"
                className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
              >
                <Upload className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setShowNewForm(v => !v)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                新建
              </button>
            </div>
          </div>
          <p className="text-xs text-gray-400">基于研报的赛道深度分析</p>
          {importMsg && (
            <p className={`text-xs mt-1 ${importMsg.includes('失败') ? 'text-red-500' : 'text-indigo-600'}`}>
              {importMsg}
            </p>
          )}
          <input
            ref={importInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleImport(f); }}
          />
        </div>

        {/* 新建表单 */}
        {showNewForm && (
          <div className="p-4 border-b border-gray-200 bg-white overflow-y-auto max-h-[60vh]">
            <NewProjectForm onCreated={() => { setShowNewForm(false); loadProjects(); }} />
          </div>
        )}

        {/* 项目列表 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {err && <p className="text-xs text-red-500 px-2">{err}</p>}
          {projects.length === 0 && !showNewForm && (
            <div className="text-center py-12 text-gray-400">
              <BookOpen className="w-8 h-8 mx-auto mb-2 opacity-40" />
              <p className="text-xs">还没有项目</p>
              <p className="text-xs mt-1">点击「新建」创建第一个研究项目</p>
            </div>
          )}
          {projects.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              isSelected={selectedId === p.id}
              onSelect={() => handleSelect(p.id)}
              onDelete={() => handleDelete(p.id)}
              onFetch={() => { loadProjects(); }}
              onAnalyze={() => { loadProjects(); handleAnalyzeStarted(p.id); }}
            />
          ))}
        </div>
      </div>

      {/* 折叠按钮 — 垂直居中，贴在左右分界处 */}
      <div className="flex-shrink-0 flex items-center self-stretch">
        <button
          onClick={() => setSidebarOpen(v => !v)}
          title={sidebarOpen ? '收起项目列表' : '展开项目列表'}
          className="w-5 h-12 flex items-center justify-center bg-white border border-gray-200 border-l-0 rounded-r-lg shadow-sm hover:bg-indigo-50 hover:border-indigo-200 transition-colors"
        >
          <ChevronLeft
            className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${sidebarOpen ? '' : 'rotate-180'}`}
          />
        </button>
      </div>

      {/* 右侧：看板 — flex col，boardResult 时撑满高度不滚动外层 */}
      <div className={`flex-1 flex flex-col overflow-hidden ${boardResult ? 'p-4' : 'overflow-y-auto p-5'}`}>
        {!selectedId && (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <div className="text-center">
              <BarChart2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">选择左侧项目查看分析看板</p>
              <p className="text-xs mt-1">或新建项目开始研究</p>
            </div>
          </div>
        )}

        {selectedId && loadingResult && (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            <RefreshCw className="w-6 h-6 animate-spin" />
          </div>
        )}

        {selectedId && !loadingResult && !boardResult && (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            <div className="text-center">
              <Play className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">暂无分析结果</p>
              <p className="text-xs mt-1">请先抓取研报，然后点击「分析」</p>
            </div>
          </div>
        )}

        {selectedId && !loadingResult && boardResult && (
          <div className="flex-1 min-h-0">
            <BoardView
              result={boardResult}
            />
          </div>
        )}
      </div>
    </div>
  );
}
