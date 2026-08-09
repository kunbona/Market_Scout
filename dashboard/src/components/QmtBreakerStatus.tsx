import { useState, useEffect } from 'react';
import { AlertTriangle, Loader2, Wifi } from 'lucide-react';

interface QmtBreakerSnapshot {
  state: 'closed' | 'open' | 'half_open';
  fail_count: number;
  fail_threshold: number;
  retry_in_secs: number;
  offline_secs: number;
  last_error: string;
  last_success_at: number;
  open_until_ts: number;
}

/**
 * QMT Bridge 熔断器状态徽章
 *
 * 挂在 Sidebar 顶部 Logo 下方，dashboard 全局可见。
 *
 * 状态：
 * - closed (绿点): QMT 正常
 * - half_open (黄色脉冲): 试探中
 * - open (红色 + 倒计时): 熔断中，30s 后自动重试
 *
 * 轮询 /api/qmt-breaker 5s 一次（轻量 endpoint，不查 QMT）。
 */
export function QmtBreakerStatus() {
  const [snap, setSnap] = useState<QmtBreakerSnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchSnap = async () => {
      try {
        const r = await fetch('/api/qmt-breaker');
        const j = await r.json();
        if (!cancelled && j.success) setSnap(j.data);
      } catch {
        /* 忽略 */
      }
    };
    fetchSnap();
    // 5s 轮询，retry_in_secs 字段本身就包含倒计时，无需本地驱动
    const t = setInterval(fetchSnap, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (!snap) return null;
  if (snap.state === 'closed' && snap.fail_count === 0) return null;  // 完全正常不显示

  const state = snap.state;

  if (state === 'open') {
    return (
      <div
        title={`QMT 桥熔断中：${snap.fail_count} 次连续失败。\n${snap.last_error ? '最近错误: ' + snap.last_error : ''}`}
        className="mx-4 mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 relative z-10"
      >
        <div className="flex items-center gap-1.5 font-medium">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>QMT 桥离线</span>
        </div>
        <div className="mt-1 text-red-600/80 leading-relaxed">
          连续失败 {snap.fail_count} 次 · 约 {snap.offline_secs}s 前断开
          <br />
          <span className="text-red-500">熔断中，{snap.retry_in_secs}s 后自动重试</span>
        </div>
      </div>
    );
  }

  if (state === 'half_open') {
    return (
      <div
        title="熔断器正在试探一次重连"
        className="mx-4 mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 relative z-10"
      >
        <div className="flex items-center gap-1.5 font-medium">
          <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" />
          <span>QMT 桥重连中</span>
        </div>
        <div className="mt-1 text-amber-600/80">
          上次失败 {snap.fail_count} 次，正在试探一次
        </div>
      </div>
    );
  }

  // closed 但 fail_count > 0（刚失败过几次，还未到阈值）
  return (
    <div
      title="QMT 桥最近失败过几次，尚未触发熔断"
      className="mx-4 mb-3 rounded-xl border border-amber-100 bg-amber-50/50 px-3 py-1.5 text-xs text-amber-600 relative z-10"
    >
      <div className="flex items-center gap-1.5">
        <Wifi className="w-3 h-3" />
        <span>QMT 不稳 · 已失败 {snap.fail_count}/{snap.fail_threshold}</span>
      </div>
    </div>
  );
}
