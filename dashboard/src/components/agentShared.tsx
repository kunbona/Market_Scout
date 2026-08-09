import { useEffect, useRef, useState, useCallback } from 'react';
import type { ElementType } from 'react';
import { Brain, Loader2, Square, Users } from 'lucide-react';

// ─── 共享类型与组件：AgentPage（AI 智能分析）与 WatchlistPage（关注股池）共用 ───

export interface AgentStatus {
  running: boolean;
  phase: 'analysts' | 'chief' | null;
  phase_detail: string | null;
  last_run: string | null;
  last_run_type: string | null;
  last_error: string | null;
  pid: number | null;
}

// run_type → 中文标签。AgentPage / WatchlistPage 共用，避免两处各维护一份。
const RUN_TYPE_LABEL: Record<string, string> = {
  intraday:  '盘中分析',
  evening:   '盘后总结',
  morning:   '早盘前分析',
  auction:   '竞价分析',
  closing:   '收盘后分析',
  policy:    '政策解读',
  research:  '研报解读',
  notice:    '公告解读',
  watchlist: '股池分析',
  info_brief:'信息情报简报',
};

export function formatRunTypeLabel(runType: string | null | undefined): string {
  if (!runType) return '其他分析';
  return RUN_TYPE_LABEL[runType] ?? runType;
}

// /api/agent/trigger 在已有管道运行时返回的 data 形状
export interface AlreadyRunningData {
  status: 'already_running';
  run_type?: string | null;
  phase?: 'analysts' | 'chief' | null;
}

// 把"已在运行 X"的响应翻译成给用户看的中文提示
export function formatAlreadyRunningMessage(data: AlreadyRunningData | null | undefined): string {
  if (!data) return '已有 Agent 分析在运行，请等待完成后再试';
  const label = formatRunTypeLabel(data.run_type);
  return `当前正在运行「${label}」，请等待完成后再试`;
}

export async function safeFetch<T>(path: string, options?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(path, options);
    const json = await res.json();
    if (!json.success) return null;
    return json.data as T;
  } catch {
    return null;
  }
}

// ─── Phase Progress Bar ───────────────────────────────────────────────────────

const PHASES: Array<{ key: 'analysts' | 'chief'; label: string; icon: ElementType }> = [
  { key: 'analysts', label: '分析师', icon: Users },
  { key: 'chief',    label: '裁决',   icon: Brain },
];

export function PhaseBar({
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

// ─── HTML Report (iframe) ────────────────────────────────────────────────────

export function HtmlReportView({ reportId }: { reportId: number }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(600);

  const onMessage = useCallback((e: MessageEvent) => {
    if (e.data?.type === 'mra-report-height' && typeof e.data.height === 'number') {
      setHeight(Math.max(400, e.data.height + 32));
    }
  }, []);

  useEffect(() => {
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [onMessage]);

  return (
    <iframe
      ref={iframeRef}
      src={`/api/agent/report/${reportId}`}
      style={{ width: '100%', height, border: 'none', borderRadius: '16px', display: 'block' }}
      onLoad={() => {
        // 注入高度上报脚本
        try {
          iframeRef.current?.contentWindow?.postMessage({ type: 'mra-request-height' }, '*');
        } catch { /* cross-origin guard */ }
      }}
    />
  );
}
