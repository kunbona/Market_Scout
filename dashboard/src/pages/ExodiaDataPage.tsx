import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Play, Database, AlertTriangle, CheckCircle2, Clock, Terminal, X } from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { apiFetch } from '../lib/api';

interface ExodiaProduct {
  name: string;
  displayName: string;
  dataContentTime: string | null;
  dataTime: string | null;
  lastUpdateTime: string | null;
  nextUpdateTime: string | null;
  lastErrTime: string | null;
  lag_days: number | null;
  success: boolean;
  has_error: boolean;
}

interface ExodiaStatus {
  base_dir: string;
  status_file_mtime: string | null;
  update_success: { updateTime: string; products: string[] };
  running: boolean;
  running_cmd: string | null;
  products: ExodiaProduct[];
  summary: {
    total: number;
    success: number;
    error: number;
    stale: number;
    latest_data_date: string | null;
  };
}

interface ExodiaLog {
  cmd: string;
  log_path: string;
  exists: boolean;
  size: number;
  mtime: string | null;
  running: boolean;
  lines: string[];
}

// 数据滞后 → 配色
function lagTone(lag: number | null): string {
  if (lag == null) return 'bg-gray-50 border-gray-200 text-gray-500';
  if (lag <= 1) return 'bg-emerald-50 border-emerald-200 text-emerald-700';
  if (lag <= 3) return 'bg-amber-50 border-amber-200 text-amber-700';
  return 'bg-red-50 border-red-200 text-red-600';
}

function lagText(lag: number | null): string {
  if (lag == null) return '--';
  if (lag === 0) return '今天';
  if (lag === 1) return 'T+1';
  return `${lag} 天前`;
}

// exodia 支持的更新命令（与后端 EXODIA_CMDS 白名单对齐）
interface ExodiaCommand {
  cmd: string;
  label: string;
  desc: string;
  needsProduct?: boolean;
}

const COMMANDS: ExodiaCommand[] = [
  { cmd: 'all_data', label: '增量更新全部', desc: '所有白名单产品，自动只拉新增/变更数据（每天收盘后跑这条）' },
  { cmd: 'one_data', label: '单个产品更新', desc: '只增量更新选中的产品', needsProduct: true },
  { cmd: 'full_data', label: '全量恢复(ZIP)', desc: '从 zip/ 目录的全量 ZIP 恢复历史（日常用不到）', needsProduct: true },
  { cmd: 'init', label: '同步元数据', desc: '改白名单后拉取产品元数据' },
  { cmd: 'min_data', label: '分钟线(5m)', desc: '经 qmt_proxy 拉分钟级精确数据，交易时段自动执行' },
  { cmd: 'min_data_fuzzy', label: '分钟线(tick)', desc: '经 qmt_proxy 拉分钟级模糊数据，交易时段自动执行' },
];

