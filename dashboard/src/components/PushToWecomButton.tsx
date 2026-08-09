import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Send } from 'lucide-react';

/**
 * 通用"推企微"按钮组件 (React Portal + 普通 state)
 *
 * 设计演进:
 * - v1: 父组件 <button> + 子 <button>: 浏览器 nested-button 警告
 * - v2: 父改 <div role="button"> + 子 <button>: 父级 re-render 时弹窗 state 丢失
 * - v3 (DOM-direct useRef+style.display): 位置容易撞上 overflow/transform 上下文,
 *       父级 absolute 没 z-index 容器就显示不出来
 * - v4 (本版): React Portal 挂到 document.body + 普通 useState
 *       → 完全脱离父组件的 stacking context, position: fixed 相对视口,
 *       普通 React state 控制显示, 100% 可靠
 *
 * 支持三种 mode: agent-report / ai-report / cycle
 */
type Mode = 'agent-report' | 'ai-report' | 'cycle';

export interface PushToWecomButtonProps {
  mode?: Mode;
  size?: 'sm' | 'md';
  label?: string;
  className?: string;
  // mode=agent-report
  rowId?: number;
  // mode=ai-report
  title?: string;
  summary?: string;
  fullText?: string;
}

export function PushToWecomButton(props: PushToWecomButtonProps) {
  const { mode = 'ai-report', size = 'sm', label, className = '' } = props;
  const [open, setOpen] = useState(false);
  const [addresses, setAddresses] = useState<string[]>(['info']);
  const [address, setAddress] = useState<string>('info');
  const [includeFull, setIncludeFull] = useState<boolean>(true);  // 默认推完整内容
  const [status, setStatus] = useState<{ state: 'idle' | 'sending' | 'sent' | 'error'; msg?: string }>({ state: 'idle' });

  // 加载地址列表 (打开 modal 时再拉, 减少初次加载噪音)
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch('/api/wecom/addresses');
        const j = await r.json();
        if (!cancelled && j?.data?.addresses?.length) {
          setAddresses(j.data.addresses);
          if (!j.data.addresses.includes(address)) {
            setAddress(j.data.addresses[0]);
          }
        }
      } catch {
        // 静默
      }
    })();
    return () => { cancelled = true; };
  }, [open, address]);

  // 打开时重置状态
  useEffect(() => {
    if (open) setStatus({ state: 'idle' });
  }, [open]);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const doPush = async () => {
    setStatus({ state: 'sending' });

    try {
      let ok = false;
      let extra: { dry_run?: boolean; configured?: boolean } = {};

      if (mode === 'agent-report') {
        const j = await fetch(`/api/agent/report/${props.rowId}/push`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ address, include_full: includeFull }),
        }).then(r => r.json());
        if (j?.success) {
          ok = j.data?.ok;
          if (j.data?.dry_run) extra.dry_run = j.data.dry_run;
        }
      } else if (mode === 'ai-report') {
        const j = await fetch('/api/wecom/send-ai-report', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: props.title, summary: props.summary, full_text: props.fullText, address,
          }),
        }).then(r => r.json());
        ok = j?.data?.ok;
      } else if (mode === 'cycle') {
        const j = await fetch('/api/cycle/notify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ address }),
        }).then(r => r.json());
        ok = j?.data?.text_ok;
        if (j?.data?.configured !== undefined) extra.configured = j.data.configured;
      }

      if (ok) {
        const suffix = extra.dry_run ? ' (沙盒, 未真发)' : '';
        setStatus({ state: 'sent', msg: `已推 → ${address}${suffix}` });
        setTimeout(() => setOpen(false), 1500);
      } else {
        setStatus({ state: 'error', msg: '推送失败, 看 server log' });
      }
    } catch (e: any) {
      setStatus({ state: 'error', msg: e?.message || String(e) });
    }
  };

  const btnClass = size === 'sm'
    ? 'px-2.5 py-1.5 text-xs rounded-lg inline-flex items-center gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60 transition-colors'
    : 'px-3 py-2 text-sm rounded-xl inline-flex items-center gap-2 bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60 transition-colors shadow-sm';

  return (
    <>
      <button
        type="button"
        onClick={e => {
          e.stopPropagation();
          e.preventDefault();
          setOpen(true);
        }}
        className={`${btnClass} ${className}`}
        title="推送到企业微信"
      >
        <Send className={size === 'sm' ? 'w-3 h-3' : 'w-4 h-4'} />
        {label || '推企微'}
      </button>

      {open && createPortal(
        <div
          // Portal 挂到 body, fixed 相对视口, 完全脱离父 stacking context
          style={{ position: 'fixed', inset: 0, zIndex: 9999 }}
          onClick={() => setOpen(false)}
          data-testid="push-modal"
        >
          <div
            className="absolute inset-0 bg-black/40"
          />
          <div
            className="relative bg-white rounded-2xl p-6 w-[380px] max-w-[calc(100vw-2rem)] shadow-2xl border border-gray-200 mx-auto mt-[10vh]"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-4">
              <Send className="w-5 h-5 text-emerald-600" />
              <h3 className="text-lg font-semibold text-gray-900">推送到企业微信</h3>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="ml-auto text-gray-400 hover:text-gray-600 text-2xl leading-none"
                aria-label="关闭"
              >
                ×
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500">推送地址</label>
                {addresses.length > 1 ? (
                  <>
                    <select
                      value={address}
                      onChange={e => setAddress(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm bg-white"
                    >
                      {addresses.map(addr => (
                        <option key={addr} value={addr}>{addr}</option>
                      ))}
                    </select>
                    <p className="text-xs text-gray-400 mt-1">
                      从 <code>config.WECOM_ROBOT_KEYS</code> + env <code>WECOM_ROBOT_KEY_&lt;name&gt;</code> 扫到
                    </p>
                  </>
                ) : (
                  <div className="mt-1 px-2.5 py-1.5 rounded-lg bg-gray-50 text-sm text-gray-700 inline-block">
                    推 → <code className="text-emerald-700 font-mono">{address}</code>
                    <span className="ml-2 text-xs text-gray-400">(唯一已配地址)</span>
                  </div>
                )}
              </div>

              {mode === 'ai-report' && props.summary && (
                <div>
                  <label className="text-xs text-gray-500">预览</label>
                  <div className="mt-1 p-3 bg-gray-50 rounded-lg text-xs whitespace-pre-wrap max-h-40 overflow-y-auto">
                    <strong># {props.title}</strong>{'\n\n'}
                    {props.summary.slice(0, 300)}
                    {props.summary.length > 300 ? '...(已截断)' : ''}
                  </div>
                </div>
              )}
              {mode === 'agent-report' && (
                <label className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer select-none pt-1">
                  <input
                    type="checkbox"
                    checked={includeFull}
                    onChange={e => setIncludeFull(e.target.checked)}
                    className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  包含完整内容
                  <span className="text-gray-400">(DB report_html 剥 HTML, ≈3KB, 受企微 4096 字节限制)</span>
                </label>
              )}
              {mode === 'cycle' && (
                <div className="p-3 bg-gray-50 rounded-lg text-xs text-gray-600">
                  推 <code>data/results/cycle_signal_latest.json</code> + <code>data/pic/cycle_position.png</code>
                </div>
              )}

              {status.msg && (
                <div className={
                  'text-xs ' +
                  (status.state === 'sent' ? 'text-emerald-600'
                    : status.state === 'sending' ? 'text-gray-500'
                      : 'text-red-600')
                }>
                  {status.state === 'sent' ? '✓' : status.state === 'sending' ? '…' : '✗'} {status.msg}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="px-3 py-1.5 text-sm text-gray-700 rounded-lg hover:bg-gray-100"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={doPush}
                  disabled={status.state === 'sending' || status.state === 'sent'}
                  className="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 flex items-center gap-1.5 disabled:opacity-60"
                >
                  <Send className="w-3.5 h-3.5" />
                  {status.state === 'sending' ? '推送中…' : '确认推送'}
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
