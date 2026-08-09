import { useState, useEffect, useMemo, useRef } from 'react';
import Plotly from 'plotly.js-basic-dist-min';
import { AlertTriangle, Layers, Eye, EyeOff, RefreshCw, CheckCircle2, XCircle, Loader2, Clock, Send } from 'lucide-react';

// ─── Types ───────────────────────────────────────────────────────────────────

interface CycleSummary {
  available: boolean;
  reason?: string;
  data_root?: string;
  date?: string;
  cycle_score?: number;
  phase?: string;
  emoji?: string;
  advice?: string;
  signal_state?: string;
  current_signal?: string;
  current_light_signal?: string;
  last_signal?: { date: string; type: string; tier: string } | null;
  last_light_signal?: { date: string; type: string; tier: string } | null;
  current_event?: string;
  current_position?: number;
  last_event?: { date: string; type: string; target_position: number } | null;
  generated_at?: string;
  freshness?: {
    level: string;
    fresh_count: number;
    total: number;
    stale: string[];
    as_of: string;
    latest_per_indicator: Record<string, string>;
  };
  xbx_freshness?: XbxFreshness;
}

interface XbxFreshness {
  available: boolean;
  path?: string;
  mtime?: string;
  mtime_ago_minutes?: number;
  freshness?: 'fresh' | 'stale' | 'old' | 'missing';
}

interface IndicatorMetaItem {
  key: string;
  name: string;
  category: string;
  latest: {
    date?: string;
    value?: number | null;
    percentile?: number | null;
    risk_percentage?: number | null;
    status?: string | null;
  };
  has_series: boolean;
  series_length: number;
}

interface IndicatorSeriesResp {
  available: boolean;
  reason?: string;
  meta?: { key: string; name: string; category: string; value_label: string };
  latest?: { date?: string; value?: number | null; percentile?: number | null; risk_percentage?: number | null; status?: string | null };
  series?: Array<{
    date: string;
    value: number | null;
    percentile: number | null;
    risk_percentage: number | null;
    status: string | null;
  }>;
}

interface EquityCurveResp {
  available: boolean;
  reason?: string;
  series?: Array<{ date: string; close: number }>;
  last_date?: string;
  last_close?: number;
}

interface UnifiedScoreResp {
  available: boolean;
  reason?: string;
  series?: Array<{ date: string; score: number }>;
  last_date?: string;
  last_score?: number;
}

interface CycleRefreshState {
  status: 'idle' | 'running' | 'done' | 'error';
  mode?: 'quick' | 'full';
  started_at: string;
  finished_at: string;
  exit_code: number | null;
  step: string;
  step_label: string;
  last_log: string;
  log_tail: string[];
  error: string;
  data_root?: string;
}

// ─── API helpers ────────────────────────────────────────────────────────────

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    const j = await r.json();
    if (!j.success) return null;
    return j.data as T;
  } catch {
    return null;
  }
}

// ─── Date formatting helpers (中文日期 + 星期) ──────────────────────────────

const WEEKDAY_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function formatCnDate(iso: string): string {
  // "2026-08-06" → "2026年8月6日"
  const [y, m, d] = iso.split('-');
  return `${y}年${parseInt(m, 10)}月${parseInt(d, 10)}日`;
}

function getWeekdayCn(iso: string): string {
  // "2026-08-06" → "周三"
  const [y, m, d] = iso.split('-').map(Number);
  return WEEKDAY_CN[new Date(y, m - 1, d).getDay()];
}

/**
 * 给 Plotly trace 用：每个点塞一个 customdata，hover 时显示
 * `2026年8月6日 (周三)`。还塞了 raw `2026-08-06` 备用。
 */
function buildDateCustomdata(dates: string[]): Array<{ cn: string; iso: string; weekday: string }> {
  return dates.map(d => ({
    cn: formatCnDate(d),
    iso: d,
    weekday: getWeekdayCn(d),
  }));
}

// ─── Signal / phase helpers ──────────────────────────────────────────────────

const SIGNAL_LABEL: Record<string, string> = {
  buy_bottom: '重仓抄底',
  light_buy_bottom: '轻仓抄底',
  sell_top: '重仓逃顶',
  light_sell_top: '轻仓逃顶',
  none: '无信号',
};

const SIGNAL_STATE_LABEL: Record<string, string> = {
  near_bottom: '近底部',
  near_top: '近顶部',
  neutral: '中性',
};

const PHASE_COLOR: Record<string, string> = {
  极空: '#dc2626',  // 红
  偏空: '#f97316',  // 橙
  中性: '#6b7280',  // 灰
  偏多: '#10b981',  // 绿
  极多: '#059669',  // 深绿
};

function phaseColor(phase?: string): string {
  if (!phase) return '#6b7280';
  return PHASE_COLOR[phase] || '#6b7280';
}

function freshnessColor(level?: string): string {
  if (level === 'high') return 'text-green-600';
  if (level === 'medium') return 'text-amber-600';
  return 'text-red-600';
}

// ─── Main component ──────────────────────────────────────────────────────────

