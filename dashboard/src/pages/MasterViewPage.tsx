import { useEffect, useRef, useState, Component } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronUp,
  Clock,
  Compass,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { safeFetch } from '../components/agentShared';
import { MdReportView } from '../components/MdReportView';
import type { MdReport } from '../components/MdReportView';

// ─── Types ────────────────────────────────────────────────────────────────────

interface JobState {
  state: 'idle' | 'running' | 'done' | 'error';
  progress?: string;
  result?: { status?: string; trade_date?: string; summary?: string; md_len?: number } | null;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

interface HistoryItem {
  id: number;
  summary_time: string;
  content: string;
}

// data_snapshot_json 解析后的形状 (agent/master_view.py run() 落库)
interface MasterSnapshot extends MdReport {
  digest_used?: { trade_date?: string; sources?: string[] };
}

// 后端 sources 嵌在 digest_used 里, 展平到顶层供 MdReportView 渲染徽章
function normalizeSnap(s: MasterSnapshot): MdReport {
  const { digest_used, ...rest } = s;
  return { ...rest, sources: digest_used?.sources ?? s.sources };
}

// ─── Error Boundary ───────────────────────────────────────────────────────────

class MasterViewErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
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

// ─── History Panel (只列总览分析, 展开读快照) ─────────────────────────────────

function MasterHistoryPanel({ items, activeId, onOpen }: {
  items: HistoryItem[];
  activeId: number | null;
  onOpen: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-semibold text-gray-800">历史总览报告</span>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
            {items.length} 条
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </button>

      {open && (
        <div className="border-t border-gray-100 divide-y divide-gray-50">
          {items.length === 0 ? (
            <div className="px-6 py-8 text-center text-sm text-gray-400">暂无历史记录</div>
          ) : (
            items.map(item => (
              <button
                key={item.id}
                onClick={() => onOpen(item.id)}
                className={`w-full flex items-center gap-3 px-6 py-3.5 hover:bg-gray-50 transition-colors text-left ${
                  activeId === item.id ? 'bg-indigo-50/50' : ''
                }`}
              >
                <span className="shrink-0 text-xs px-2 py-0.5 rounded font-semibold bg-gradient-to-r from-indigo-100 to-blue-100 text-indigo-700">
                  总览
                </span>
                <span className="shrink-0 text-xs font-mono text-gray-500">{item.summary_time}</span>
                {item.content && (
                  <span className="text-xs text-gray-400 truncate flex-1">{item.content}</span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

function MasterViewPageInner() {
  const [snapshot, setSnapshot] = useState<MasterSnapshot | null>(null);
  const [snapshotId, setSnapshotId] = useState<number | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [job, setJob] = useState<JobState | null>(null);
  const [failMsg, setFailMsg] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPoll = () => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
  };
  useEffect(() => stopPoll, []);

  const loadLatest = async () => {
    const snap = await safeFetch<MasterSnapshot>('/api/master-view/latest');
    if (snap) {
      setSnapshot(snap);
      setSnapshotId(null); // 最新视图不绑定历史 id
    }
  };

  const loadHistory = async () => {
    const rows = await safeFetch<HistoryItem[]>('/api/master-view/history');
    if (rows) setHistory(rows);
  };

  const loadJob = async () => {
    const j = await safeFetch<JobState>('/api/master-view/job');
    if (j) setJob(j);
    return j;
  };

  const startPoll = () => {
    stopPoll();
    pollRef.current = window.setInterval(async () => {
      const j = await loadJob();
      if (!j) return;
      if (j.state !== 'running') {
        stopPoll();
        if (j.state === 'error') setFailMsg(j.error || '生成失败，请看后端日志');
        else {
          setFailMsg(null);
          await loadLatest();
          await loadHistory();
        }
      }
    }, 3000);
  };

  useEffect(() => {
    loadLatest();
    loadHistory();
    // 页面打开时如果服务里有在跑的 job, 恢复轮询 (切页再切回来不丢进度)
    loadJob().then(j => { if (j?.state === 'running') startPoll(); });
    return stopPoll;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trigger = async () => {
    setFailMsg(null);
    try {
      const resp = await fetch('/api/master-view/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const json = await resp.json().catch(() => null);
      if (!resp.ok || !json?.success) {
        setFailMsg(json?.error ?? `HTTP ${resp.status}`);
        return;
      }
      await loadJob();
      startPoll();
    } catch {
      setFailMsg('请求失败，请检查后端服务');
    }
  };

  const openHistoryItem = async (id: number) => {
    // 复用 agent 历史详情接口 (同一张表), 取 data_snapshot_json
    const data = await safeFetch<MasterSnapshot>(`/api/agent/history/${id}`);
    if (data) {
      setSnapshot(data);
      setSnapshotId(id);
    }
  };

  const running = job?.state === 'running';

  return (
    <div className="space-y-0">
      <TabHeader
        title="总览 AI 分析"
        subtitle="行业趋势 × 复盘 × 资金流 × 专题 × 智堡海外视角 — 全数据源顶层交叉分析"
        lastUpdate={snapshot?.run_time ? `最近一次 ${snapshot.run_time}` : undefined}
      />

      {failMsg && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-800 mb-6">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{failMsg}</span>
        </div>
      )}

      {/* 触发面板 */}
      <div className="mb-6 p-5 bg-gradient-to-r from-indigo-600 via-blue-600 to-indigo-600 rounded-2xl shadow-[var(--shadow-sm)]">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2.5">
            <Compass className="w-5 h-5 text-white/90" />
            <div>
              <div className="text-sm font-semibold text-white">全景总览分析</div>
              <div className="text-xs text-indigo-200 mt-0.5">
                {running
                  ? (job?.progress || '正在生成中，约需 1-2 分钟...')
                  : '汇总行业趋势截面(31行业) + 当日复盘 + DM-kun 6专题 + 板块资金流 + 智堡海外视角 + 既有AI结论'}
              </div>
            </div>
          </div>
          <button
            onClick={trigger}
            disabled={running}
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold bg-white text-indigo-700
                       hover:bg-indigo-50 transition-colors shadow disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {running
              ? <><Loader2 className="w-4 h-4 animate-spin" />生成中...</>
              : <><RefreshCw className="w-4 h-4" />生成总览分析</>}
          </button>
        </div>
      </div>

      {/* 正文 */}
      {snapshot ? (
        <MdReportView r={{ ...normalizeSnap(snapshot), id: snapshotId ?? undefined, report_title: '总览 AI 分析' }} />
      ) : (
        <div className="bg-white rounded-2xl border border-gray-100 p-12 shadow-[var(--shadow-sm)]
                        flex flex-col items-center justify-center gap-3 text-center mb-6">
          <Brain className="w-10 h-10 text-gray-200" />
          <p className="text-sm font-semibold text-gray-400">暂无总览分析报告</p>
          <p className="text-xs text-gray-300">点击「生成总览分析」按钮，让 AI 综合全部数据源做一次顶层判断</p>
        </div>
      )}

      <div className="mt-6">
        <MasterHistoryPanel items={history} activeId={snapshotId} onOpen={openHistoryItem} />
      </div>
    </div>
  );
}

export function MasterViewPage() {
  return (
    <MasterViewErrorBoundary>
      <MasterViewPageInner />
    </MasterViewErrorBoundary>
  );
}
