import { useState, useEffect, useRef } from 'react';
import { Activity, BarChart3, BookOpen, Database, FileText, Star, TrendingUp, Settings, Sparkles, RefreshCw, CheckCircle, XCircle, Waves } from 'lucide-react';
import { QmtBreakerStatus } from './QmtBreakerStatus';

interface FetchResult { name: string; ok: boolean; error?: string; }
interface FetchState { status: 'idle' | 'running' | 'done' | 'auto'; results: FetchResult[]; ts: string; auto_ts: string; auto_task: string; }

export function Sidebar({ activeTab, setActiveTab }: { activeTab: string; setActiveTab: (tab: string) => void }) {
  const [currentTime, setCurrentTime] = useState(() =>
    new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  );
  const [fetchState, setFetchState] = useState<FetchState>({ status: 'idle', results: [], ts: '', auto_ts: '', auto_task: '' });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const bgPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 实时时钟
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };

  // 组件挂载时恢复抓取进度（页面刷新或切 tab 后回来）
  useEffect(() => {
    fetch('/api/fetch-all-status')
      .then(r => r.json())
      .then(j => {
        if (!j.success) return;
        const s: FetchState = j.data;
        setFetchState(s);
        if (s.status === 'running') startPoll();
      })
      .catch(() => {});

    // 常驻后台轮询，每 3s 同步一次自动抓取状态
    bgPollRef.current = setInterval(async () => {
      try {
        const r = await fetch('/api/fetch-all-status');
        const j = await r.json();
        if (j.success) {
          setFetchState(prev => {
            // 手动抓取进行中时由 pollRef 管，不用 bgPoll 覆盖
            if (prev.status === 'running' && j.data.status !== 'running') return prev;
            return j.data;
          });
        }
      } catch { /* ignore */ }
    }, 3000);

    return () => {
      stopPoll();
      if (bgPollRef.current) { clearInterval(bgPollRef.current); bgPollRef.current = null; }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startPoll = () => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch('/api/fetch-all-status');
        const j = await r.json();
        if (j.success) {
          setFetchState(j.data);
          if (j.data.status === 'done') stopPoll();
        }
      } catch { /* ignore */ }
    }, 800);
  };

  const handleFetch = async () => {
    try {
      const r = await fetch('/api/fetch-all', { method: 'POST' });
      const j = await r.json();
      if (j.success && j.data.started) {
        setFetchState(prev => ({ ...prev, status: 'running', results: [], ts: '' }));
        startPoll();
      }
    } catch { /* ignore */ }
  };

  const menuItems = [
    { id: 'market',      icon: TrendingUp, label: '市场数据' },
    { id: 'qmt',         icon: Activity,   label: 'QMT数据' },
    { id: 'exodia',      icon: Database,   label: '数据更新' },
    { id: 'cycle',       icon: Waves,      label: '市场周期' },
    { id: 'review',      icon: BookOpen,   label: '复盘数据' },
    { id: 'watchlist',   icon: Star,       label: '关注股池' },
    { id: 'news',        icon: FileText,   label: '财经快讯' },
    { id: 'policy',      icon: BarChart3,  label: '政策动态' },
    { id: 'research',    icon: FileText,   label: '研究报告' },
    { id: 'ai-analysis', icon: Sparkles, label: 'AI智能分析' },
    { id: 'settings',    icon: Settings, label: '设置' },
  ];

  const isRunning = fetchState.status === 'running';
  const isDone    = fetchState.status === 'done';
  const isAuto    = fetchState.status === 'auto';
  const failed    = fetchState.results.filter(r => !r.ok).length;
  const done      = fetchState.results.filter(r => r.ok).length;

  // 最新一条已完成/进行中的任务名（手动抓取）
  const currentTask = isRunning && fetchState.results.length > 0
    ? fetchState.results[fetchState.results.length - 1].name
    : null;

  const totalTasks = fetchState.results.length + (isRunning ? 1 : 0);

  // 同步状态指示
  const syncLabel = isRunning
    ? `抓取中 ${done}/${totalTasks || '…'}…`
    : isDone && failed > 0
    ? `${done} 成功，${failed} 失败`
    : isDone
    ? fetchState.ts ? `已更新 ${fetchState.ts}` : '已完成'
    : isAuto
    ? fetchState.auto_task ? `自动：${fetchState.auto_task}` : '自动更新中…'
    : fetchState.auto_ts
    ? `自动更新 ${fetchState.auto_ts}`
    : '就绪';
  const syncColor = isDone && failed === 0 ? 'text-green-600'
    : isDone && failed > 0 ? 'text-amber-600'
    : isRunning ? 'text-indigo-600'
    : isAuto ? 'text-indigo-500'
    : 'text-green-600';
  const dotColor = isDone && failed === 0 ? 'bg-green-500'
    : isDone && failed > 0 ? 'bg-amber-500'
    : isRunning ? 'bg-indigo-500'
    : isAuto ? 'bg-indigo-400'
    : 'bg-green-500';

  return (
    <div className="w-72 bg-white h-screen flex flex-col relative overflow-hidden border-r border-gray-200/80 shadow-xl">
      {/* Subtle accent tint top-right */}
      <div className="absolute top-0 right-0 w-48 h-48 rounded-full pointer-events-none" style={{ background: 'var(--accent-subtle)', filter: 'blur(48px)', opacity: 0.6 }} />

      {/* Logo */}
      <div className="p-6 border-b border-gray-200/80 relative z-10">
        <div className="flex items-center gap-3">
          <div className="accent-logo w-10 h-10 rounded-xl flex items-center justify-center shadow-lg" style={{ boxShadow: '0 4px 12px var(--accent-glow)' }}>
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">Market Scout</h1>
            <p className="text-xs text-gray-500">A 股行情监控</p>
          </div>
        </div>
      </div>

      {/* QMT 桥熔断状态（VM 不通时显示，全局可见） */}
      <QmtBreakerStatus />

      {/* Nav */}
      <nav aria-label="主导航" className="flex-1 p-4 relative z-10 overflow-y-auto">
        {/* 滑动活跃背景指示器 — CSS transition 驱动，无 React 重渲染延迟 */}
        <div
          className="absolute left-4 right-4 rounded-xl accent-logo nav-item-active pointer-events-none"
          style={{
            height: '44px',
            top: '16px',
            transform: `translateY(${menuItems.findIndex(m => m.id === activeTab) * 50}px)`,
            transition: 'transform 220ms var(--ease-move, cubic-bezier(0.25, 1, 0.5, 1))',
          }}
        />
        {menuItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              aria-current={isActive ? 'page' : undefined}
              className="nav-item relative z-10 w-full flex items-center gap-3 px-4 py-3 rounded-xl mb-1.5 text-sm font-medium"
              style={{
                color: isActive ? 'white' : undefined,
                transition: 'color 180ms ease',
              }}
            >
              <Icon
                className="w-4 h-4 flex-shrink-0"
                style={{
                  color: isActive ? 'white' : undefined,
                  transition: 'color 180ms ease',
                }}
              />
              <span>{item.label}</span>
              <span
                className="ml-auto w-1.5 h-1.5 rounded-full bg-white/70"
                style={{
                  opacity: isActive ? 1 : 0,
                  transition: 'opacity 180ms ease',
                }}
              />
            </button>
          );
        })}
      </nav>

      {/* 底部：同步状态 + 手动抓取按钮 */}
      <div className="p-4 border-t border-gray-100 relative z-10 space-y-2.5">

        {/* 手动抓取按钮 */}
        <button
          onClick={handleFetch}
          disabled={isRunning}
          aria-busy={isRunning}
          className="accent-solid w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium
                     disabled:opacity-60 disabled:cursor-not-allowed"
          style={{ boxShadow: '0 2px 8px var(--accent-glow)' }}
        >
          <RefreshCw className={`w-4 h-4 ${isRunning ? 'animate-spin' : ''}`} />
          {isRunning ? '抓取中...' : '抓取最新数据'}
        </button>

        {/* 进度/结果 */}
        {(isRunning || isDone) && (
          <div className="bg-gray-50 rounded-lg px-3 py-2 text-xs space-y-1">
            {isRunning && currentTask && (
              <p className="text-blue-600 truncate">▶ {currentTask}</p>
            )}
            {isDone && (
              <div className="flex items-center gap-1.5">
                {failed === 0
                  ? <><CheckCircle className="w-3.5 h-3.5 text-green-500 flex-shrink-0" /><span className="text-green-600">全部完成（{done} 项）</span></>
                  : <><XCircle className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" /><span className="text-amber-600">{done} 成功 / {failed} 失败</span></>
                }
              </div>
            )}
          </div>
        )}

        {/* 时间状态卡片 */}
        <div className="rounded-xl p-3" style={{ background: 'var(--accent-subtle)' }}>
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500">当前时间</span>
            <span className="text-xs font-semibold text-gray-900 font-mono">{currentTime}</span>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <div className={`w-1.5 h-1.5 rounded-full ${dotColor} ${(isRunning || isAuto) ? 'animate-pulse' : ''}`} />
            <span aria-live="polite" aria-atomic="true" className={`text-xs font-medium ${syncColor}`}>{syncLabel}</span>
          </div>
        </div>

      </div>
    </div>
  );
}
