import { RefreshCw, Zap, Calendar, CheckCircle, XCircle } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';

interface ControlBarProps {
  onRefresh?: () => void;
  lastUpdate?: string;
}

interface TaskResult {
  name: string;
  ok: boolean;
  error?: string;
}

interface ComputeStatus {
  status: 'idle' | 'running' | 'done';
  progress: TaskResult[];
  trade_date: string;
  results: TaskResult[];
}

const TASK_NAMES = [
  '市场情绪指标', '连板梯队统计', '板块涨停密度', '资金流加速度',
  '成交额异动', '筹码状态', '连板链条', '机构调研热度',
  '概念涨停密度', '集合竞价委比', '换手率分层', '市值分布', '市场宽度',
];

export function ControlBar({ onRefresh, lastUpdate }: ControlBarProps) {
  const [computing, setComputing] = useState(false);
  const [progress, setProgress] = useState<TaskResult[]>([]);
  const [results, setResults] = useState<TaskResult[]>([]);
  const [done, setDone] = useState(false);
  const [tradeDate, setTradeDate] = useState('');
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // 组件挂载时检查后端是否有计算任务正在运行（切 tab 再回来能恢复进度）
  useEffect(() => {
    fetch('/api/compute-status')
      .then(r => r.json())
      .then(j => {
        if (!j.success) return;
        const state: ComputeStatus = j.data;
        if (state.status === 'running') {
          setComputing(true);
          setProgress(state.progress ?? []);
          startPolling();
        } else if (state.status === 'done' && (state.results ?? []).length > 0) {
          // 上次计算已完成，恢复显示结果
          setResults(state.results ?? []);
          setTradeDate(state.trade_date ?? '');
          setDone(true);
        }
      })
      .catch(() => {});
    return () => stopPolling();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startPolling = () => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch('/api/compute-status');
        const json = await res.json();
        if (!json.success) return;
        const state: ComputeStatus = json.data;
        setProgress(state.progress ?? []);
        setTradeDate(state.trade_date ?? '');
        if (state.status === 'done') {
          stopPolling();
          setResults(state.results ?? []);
          setComputing(false);
          setDone(true);
          onRefresh?.();
        }
      } catch {
        // 网络抖动忽略，继续轮询
      }
    }, 1500);
  };

  const handleCompute = async () => {
    setComputing(true);
    setDone(false);
    setError('');
    setResults([]);
    setProgress([]);
    try {
      const res = await fetch('/api/compute', { method: 'POST' });
      const json = await res.json();
      if (!json.success) throw new Error(json.error ?? '启动失败');
      if (!json.data.started) {
        // 已在运行，直接轮询
      }
      startPolling();
    } catch (e: any) {
      setError(e?.message ?? '请求失败');
      setComputing(false);
    }
  };

  const pct = progress.length > 0
    ? Math.round((progress.length / TASK_NAMES.length) * 100)
    : (computing ? 2 : 0);
  const currentTaskName = computing && progress.length < TASK_NAMES.length
    ? TASK_NAMES[progress.length]
    : '';
  const failedCount = results.filter((r) => !r.ok).length;

  return (
    <div className="mb-6 space-y-3">
      {/* 按钮行 */}
      <div className="flex items-center gap-3">
        {onRefresh && (
          <button
            onClick={onRefresh}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 hover:border-gray-300 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            刷新数据
          </button>
        )}

        <button
          onClick={handleCompute}
          disabled={computing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-white bg-gradient-to-r from-orange-500 to-red-500 rounded-lg hover:from-orange-600 hover:to-red-600 transition-all disabled:opacity-60 disabled:cursor-not-allowed shadow-sm"
        >
          {computing
            ? <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            : <Zap className="w-3.5 h-3.5" />}
          {computing ? '计算中...' : '⚡ 计算今日数据'}
        </button>

        {lastUpdate && (
          <div className="ml-auto flex items-center gap-1 text-xs text-gray-400">
            <Calendar className="w-3 h-3" />
            {lastUpdate}
          </div>
        )}
      </div>

      {/* 进度条（计算中） */}
      {computing && (
        <div className="bg-white border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between text-xs text-gray-600">
            <span className="flex items-center gap-1.5">
              <RefreshCw className="w-3 h-3 animate-spin text-orange-500" />
              {currentTaskName || '准备中...'}
            </span>
            <span className="font-mono text-gray-400">{pct}%</span>
          </div>
          <div className="w-full bg-gray-100 rounded-full h-1.5">
            <div
              className="h-1.5 rounded-full bg-gradient-to-r from-orange-400 to-red-500 transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex gap-1 flex-wrap">
            {TASK_NAMES.map((name, i) => {
              const isDone = i < progress.length;
              const isCurrent = i === progress.length;
              const failed = isDone && progress[i] && !progress[i].ok;
              return (
                <span
                  key={name}
                  className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                    failed
                      ? 'bg-red-50 text-red-600'
                      : isDone
                      ? 'bg-green-50 text-green-600'
                      : isCurrent
                      ? 'bg-orange-50 text-orange-600 font-medium'
                      : 'bg-gray-50 text-gray-400'
                  }`}
                >
                  {failed ? '✗ ' : isDone ? '✓ ' : isCurrent ? '▶ ' : ''}{name}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* 完成结果 */}
      {done && results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-3">
          <div className={`flex items-center gap-1.5 text-sm mb-2 ${failedCount === 0 ? 'text-green-600' : 'text-orange-600'}`}>
            {failedCount === 0
              ? <><CheckCircle className="w-4 h-4" />计算完成（{tradeDate}，共 {results.length} 项）</>
              : <><XCircle className="w-4 h-4" />{results.length - failedCount} 项成功，{failedCount} 项失败</>}
          </div>
          {failedCount > 0 && (
            <div className="flex gap-1 flex-wrap">
              {results.filter((r) => !r.ok).map((r) => (
                <span key={r.name} className="text-xs px-1.5 py-0.5 bg-red-50 text-red-600 rounded">
                  ✗ {r.name}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="flex items-center gap-1.5 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          <XCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}