export function CyclePage() {
  const [summary, setSummary] = useState<CycleSummary | null>(null);
  const [meta, setMeta] = useState<IndicatorMetaItem[]>([]);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [refreshState, setRefreshState] = useState<CycleRefreshState | null>(null);
  const [refreshBannerDismissed, setRefreshBannerDismissed] = useState(false);

  // 加载 summary + meta
  const loadData = async () => {
    setLoadingSummary(true);
    const [s, m] = await Promise.all([
      fetchJson<CycleSummary>('/api/cycle/summary'),
      fetchJson<{ items: IndicatorMetaItem[] }>('/api/cycle/indicators'),
    ]);
    setSummary(s);
    setMeta(m?.items || []);
    setLoadingSummary(false);
  };

  useEffect(() => { loadData(); }, []);

  // 拉取 refresh 状态（页面挂载时恢复任何进行中的任务）
  useEffect(() => {
    let cancelled = false;
    const sync = async () => {
      const j = await fetchJson<CycleRefreshState>('/api/cycle/refresh/status');
      if (!cancelled && j) setRefreshState(j);
    };
    sync();
    const t = setInterval(sync, 3000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // refresh 进行中时高频轮询；完成/失败自动 reload 数据
  useEffect(() => {
    if (refreshState?.status !== 'running') return;
    const t = setInterval(async () => {
      const j = await fetchJson<CycleRefreshState>('/api/cycle/refresh/status');
      if (j) {
        setRefreshState(j);
        if (j.status === 'done' || j.status === 'error') {
          // 数据已更新，重新拉
          loadData();
          setRefreshBannerDismissed(false);
        }
      }
    }, 2000);
    return () => clearInterval(t);
  }, [refreshState?.status]);

  const [notifyOnRefresh, setNotifyOnRefresh] = useState(false);
  const [notifyAddress, setNotifyAddress] = useState('info');
  const [wecomAddresses, setWecomAddresses] = useState<string[]>(['info']);
  const [notifyStatus, setNotifyStatus] = useState<{ state: 'idle' | 'sending' | 'sent' | 'error'; msg?: string }>({ state: 'idle' });

  // 拉企微地址列表 (顶部 useEffect 旁边)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch('/api/wecom/addresses');
        const j = await r.json();
        if (!cancelled && j.success && j.data?.addresses) {
          setWecomAddresses(j.data.addresses);
          // 如果当前选的不在列表里, 切到第一个
          if (j.data.addresses.length > 0 && !j.data.addresses.includes(notifyAddress)) {
            setNotifyAddress(j.data.addresses[0]);
          }
        }
      } catch {
        // 静默失败, 仍用默认 ['info']
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleRefresh = async (mode: 'quick' | 'full' = 'quick') => {
    setRefreshBannerDismissed(false);
    const params = new URLSearchParams({
      mode,
      notify: notifyOnRefresh ? '1' : '0',
      notify_address: notifyAddress,
    });
    const r = await fetch(`/api/cycle/refresh?${params}`, { method: 'POST' });
    const j = await r.json();
    if (j.success && j.data) {
      setRefreshState(j.data.state || null);
    }
  };

  const handlePushNotify = async () => {
    setNotifyStatus({ state: 'sending' });
    try {
      const r = await fetch('/api/cycle/notify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: notifyAddress }),
      });
      const j = await r.json();
      if (j.success && j.data) {
        if (j.data.skipped_reason) {
          setNotifyStatus({ state: 'error', msg: j.data.skipped_reason });
        } else if (j.data.text_ok) {
          setNotifyStatus({
            state: 'sent',
            msg: `${j.data.image_ok ? '文本 + 图片已推' : '仅文本已推 (图片失败)'} → ${j.data.address || notifyAddress}`,
          });
        } else {
          setNotifyStatus({ state: 'error', msg: '文本推送失败' });
        }
      } else {
        setNotifyStatus({ state: 'error', msg: j.error || '推送失败' });
      }
    } catch (e) {
      setNotifyStatus({ state: 'error', msg: String(e) });
    }
  };

  return (
    <div className="space-y-5">
      <Header
        onRefresh={handleRefresh}
        refreshState={refreshState}
        xbx={summary?.xbx_freshness}
        notifyOnRefresh={notifyOnRefresh}
        setNotifyOnRefresh={setNotifyOnRefresh}
        notifyAddress={notifyAddress}
        setNotifyAddress={setNotifyAddress}
        wecomAddresses={wecomAddresses}
        onPushNotify={handlePushNotify}
        notifyStatus={notifyStatus}
      />
      <RefreshBanner state={refreshState} dismissed={refreshBannerDismissed} onDismiss={() => setRefreshBannerDismissed(true)} />
      {loadingSummary ? (
        <SkeletonRow />
      ) : summary && summary.available ? (
        <>
          <SummaryCard summary={summary} />
          <IndicatorGrid items={meta} />
          <ChartSection summary={summary} />
        </>
      ) : (
        <DataMissing reason={summary?.reason || '数据未就绪'} />
      )}
    </div>
  );
}

// ─── Header ─────────────────────────────────────────────────────────────────

function Header({ onRefresh, refreshState, xbx, notifyOnRefresh, setNotifyOnRefresh, notifyAddress, setNotifyAddress, wecomAddresses, onPushNotify, notifyStatus }: {
  onRefresh: (mode: 'quick' | 'full') => void;
  refreshState: CycleRefreshState | null;
  xbx?: XbxFreshness;
  notifyOnRefresh: boolean;
  setNotifyOnRefresh: (v: boolean) => void;
  notifyAddress: string;
  setNotifyAddress: (v: string) => void;
  wecomAddresses: string[];
  onPushNotify: () => void;
  notifyStatus: { state: 'idle' | 'sending' | 'sent' | 'error'; msg?: string };
}) {
  const isRunning = refreshState?.status === 'running';
  const isDone = refreshState?.status === 'done';
  const isError = refreshState?.status === 'error';
  const mode = refreshState?.mode || 'quick';
  // 是否是 full 模式跑的（成功 / 错误 都算）
  const isFullMode = mode === 'full';
  const isNotifying = notifyStatus.state === 'sending';

  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex-1 min-w-0">
        <h1 className="text-2xl font-semibold text-gray-900">市场周期 · 顶底量化</h1>
        <p className="text-sm text-gray-500 mt-1">
          8 因子等权打分 · 沪深300/上证50/中证500/中证1000 PE 估值 · 风险溢价 · MA250 偏离 · 拥挤度 · 抱团率 · 换手率 · 破净率
        </p>
        {xbx && <div className="mt-2"><XbxFreshnessBadge xbx={xbx} /></div>}
      </div>
      <div className="flex flex-col items-end gap-2 shrink-0">
        <div className="flex gap-2">
          <button
            onClick={() => onRefresh('quick')}
            disabled={isRunning}
            aria-busy={isRunning}
            title="只跑 step2+3，假设 raw_data 已齐（XBX 已拉、akshare 已拉过）"
            className="px-3 py-2 rounded-xl text-sm font-medium flex items-center gap-2 accent-solid shadow-sm disabled:opacity-60 disabled:cursor-not-allowed transition-all"
          >
            {isRunning && !isFullMode ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : isDone && !isFullMode ? (
              <CheckCircle2 className="w-4 h-4" />
            ) : isError && !isFullMode ? (
              <XCircle className="w-4 h-4" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            {isRunning && !isFullMode
              ? '刷新中…'
              : isDone && !isFullMode
                ? '刷新成功'
                : isError && !isFullMode
                  ? '刷新失败 · 重试'
                  : '快速刷新'}
          </button>
          <button
            onClick={() => onRefresh('full')}
            disabled={isRunning}
            aria-busy={isRunning}
            title="跑 step1+2+3：先 akshare 拉数据（PE/国债/上证指数等），再算指标。XBX 仍需手动拉。"
            className="px-3 py-2 rounded-xl text-sm font-medium flex items-center gap-2 bg-indigo-600 text-white shadow-sm disabled:opacity-60 disabled:cursor-not-allowed transition-all hover:bg-indigo-700"
          >
            {isRunning && isFullMode ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : isDone && isFullMode ? (
              <CheckCircle2 className="w-4 h-4" />
            ) : isError && isFullMode ? (
              <XCircle className="w-4 h-4" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            {isRunning && isFullMode
              ? '完整刷新中…'
              : isDone && isFullMode
                ? '完整刷新成功'
                : isError && isFullMode
                  ? '完整刷新失败 · 重试'
                  : '完整刷新 (含拉数据)'}
          </button>
          <button
            onClick={onPushNotify}
            disabled={isNotifying}
            aria-busy={isNotifying}
            title="手动推送当前周期信号到企业微信机器人 (用最新 data/results/cycle_signal_latest.json + data/pic/cycle_position.png)"
            className="px-3 py-2 rounded-xl text-sm font-medium flex items-center gap-2 bg-emerald-600 text-white shadow-sm disabled:opacity-60 disabled:cursor-not-allowed transition-all hover:bg-emerald-700"
          >
            {isNotifying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {isNotifying ? '推送中…' : '推企微'}
          </button>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-600">
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={notifyOnRefresh}
              onChange={e => setNotifyOnRefresh(e.target.checked)}
              className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
            />
            跑完推企微 →
          </label>
          <select
            value={notifyAddress}
            onChange={e => setNotifyAddress(e.target.value)}
            className="rounded border-gray-300 text-xs px-1.5 py-0.5 bg-white"
            title="选择企微推送地址"
          >
            {wecomAddresses.map(addr => (
              <option key={addr} value={addr}>{addr}</option>
            ))}
          </select>
        </div>
        {notifyStatus.state !== 'idle' && notifyStatus.msg && (
          <div
            className={
              'text-xs ' +
              (notifyStatus.state === 'sent'
                ? 'text-emerald-600'
                : notifyStatus.state === 'sending'
                  ? 'text-gray-500'
                  : 'text-red-600')
            }
          >
            {notifyStatus.state === 'sent' ? '✓' : notifyStatus.state === 'sending' ? '…' : '✗'} {notifyStatus.msg}
          </div>
        )}
        <div className="text-xs text-gray-400 text-right">
          快速 = step2+3 · 完整 = step1+2+3
          <br />
          XBX 仍需手动拉 (XBX 工具登录券商)
        </div>
      </div>
    </div>
  );
}

// ─── XBX Freshness Badge (右上角 XBX 数据新鲜度提示) ───────────────────────

function formatAgo(minutes: number): string {
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}

function XbxFreshnessBadge({ xbx }: { xbx?: XbxFreshness }) {
  if (!xbx) return null;
  const f = xbx.freshness;
  const minutes = xbx.mtime_ago_minutes ?? -1;

  // missing: 灰底警示
  if (!xbx.available || f === 'missing') {
    return (
      <div className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-gray-100 text-gray-500">
        <AlertTriangle className="w-3.5 h-3.5" />
        <span>XBX 数据文件缺失</span>
      </div>
    );
  }

  // fresh: 绿底
  if (f === 'fresh') {
    return (
      <div className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-green-50 text-green-700 border border-green-200">
        <CheckCircle2 className="w-3.5 h-3.5" />
        <span>XBX 数据新鲜 · {formatAgo(minutes)}更新</span>
      </div>
    );
  }

  // stale: 黄底
  if (f === 'stale') {
    return (
      <div
        title={`XBX parquet mtime: ${xbx.mtime || '?'}（${formatAgo(minutes)}）。可能正在拉取新数据，或上次拉取未完成。`}
        className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-amber-50 text-amber-700 border border-amber-200"
      >
        <Clock className="w-3.5 h-3.5" />
        <span>XBX 数据偏旧 · {formatAgo(minutes)}未更新</span>
      </div>
    );
  }

  // old: 红底
  return (
    <div
      title={`XBX parquet mtime: ${xbx.mtime || '?'}（${formatAgo(minutes)}）。XBX 工具可能很久没跑了，先拉数据再点 refresh。`}
      className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-red-50 text-red-700 border border-red-200"
    >
      <AlertTriangle className="w-3.5 h-3.5" />
      <span>XBX 数据陈旧 · {formatAgo(minutes)}未更新</span>
    </div>
  );
}

// ─── Refresh Banner (进度 + 日志) ───────────────────────────────────────────

function RefreshBanner({ state, dismissed, onDismiss }: {
  state: CycleRefreshState | null;
  dismissed: boolean;
  onDismiss: () => void;
}) {
  if (!state || state.status === 'idle' || dismissed) return null;

  const isRunning = state.status === 'running';
  const isError = state.status === 'error';
  const accent = isRunning ? 'bg-indigo-50 border-indigo-200 text-indigo-900'
    : isError ? 'bg-red-50 border-red-200 text-red-900'
    : 'bg-green-50 border-green-200 text-green-900';
  const icon = isRunning ? <Loader2 className="w-4 h-4 animate-spin" />
    : isError ? <XCircle className="w-4 h-4" />
    : <CheckCircle2 className="w-4 h-4" />;
  const title = isRunning
    ? `正在刷新日线数据${state.step_label ? ' · ' + state.step_label : ''}`
    : isError
      ? `刷新失败${state.error ? ' · ' + state.error : ''}`
      : '刷新成功，数据已更新';

  return (
    <div className={`rounded-2xl border p-4 ${accent}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 flex-1 min-w-0">
          <div className="mt-0.5 shrink-0">{icon}</div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{title}</p>
            {state.started_at && (
              <p className="text-xs opacity-70 mt-0.5">
                {isRunning ? '开始于' : '开始'}: {state.started_at}
                {state.finished_at && ` · ${isError ? '失败于' : '完成于'}: ${state.finished_at}`}
                {state.exit_code != null && ` · 退出码 ${state.exit_code}`}
              </p>
            )}
            {isRunning && state.last_log && (
              <p className="text-xs opacity-60 mt-1.5 truncate font-mono">
                &gt; {state.last_log}
              </p>
            )}
          </div>
        </div>
        {!isRunning && (
          <button
            onClick={onDismiss}
            className="text-xs opacity-60 hover:opacity-100 px-2 py-1 rounded transition-opacity"
          >
            收起
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Skeleton ───────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="space-y-3">
      <div className="h-32 bg-gray-100 rounded-2xl animate-pulse" />
      <div className="h-24 bg-gray-100 rounded-2xl animate-pulse" />
      <div className="h-96 bg-gray-100 rounded-2xl animate-pulse" />
    </div>
  );
}

// ─── DataMissing ────────────────────────────────────────────────────────────

function DataMissing({ reason }: { reason: string }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-2xl p-6 flex items-start gap-3">
      <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
      <div>
        <p className="text-sm font-medium text-amber-900">数据未就绪</p>
        <p className="text-sm text-amber-700 mt-1">{reason}</p>
        <p className="text-xs text-amber-600 mt-2">
          提示：点上方「完整刷新 (含拉数据)」按钮一键生成 (走自管算法, 不再依赖对方项目)
        </p>
      </div>
    </div>
  );
}

// ─── SummaryCard ────────────────────────────────────────────────────────────

function SummaryCard({ summary }: { summary: CycleSummary }) {
  const color = phaseColor(summary.phase);
  const score = summary.cycle_score ?? 0;
  const freshness = summary.freshness;
  const isStale = freshness && freshness.fresh_count < freshness.total;
  const lastSig = summary.last_signal;
  const lastLightSig = summary.last_light_signal;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] p-6">
      <div className="grid grid-cols-12 gap-6">
        {/* 左侧：当前定位 + emoji */}
        <div className="col-span-12 lg:col-span-3 border-r border-gray-100 lg:pr-6">
          <p className="text-xs text-gray-500">当前定位</p>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-4xl font-bold" style={{ color }}>{summary.emoji || '⚪'}</span>
            <span className="text-2xl font-semibold" style={{ color }}>{summary.phase || '—'}</span>
          </div>
          <p className="text-sm text-gray-600 mt-2">{summary.advice || '—'}</p>
          <p className="text-xs text-gray-400 mt-1">基准日 {summary.date}</p>
        </div>

        {/* 中间：分数条 + freshness */}
        <div className="col-span-12 lg:col-span-5 border-r border-gray-100 lg:pr-6">
          <div className="flex items-baseline justify-between">
            <p className="text-xs text-gray-500">综合风险分（0-100）</p>
            <p className={`text-xs ${freshnessColor(freshness?.level)}`}>
              数据新鲜度：{freshness?.fresh_count}/{freshness?.total}
              {isStale && freshness?.stale && freshness.stale.length > 0 && (
                <span className="ml-1">· 滞后 {freshness.stale.length} 项</span>
              )}
            </p>
          </div>
          <div className="mt-2 relative h-3 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="absolute inset-y-0 left-0 transition-all"
              style={{
                width: `${Math.min(100, Math.max(0, score))}%`,
                background: `linear-gradient(to right, #10b981 0%, #f59e0b 50%, #dc2626 100%)`,
              }}
            />
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-gray-900"
              style={{ left: `${score}%` }}
            />
          </div>
          <p className="text-2xl font-bold mt-2" style={{ color }}>{score.toFixed(1)}</p>
          <p className="text-xs text-gray-400 mt-1">
            当前仓位建议：<span className="font-medium text-gray-700">{((summary.current_position ?? 0) * 100).toFixed(0)}%</span>
            {' · '}
            <span>状态：<span className="font-medium text-gray-700">{SIGNAL_STATE_LABEL[summary.signal_state || ''] || summary.signal_state || '—'}</span></span>
          </p>
        </div>

        {/* 右侧：最近信号 */}
        <div className="col-span-12 lg:col-span-4">
          <p className="text-xs text-gray-500">最近信号</p>
          <div className="mt-2 space-y-2">
            <SignalRow
              label="重仓信号"
              sig={lastSig ? { date: lastSig.date, type: SIGNAL_LABEL[lastSig.type] || lastSig.type } : null}
            />
            <SignalRow
              label="轻仓信号"
              sig={lastLightSig ? { date: lastLightSig.date, type: SIGNAL_LABEL[lastLightSig.type] || lastLightSig.type } : null}
            />
          </div>
          <p className="text-xs text-gray-400 mt-3">计算时间：{summary.generated_at}</p>
        </div>
      </div>
    </div>
  );
}

function SignalRow({ label, sig }: { label: string; sig: { date: string; type: string } | null }) {
  if (!sig) {
    return (
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-500">{label}</span>
        <span className="text-gray-400">—</span>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <div className="text-right">
        <span className="font-medium text-gray-900">{sig.type}</span>
        <span className="text-gray-400 ml-2">{sig.date}</span>
      </div>
    </div>
  );
}

// ─── IndicatorGrid ──────────────────────────────────────────────────────────

function IndicatorGrid({ items }: { items: IndicatorMetaItem[] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {items.map(it => <IndicatorCard key={it.key} item={it} />)}
    </div>
  );
}

function IndicatorCard({ item }: { item: IndicatorMetaItem }) {
  const v = item.latest.value;
  const r = item.latest.risk_percentage;
  const status = item.latest.status;
  const score = r ?? null;
  const scoreColor =
    score == null ? 'text-gray-400'
      : score >= 70 ? 'text-red-600'
      : score >= 50 ? 'text-amber-600'
      : 'text-green-600';
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-3 shadow-[var(--shadow-sm)]">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">{item.category}</span>
        <span className="text-[10px] text-gray-400">{item.latest.date || '—'}</span>
      </div>
      <p className="text-xs text-gray-700 font-medium mt-1.5 truncate" title={item.name}>{item.name}</p>
      <div className="flex items-baseline justify-between mt-1.5">
        <span className="text-lg font-semibold text-gray-900 tabular-nums">
          {v != null ? formatValue(v) : '—'}
        </span>
        {score != null && (
          <span className={`text-xs font-medium tabular-nums ${scoreColor}`}>
            风险 {score.toFixed(0)}
          </span>
        )}
      </div>
      {status && <p className="text-[10px] text-gray-400 mt-1 truncate">{status}</p>}
    </div>
  );
}

function formatValue(v: number): string {
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 100) return v.toFixed(1);
  if (Math.abs(v) >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

// ─── ChartSection ───────────────────────────────────────────────────────────

type Tab = 'overview' | string;  // 'overview' or indicator key

function ChartSection(_props: { summary: CycleSummary }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [hideUnified, setHideUnified] = useState(false);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
      <ChartTabs active={tab} onChange={setTab} hideUnified={hideUnified} setHideUnified={setHideUnified} />
      <div className="p-4">
        {tab === 'overview' ? <OverviewChart hideUnified={hideUnified} /> : <SingleIndicatorChart key={tab} indicatorKey={tab} />}
      </div>
    </div>
  );
}

function ChartTabs({
  active, onChange, hideUnified, setHideUnified,
}: {
  active: Tab; onChange: (t: Tab) => void;
  hideUnified: boolean; setHideUnified: (v: boolean) => void;
}) {
  const [meta, setMeta] = useState<IndicatorMetaItem[]>([]);
  useEffect(() => {
    fetchJson<{ items: IndicatorMetaItem[] }>('/api/cycle/indicators').then(r => setMeta(r?.items || []));
  }, []);

  return (
    <div className="border-b border-gray-100 px-3 pt-3 flex items-center gap-2 flex-wrap">
      <button
        onClick={() => onChange('overview')}
        className={`px-3 py-1.5 text-xs font-medium rounded-lg flex items-center gap-1.5 transition-colors ${
          active === 'overview'
            ? 'accent-solid shadow-sm'
            : 'border border-gray-200 text-gray-600 hover:bg-gray-50'
        }`}
      >
        <Layers className="w-3.5 h-3.5" /> 周期总图
      </button>

      <div className="w-px h-5 bg-gray-200 mx-1" />

      {meta.map(m => (
        <button
          key={m.key}
          onClick={() => onChange(m.key)}
          className={`px-3 py-1.5 text-xs font-medium rounded-lg flex items-center gap-1.5 transition-colors ${
            active === m.key
              ? 'accent-solid shadow-sm'
              : 'border border-gray-200 text-gray-600 hover:bg-gray-50'
          }`}
        >
          {m.category} · {m.name}
        </button>
      ))}

      {active === 'overview' && (
        <div className="ml-auto pb-1">
          <button
            onClick={() => setHideUnified(!hideUnified)}
            className="px-2.5 py-1 text-xs font-medium border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 flex items-center gap-1"
          >
            {hideUnified ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            {hideUnified ? '显示分数' : '隐藏分数'}
          </button>
        </div>
      )}
    </div>
  );
}

// ─── OverviewChart (上证指数 + unified score 双面板) ─────────────────────────

function OverviewChart({ hideUnified }: { hideUnified: boolean }) {
  const elRef = useRef<HTMLDivElement>(null);
  const [equity, setEquity] = useState<EquityCurveResp | null>(null);
  const [unified, setUnified] = useState<UnifiedScoreResp | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      const [e, u] = await Promise.all([
        fetchJson<EquityCurveResp>('/api/cycle/equity-curve?max_points=2000'),
        fetchJson<UnifiedScoreResp>('/api/cycle/unified-score?max_points=2000'),
      ]);
      if (cancelled) return;
      setEquity(e);
      setUnified(u);
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const { equityTrace, unifiedTrace, layout } = useMemo(() => buildOverviewPlotlyData(equity, unified, hideUnified), [equity, unified, hideUnified]);

  useEffect(() => {
    if (!elRef.current || !equityTrace) return;
    Plotly.react(elRef.current, [equityTrace, ...(unifiedTrace ? [unifiedTrace] : [])], layout, {
      displayModeBar: true,
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    });
  }, [equityTrace, unifiedTrace, layout]);

  useEffect(() => {
    return () => {
      if (elRef.current) Plotly.purge(elRef.current);
    };
  }, []);

  if (loading) return <div className="h-[560px] flex items-center justify-center text-gray-400 text-sm">加载中…</div>;
  if (!equity?.available) return <div className="h-[560px] flex items-center justify-center text-amber-600 text-sm">{equity?.reason || '上证指数数据不可用'}</div>;
  if (!unified?.available) return <div className="h-[560px] flex items-center justify-center text-amber-600 text-sm">{unified?.reason || '综合分数数据不可用'}</div>;

  return (
    <div>
      <ChartLegend equity={equity} unified={unified} hideUnified={hideUnified} />
      <div ref={elRef} className="w-full" style={{ height: 560 }} />
    </div>
  );
}

function ChartLegend({ equity, unified, hideUnified }: { equity: EquityCurveResp; unified: UnifiedScoreResp; hideUnified: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-2 pb-2 text-xs text-gray-600">
      <span className="flex items-center gap-1.5">
        <span className="w-3 h-0.5 bg-gray-500 inline-block" /> 上证指数 ({equity.last_close?.toFixed(2)}, {equity.last_date})
      </span>
      {!hideUnified && (
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 bg-red-500/60 inline-block rounded-sm" /> 综合风险分 ({unified.last_score?.toFixed(1)}, {unified.last_date})
        </span>
      )}
      <span className="text-gray-400 ml-auto">提示：可滚轮缩放 · 双击复位 · 拖动 rangeslider 选区段</span>
    </div>
  );
}

function buildOverviewPlotlyData(equity: EquityCurveResp | null, unified: UnifiedScoreResp | null, hideUnified: boolean) {
  if (!equity?.series) return { equityTrace: null, unifiedTrace: null, layout: {} };
  const dates = equity.series.map(d => d.date);
  const closes = equity.series.map(d => d.close);

  const dateCustom = buildDateCustomdata(dates);

  const equityTrace: any = {
    x: dates,
    y: closes,
    type: 'scatter',
    mode: 'lines',
    name: '上证指数',
    line: { color: '#6b7280', width: 1.2 },
    yaxis: 'y1',
    customdata: dateCustom,
    hovertemplate: '<b>%{customdata.cn} (%{customdata.weekday})</b><br>上证指数: %{y:.2f}<extra></extra>',
  };

  let unifiedTrace: any = null;
  if (!hideUnified && unified?.series) {
    // 统一分数按日期对齐到指数的 x 轴
    const uMap = new Map(unified.series.map(u => [u.date, u.score]));
    const uScores = dates.map(d => uMap.has(d) ? uMap.get(d) : null);
    unifiedTrace = {
      x: dates,
      y: uScores,
      type: 'scatter',
      mode: 'lines',
      name: '综合风险分',
      line: { color: '#dc2626', width: 1.5 },
      fill: 'tozeroy',
      fillcolor: 'rgba(220, 38, 38, 0.08)',
      yaxis: 'y2',
      connectgaps: true,
      customdata: dateCustom,
      hovertemplate: '<b>%{customdata.cn} (%{customdata.weekday})</b><br>风险分: %{y:.1f}<extra></extra>',
    };
  }

  // 80/50 阈值线（在 y2 上）
  const shapes: any[] = [];
  if (!hideUnified && dates.length > 0) {
    shapes.push(
      { type: 'line', xref: 'x', yref: 'y2', x0: dates[0], x1: dates[dates.length - 1], y0: 80, y1: 80, line: { color: '#dc2626', width: 1, dash: 'dash' }, opacity: 0.4 },
      { type: 'line', xref: 'x', yref: 'y2', x0: dates[0], x1: dates[dates.length - 1], y0: 50, y1: 50, line: { color: '#f59e0b', width: 1, dash: 'dash' }, opacity: 0.4 },
    );
  }

  const layout: any = {
    template: 'none',
    margin: { l: 60, r: 60, t: 10, b: 40 },
    paper_bgcolor: '#ffffff',
    plot_bgcolor: '#ffffff',
    hovermode: 'x unified',
    hoverlabel: { bgcolor: '#ffffff', bordercolor: '#e5e7eb' },
    showlegend: false,
    xaxis: {
      domain: [0, 1],
      anchor: 'y2',
      type: 'date',
      tickformat: '%Y年%m月',
      dtick: 'M6',  // 每 6 个月一个主刻度
      tickfont: { size: 10, color: '#6b7280' },
      showspikes: true,
      spikemode: 'across+marker',
      spikesnap: 'cursor',
      spikedash: 'solid',
      spikethickness: 1,
      spikecolor: '#9ca3af',
      rangeslider: { visible: true, thickness: 0.05, bgcolor: '#f9fafb' },
    },
    xaxis2: {
      domain: [0, 1],
      anchor: 'y1',
      type: 'date',
      tickformat: '%Y年%m月',
      dtick: 'M6',
      tickfont: { size: 10, color: '#9ca3af' },
      showspikes: true,
      spikemode: 'across+marker',
      spikesnap: 'cursor',
      spikedash: 'solid',
      spikethickness: 1,
      spikecolor: '#9ca3af',
    },
    yaxis: {
      domain: [0.55, 1.0],
      title: { text: '上证指数', font: { color: '#6b7280', size: 11 } },
      tickfont: { color: '#6b7280', size: 10 },
      gridcolor: '#f3f4f6',
      zerolinecolor: '#e5e7eb',
    },
    yaxis2: {
      domain: [0, 0.45],
      title: { text: '综合风险分', font: { color: '#dc2626', size: 11 } },
      tickfont: { color: '#dc2626', size: 10 },
      range: [0, 100],
      gridcolor: '#f3f4f6',
      zerolinecolor: '#e5e7eb',
    },
    shapes,
  };

  return { equityTrace, unifiedTrace, layout };
}

// ─── SingleIndicatorChart ───────────────────────────────────────────────────

function SingleIndicatorChart({ indicatorKey }: { indicatorKey: string }) {
  const elRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<IndicatorSeriesResp | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchJson<IndicatorSeriesResp>(`/api/cycle/indicator/${indicatorKey}?max_points=2000`)
      .then(d => { if (!cancelled) { setData(d); setLoading(false); } });
    return () => { cancelled = true; };
  }, [indicatorKey]);

  const { traces, layout } = useMemo(() => buildIndicatorPlotlyData(data), [data]);

  useEffect(() => {
    if (!elRef.current || !traces.length) return;
    Plotly.react(elRef.current, traces, layout, {
      displayModeBar: true,
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    });
  }, [traces, layout]);

  useEffect(() => {
    return () => { if (elRef.current) Plotly.purge(elRef.current); };
  }, []);

  if (loading) return <div className="h-[520px] flex items-center justify-center text-gray-400 text-sm">加载中…</div>;
  if (!data?.available) return <div className="h-[520px] flex items-center justify-center text-amber-600 text-sm">{data?.reason || '数据不可用'}</div>;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-2 pb-2 text-xs text-gray-600">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-indigo-600 inline-block" /> {data.meta?.value_label || 'value'}
        </span>
        {data.latest?.percentile != null && (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-amber-500 inline-block" /> 历史分位
          </span>
        )}
        {data.latest?.risk_percentage != null && (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-2 bg-red-500/60 inline-block rounded-sm" /> 风险分
          </span>
        )}
        <span className="text-gray-400 ml-auto">提示：可滚轮缩放 · 拖 rangeslider 选时间段</span>
      </div>
      <div ref={elRef} className="w-full" style={{ height: 520 }} />
    </div>
  );
}

function buildIndicatorPlotlyData(data: IndicatorSeriesResp | null) {
  if (!data?.series?.length) return { traces: [], layout: {} };
  const dates = data.series.map(d => d.date);
  const meta = data.meta!;
  const dateCustom = buildDateCustomdata(dates);

  const traces: any[] = [];

  // 主值（左侧 y 轴）
  traces.push({
    x: dates,
    y: data.series.map(d => d.value),
    type: 'scatter',
    mode: 'lines',
    name: meta.value_label,
    line: { color: '#4f46e5', width: 1.4 },
    yaxis: 'y1',
    connectgaps: true,
    customdata: dateCustom,
    hovertemplate: '<b>%{customdata.cn} (%{customdata.weekday})</b><br>' + meta.value_label + ': %{y}<extra></extra>',
  });

  // 历史分位（右侧 y1）
  const hasPercentile = data.series.some(d => d.percentile != null);
  if (hasPercentile) {
    traces.push({
      x: dates,
      y: data.series.map(d => d.percentile),
      type: 'scatter',
      mode: 'lines',
      name: '历史分位',
      line: { color: '#f59e0b', width: 1.0, dash: 'dot' },
      yaxis: 'y2',
      connectgaps: true,
      customdata: dateCustom,
      hovertemplate: '<b>%{customdata.cn} (%{customdata.weekday})</b><br>分位: %{y:.1f}<extra></extra>',
    });
  }

  // 风险分（右侧 y1，0-100）
  const hasRisk = data.series.some(d => d.risk_percentage != null);
  if (hasRisk) {
    traces.push({
      x: dates,
      y: data.series.map(d => d.risk_percentage),
      type: 'scatter',
      mode: 'lines',
      name: '风险分',
      line: { color: '#dc2626', width: 1.2 },
      yaxis: 'y2',
      fill: 'tozeroy',
      fillcolor: 'rgba(220, 38, 38, 0.05)',
      connectgaps: true,
      customdata: dateCustom,
      hovertemplate: '<b>%{customdata.cn} (%{customdata.weekday})</b><br>风险分: %{y:.1f}<extra></extra>',
    });
  }

  const layout: any = {
    template: 'none',
    margin: { l: 60, r: 60, t: 10, b: 40 },
    paper_bgcolor: '#ffffff',
    plot_bgcolor: '#ffffff',
    hovermode: 'x unified',
    hoverlabel: { bgcolor: '#ffffff', bordercolor: '#e5e7eb' },
    showlegend: false,
    xaxis: {
      type: 'date',
      tickformat: '%Y年%m月',
      dtick: 'M6',  // 每 6 个月一个主刻度
      tickfont: { size: 10, color: '#6b7280' },
      showspikes: true,
      spikemode: 'across+marker',
      spikesnap: 'cursor',
      spikedash: 'solid',
      spikethickness: 1,
      spikecolor: '#9ca3af',
      rangeslider: { visible: true, thickness: 0.05, bgcolor: '#f9fafb' },
    },
    yaxis: {
      title: { text: meta.value_label, font: { color: '#4f46e5', size: 11 } },
      tickfont: { color: '#4f46e5', size: 10 },
      gridcolor: '#f3f4f6',
      zerolinecolor: '#e5e7eb',
    },
    yaxis2: {
      overlaying: 'y',
      side: 'right',
      title: { text: '分位 / 风险分', font: { color: '#dc2626', size: 11 } },
      tickfont: { color: '#dc2626', size: 10 },
      range: hasRisk ? [0, 100] : undefined,
      gridcolor: 'transparent',
      zerolinecolor: '#e5e7eb',
    },
    shapes: hasRisk ? [
      { type: 'line', xref: 'x', yref: 'y2', x0: dates[0], x1: dates[dates.length - 1], y0: 80, y1: 80, line: { color: '#dc2626', width: 1, dash: 'dash' }, opacity: 0.3 },
      { type: 'line', xref: 'x', yref: 'y2', x0: dates[0], x1: dates[dates.length - 1], y0: 50, y1: 50, line: { color: '#f59e0b', width: 1, dash: 'dash' }, opacity: 0.3 },
    ] : [],
  };

  return { traces, layout };
}