export function ExodiaDataPage() {
  const [data, setData] = useState<ExodiaStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [runningCmd, setRunningCmd] = useState<string | null>(null);
  const [selectedProduct, setSelectedProduct] = useState('');
  const [error, setError] = useState('');
  const [triggerMsg, setTriggerMsg] = useState('');
  // 终端日志窗口
  const [logOpen, setLogOpen] = useState(false);
  const [logCmd, setLogCmd] = useState('all_data');
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logRunning, setLogRunning] = useState(false);
  const [logFollow, setLogFollow] = useState(true);
  const logRef = useRef<HTMLDivElement | null>(null);

  const fetchStatus = async (quiet = false): Promise<void> => {
    if (!quiet) setRefreshing(true);
    try {
      const s = await apiFetch<ExodiaStatus>('/api/exodia-status');
      setData(s);
      setError('');
    } catch (err) {
      if (!quiet) setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      if (!quiet) setRefreshing(false);
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchStatus();
    const timer = window.setInterval(() => void fetchStatus(true), 60_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 终端日志轮询：窗口打开时每 2s 拉一次日志尾部
  useEffect(() => {
    if (!logOpen) return;
    let cancelled = false;
    const fetchLog = async (): Promise<void> => {
      try {
        const r = await apiFetch<ExodiaLog>(
          `/api/exodia/log?cmd=${encodeURIComponent(logCmd)}&n=200`,
        );
        if (!cancelled) {
          setLogLines(r.lines);
          setLogRunning(r.running);
        }
      } catch (err) {
        if (!cancelled) {
          setTriggerMsg(err instanceof Error ? err.message : '日志加载失败');
        }
      }
    };
    void fetchLog();
    const timer = window.setInterval(fetchLog, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [logOpen, logCmd]);

  // 自动跟随滚动到底部
  useEffect(() => {
    if (logFollow && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logLines, logFollow]);

  // 有更新在跑时自动打开终端窗口
  useEffect(() => {
    if (data?.running && data.running_cmd && !logOpen) {
      setLogCmd(data.running_cmd);
      setLogOpen(true);
      setLogFollow(true);
    }
  }, [data?.running, data?.running_cmd]); // eslint-disable-line react-hooks/exhaustive-deps

  const runCommand = async (cmd: string): Promise<void> => {
    if (data?.running || runningCmd) return;
    const spec = COMMANDS.find((c) => c.cmd === cmd);
    if (!spec) return;
    const product = spec.needsProduct ? selectedProduct : undefined;
    if (spec.needsProduct && !product) {
      setTriggerMsg('请先在上方选择产品');
      return;
    }
    const label = product ? `${spec.label} · ${product}` : spec.label;
    if (!window.confirm(`确认执行「${label}」？后台运行，日志落盘。`)) return;
    setRunningCmd(cmd);
    setTriggerMsg('');
    try {
      const r = await apiFetch<{ started: boolean; log: string }>('/api/exodia/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd, product }),
      });
      setTriggerMsg(`已启动「${label}」，日志：${r.log}`);
      // 启动后自动打开终端窗口看进度
      setLogCmd(cmd);
      setLogOpen(true);
      setLogFollow(true);
      await fetchStatus();
    } catch (err) {
      setTriggerMsg(err instanceof Error ? err.message : '启动失败');
    } finally {
      setRunningCmd(null);
    }
  };

  if (loading) {
    return (
      <div className="p-8">
        <TabHeader title="数据中心更新状态" subtitle="Exodia v0.3.7 · 日线增量更新" />
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <RefreshCw className="w-4 h-4 animate-spin" /> 加载中…
        </div>
      </div>
    );
  }

  const s = data?.summary;

  return (
    <div className="p-8 space-y-6">
      <TabHeader
        title="数据中心更新状态"
        subtitle="Exodia v0.3.7 · 日线增量更新"
        lastUpdate={data?.status_file_mtime ?? undefined}
      />

      {/* 顶部状态卡 */}
      <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <p className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <Database className="w-4 h-4 text-indigo-500" />
              Exodia 数据中心
            </p>
            <p className="text-xs text-gray-400 mt-1 font-mono">{data?.base_dir}</p>
            {triggerMsg && (
              <p className="mt-2 text-xs text-gray-500 break-all font-mono">{triggerMsg}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchStatus()}
              disabled={refreshing}
              className="inline-flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <button
              onClick={() => {
                if (!logOpen) {
                  // 打开时优先显示正在运行的命令日志
                  setLogCmd(data?.running_cmd ?? 'all_data');
                }
                setLogOpen(!logOpen);
                setLogFollow(true);
              }}
              className={`inline-flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-lg transition-colors ${
                logOpen
                  ? 'text-white bg-gray-800 hover:bg-gray-900'
                  : 'text-gray-600 border border-gray-200 hover:bg-gray-50'
              }`}
            >
              <Terminal className="w-3.5 h-3.5" />
              日志
            </button>
          </div>
        </div>

        {data?.running && (
          <div className="mt-4 flex items-center gap-2 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
            </span>
            exodia「{data.running_cmd ?? '未知命令'}」正在后台运行，更新期间会自动避免重复触发。
          </div>
        )}

        {/* 更新命令按钮组 */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {COMMANDS.map((c) => {
            const primary = c.cmd === 'all_data';
            const disabled = !!runningCmd || !!data?.running;
            return (
              <button
                key={c.cmd}
                onClick={() => void runCommand(c.cmd)}
                disabled={disabled}
                title={c.desc}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg transition-colors disabled:opacity-50 ${
                  primary
                    ? 'text-white bg-indigo-600 hover:bg-indigo-700'
                    : 'text-gray-600 border border-gray-200 bg-white hover:bg-gray-50'
                }`}
              >
                <Play className="w-3.5 h-3.5" />
                {runningCmd === c.cmd ? '启动中…' : c.label}
              </button>
            );
          })}
        </div>

        {/* 产品选择（one_data / full_data 用） */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs text-gray-400">产品：</label>
          <select
            value={selectedProduct}
            onChange={(e) => setSelectedProduct(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 font-mono bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
          >
            <option value="">— 选择产品 —</option>
            {(data?.products ?? []).map((p) => (
              <option key={p.name} value={p.name}>
                {p.displayName} · {p.name}
              </option>
            ))}
          </select>
          <span className="text-xs text-gray-400">用于「单个产品更新」/「全量恢复」</span>
        </div>

        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </div>

      {/* 终端日志窗口 */}
      {logOpen && (
        <div className="bg-gray-950 rounded-2xl border border-gray-800 shadow-[var(--shadow-sm)] overflow-hidden">
          {/* 终端头部 */}
          <div className="flex items-center justify-between gap-3 px-4 py-2 bg-gray-900 border-b border-gray-800 flex-wrap">
            <div className="flex items-center gap-2.5 text-xs min-w-0">
              <span className="flex items-center gap-1.5 text-gray-200 font-semibold shrink-0">
                <Terminal className="w-3.5 h-3.5 text-green-400" />
                运行日志
              </span>
              {logRunning ? (
                <span className="inline-flex items-center gap-1.5 text-green-400 shrink-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                  {logCmd} 运行中
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-gray-500 shrink-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-600" />
                  {logCmd} 已结束
                </span>
              )}
              <span className="text-gray-500 font-mono truncate">manual-{logCmd}.log</span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <select
                value={logCmd}
                onChange={(e) => {
                  setLogCmd(e.target.value);
                  setLogFollow(true);
                }}
                className="text-xs bg-gray-800 border border-gray-700 rounded-md px-2 py-1 font-mono text-gray-300 focus:outline-none focus:ring-2 focus:ring-green-500/40"
              >
                {COMMANDS.map((c) => (
                  <option key={c.cmd} value={c.cmd}>
                    {c.label}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setLogFollow(!logFollow)}
                title="自动滚动到底部"
                className={`px-2 py-1 text-xs rounded-md transition-colors ${
                  logFollow
                    ? 'text-green-400 bg-green-500/10 border border-green-500/30'
                    : 'text-gray-400 bg-gray-800 border border-gray-700 hover:bg-gray-700'
                }`}
              >
                跟随
              </button>
              <button
                onClick={() => setLogOpen(false)}
                className="p-1.5 text-gray-400 hover:text-gray-200 rounded-md hover:bg-gray-800"
                title="关闭终端"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          {/* 日志内容 */}
          <div
            ref={logRef}
            className="h-64 overflow-y-auto px-4 py-3 font-mono text-xs text-green-400/90 whitespace-pre-wrap break-all leading-relaxed"
          >
            {logLines.length === 0 ? (
              <span className="text-gray-600">暂无日志 —— 还没运行过 {logCmd}。</span>
            ) : (
              logLines.map((line, i) => (
                <div key={i} className="min-h-[1.2em]">{line || ' '}</div>
              ))
            )}
          </div>
        </div>
      )}

      {/* 汇总 KPI */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
        <div className="bg-white rounded-2xl border border-gray-100 p-4">
          <p className="text-xs text-gray-400">产品总数</p>
          <p className="mt-1 text-2xl font-bold font-mono text-gray-900">{s?.total ?? '--'}</p>
        </div>
        <div className="bg-white rounded-2xl border border-gray-100 p-4">
          <p className="text-xs text-gray-400 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-emerald-500" /> 本次更新成功
          </p>
          <p className="mt-1 text-2xl font-bold font-mono text-emerald-600">{s?.success ?? '--'}</p>
        </div>
        <div className="bg-white rounded-2xl border border-gray-100 p-4">
          <p className="text-xs text-gray-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-red-500" /> 异常
          </p>
          <p className="mt-1 text-2xl font-bold font-mono text-red-600">{s?.error ?? '--'}</p>
        </div>
        <div className="bg-white rounded-2xl border border-gray-100 p-4">
          <p className="text-xs text-gray-400 flex items-center gap-1">
            <Clock className="w-3 h-3 text-amber-500" /> 滞后(&gt;3天)
          </p>
          <p className="mt-1 text-2xl font-bold font-mono text-amber-600">{s?.stale ?? '--'}</p>
        </div>
        <div className="bg-white rounded-2xl border border-gray-100 p-4">
          <p className="text-xs text-gray-400">最新数据日</p>
          <p className="mt-1 text-2xl font-bold font-mono text-gray-900">{s?.latest_data_date ?? '--'}</p>
        </div>
      </div>

      {/* 产品明细表 */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <p className="text-sm font-semibold text-gray-900">产品更新明细（{data?.products.length ?? 0}）</p>
          <p className="text-xs text-gray-400">
            最近一次成功更新批次：{data?.update_success.updateTime ?? '--'}
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
                <th className="px-6 py-3 font-medium">产品</th>
                <th className="px-4 py-3 font-medium">数据内容日</th>
                <th className="px-4 py-3 font-medium">数据时间</th>
                <th className="px-4 py-3 font-medium">最后更新</th>
                <th className="px-4 py-3 font-medium">下次更新</th>
                <th className="px-4 py-3 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {(data?.products ?? []).map((p) => (
                <tr key={p.name} className="hover:bg-gray-50/50">
                  <td className="px-6 py-3">
                    <p className="text-sm text-gray-900">{p.displayName}</p>
                    <p className="text-xs text-gray-400 font-mono">{p.name}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className="font-mono text-gray-700">{p.dataContentTime ?? '--'}</span>
                    <span className={`ml-2 inline-block text-xs px-2 py-0.5 rounded-full border ${lagTone(p.lag_days)}`}>
                      {lagText(p.lag_days)}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-gray-500">{p.dataTime ?? '--'}</td>
                  <td className="px-4 py-3 font-mono text-gray-500">{p.lastUpdateTime ?? '--'}</td>
                  <td className="px-4 py-3 font-mono text-gray-500">{p.nextUpdateTime ?? '--'}</td>
                  <td className="px-4 py-3">
                    {p.has_error ? (
                      <span className="inline-flex items-center gap-1 text-xs text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5">
                        <AlertTriangle className="w-3 h-3" /> 出错
                        <span className="text-red-400 font-mono normal-case">{p.lastErrTime ?? ''}</span>
                      </span>
                    ) : p.success ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">
                        <CheckCircle2 className="w-3 h-3" /> 正常
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-2 py-0.5">
                        未更新
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
