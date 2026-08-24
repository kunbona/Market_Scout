import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../lib/api';
import { TabHeader } from '../components/TabHeader';
import { FilterTabs } from '../components/FilterTabs';
import { renderMarkdown } from '../lib/markdown';

// ─── 类型 ───────────────────────────────────────────────────
interface ResourceMeta { key: string; label: string; detail: boolean; desc: string }
interface WisItem {
  id: number; title: string; datetime: string;
  content?: string; description?: string; summary?: string; body?: string;
  cover_url?: string; url?: string; images?: string[];
}
interface WisDetail { id: number; title: string; datetime: string; text: string; html?: string; url?: string; meta?: unknown; images?: string[] }
interface AiJob {
  state: 'idle' | 'running' | 'done' | 'error';
  type: 'analyze' | 'briefing' | null;
  result: { title?: string; datetime?: string; markdown?: string; count?: number; per_source?: Record<string, number> } | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

// ─── 详情弹层 ───────────────────────────────────────────────
function DetailModal({ meta, item, onClose, onAnalyze, aiJob }: {
  meta: ResourceMeta; item: WisItem; onClose: () => void; onAnalyze: (item: WisItem) => void;
  aiJob: AiJob;
}) {
  const [detail, setDetail] = useState<WisDetail | null>(null);
  const [loading, setLoading] = useState(meta.detail);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!meta.detail) {
      // 无详情接口的资源, 直接用列表里的正文
      setDetail({ id: item.id, title: item.title, datetime: item.datetime, text: item.content || item.description || item.summary || '', images: item.images });
      setLoading(false);
      return;
    }
    setLoading(true);
    setErr('');
    apiFetch<WisDetail>(`/api/wisburg/detail?resource=${meta.key}&id=${item.id}`)
      .then(setDetail)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [meta, item]);

  const text = detail?.text || '';
  const images = detail?.images || [];
  const running = aiJob.state === 'running';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-100 flex items-start justify-between gap-4">
          <div>
            <span className="text-xs text-gray-400">{meta.label} · {item.datetime}</span>
            <h3 className="text-lg font-semibold text-gray-900 mt-1 leading-snug">
              {detail?.title || item.title || (text ? text.slice(0, 40) + (text.length > 40 ? '…' : '') : meta.label)}
            </h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none shrink-0">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="py-12 text-center text-gray-400 text-sm">加载详情中…</div>
          ) : err ? (
            <div className="py-12 text-center text-red-500 text-sm">⚠️ {err}</div>
          ) : (
            <>
              {detail?.url && (
                <a href={detail.url} target="_blank" rel="noopener noreferrer"
                   className="inline-block mb-3 text-xs text-blue-600 hover:underline">↗ 查看原文</a>
              )}
              <div
                className="prose-sm"
                dangerouslySetInnerHTML={{ __html: detail?.html ? detail.html : renderMarkdown(text) }}
              />
              {/* mikko-logs 等配图 */}
              {images.length > 0 && (
                <div className="mt-4 grid grid-cols-2 gap-2">
                  {images.map((src, i) => (
                    <a key={i} href={src} target="_blank" rel="noopener noreferrer">
                      <img src={src} alt={`配图 ${i + 1}`} loading="lazy"
                           className="rounded-lg border border-gray-100 object-cover w-full max-h-64 hover:opacity-90 transition-opacity" />
                    </a>
                  ))}
                </div>
              )}
            </>
          )}

          {/* AI 分析结果 */}
          {aiJob.state === 'running' && (
            <div className="mt-5 p-4 bg-indigo-50/50 border border-indigo-100 rounded-xl">
              <p className="text-sm text-indigo-600 animate-pulse">🤖 claude 正在分析 (约 1-3 分钟)…</p>
            </div>
          )}
          {aiJob.state === 'done' && aiJob.type === 'analyze' && aiJob.result?.markdown && (
            <div className="mt-5 border-t border-gray-100 pt-4">
              <p className="text-xs font-semibold text-indigo-600 mb-2">🤖 AI 分析</p>
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiJob.result.markdown) }} />
            </div>
          )}
          {aiJob.state === 'error' && aiJob.error && (
            <div className="mt-5 p-4 bg-red-50 border border-red-100 rounded-xl">
              <p className="text-sm text-red-600">⚠️ AI 分析失败：{aiJob.error}</p>
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-2">
          <button onClick={() => onAnalyze(item)} disabled={running}
                  className="accent-solid px-4 py-2 text-sm rounded-lg disabled:opacity-50">
            {running ? '分析中…' : '🤖 AI 分析这篇'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── 主页面 ────────────────────────────────────────────────
export function WisburgPage() {
  const [resources, setResources] = useState<ResourceMeta[]>([]);
  const [activeResource, setActiveResource] = useState('feed');
  const [items, setItems] = useState<WisItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [detailItem, setDetailItem] = useState<WisItem | null>(null);
  const [aiJob, setAiJob] = useState<AiJob>({ state: 'idle', type: null, result: null, error: null, started_at: null, finished_at: null });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 加载数据源元数据
  useEffect(() => {
    apiFetch<{ resources: ResourceMeta[] }>('/api/wisburg/meta')
      .then(d => setResources(d.resources))
      .catch(() => setResources([]));
  }, []);

  // 挂载时读一次 AI job 状态（刷新页面后恢复进行中的任务）
  useEffect(() => {
    apiFetch<AiJob>('/api/wisburg/ai-job').then(setAiJob).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // 切换数据源 / 提交搜索时拉列表
  useEffect(() => {
    setLoading(true);
    setErr('');
    setItems([]);
    const params = new URLSearchParams({ resource: activeResource, first: '50' });
    if (submittedQuery) params.set('query', submittedQuery);
    apiFetch<{ items: WisItem[]; after: string | null }>(`/api/wisburg/list?${params}`)
      .then(d => setItems(d.items || []))
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [activeResource, submittedQuery]);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  // 轮询 AI job 直到 done/error
  const startPoll = useCallback(() => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      try {
        const j = await apiFetch<AiJob>('/api/wisburg/ai-job');
        setAiJob(j);
        if (j.state !== 'running') stopPoll();
      } catch { /* ignore */ }
    }, 3000);
  }, [stopPoll]);

  const handleAnalyze = useCallback(async (item: WisItem) => {
    setAiJob({ state: 'running', type: 'analyze', result: null, error: null, started_at: new Date().toISOString(), finished_at: null });
    try {
      await apiFetch('/api/wisburg/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resource: activeResource, id: item.id }),
      });
      startPoll();
    } catch (e: any) {
      setAiJob({ state: 'error', type: 'analyze', result: null, error: e.message, started_at: null, finished_at: null });
    }
  }, [activeResource, startPoll]);

  const handleBriefing = useCallback(async () => {
    setAiJob({ state: 'running', type: 'briefing', result: null, error: null, started_at: new Date().toISOString(), finished_at: null });
    try {
      await apiFetch('/api/wisburg/briefing', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resource: 'all' }),
      });
      startPoll();
    } catch (e: any) {
      setAiJob({ state: 'error', type: 'briefing', result: null, error: e.message, started_at: null, finished_at: null });
    }
  }, [startPoll]);

  const meta = resources.find(r => r.key === activeResource);
  const preview = (it: WisItem) => (it.content || it.description || it.summary || '').replace(/\s+/g, ' ').slice(0, 120);

  return (
    <div>
      <TabHeader
        title="🏛️ 智堡投研"
        subtitle="智堡(Wisburg)投研数据 + AI 分析·整理·总结·预测"
        count={loading ? undefined : items.length}
      />

      {/* 搜索 + AI 日报 */}
      <div className="flex items-center gap-2 mb-5">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') setSubmittedQuery(query.trim()); }}
          placeholder="搜索关键词（标题/正文，留空按时间倒序）"
          className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
        />
        <button onClick={() => setSubmittedQuery(query.trim())}
                className="px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">搜索</button>
        <button onClick={handleBriefing} disabled={aiJob.state === 'running'}
                className="accent-solid px-4 py-2 text-sm rounded-lg whitespace-nowrap disabled:opacity-50">
          {aiJob.state === 'running' ? 'AI 生成中…' : '📊 综合 10 类日报'}
        </button>
      </div>

      {/* 数据源 tab */}
      {resources.length > 0 && (
        <FilterTabs
          tabs={resources.map(r => ({ id: r.key, label: r.label }))}
          activeTab={activeResource}
          onTabChange={setActiveResource}
        />
      )}
      {meta && <p className="text-xs text-gray-400 -mt-3 mb-5">{meta.desc}</p>}

      {/* AI 日报结果 */}
      {aiJob.state === 'done' && aiJob.type === 'briefing' && aiJob.result?.markdown && (
        <div className="bg-white rounded-2xl border border-indigo-100 shadow-sm p-5 mb-5">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-semibold text-indigo-700">📊 AI 综合日报（{aiJob.result.count ?? 0} 条）</p>
            <button onClick={() => setAiJob(prev => ({ ...prev, state: 'idle' }))} className="text-xs text-gray-400 hover:text-gray-600">收起</button>
          </div>
          {aiJob.result.per_source && (
            <p className="text-xs text-gray-400 mb-3">
              数据覆盖：{Object.entries(aiJob.result.per_source)
                .filter(([, n]) => n > 0)
                .map(([k, n]) => `${resources.find(r => r.key === k)?.label ?? k} ${n} 条`)
                .join(' · ')}
            </p>
          )}
          <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiJob.result.markdown) }} />
        </div>
      )}

      {/* 列表 */}
      {err ? (
        <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-3 text-sm text-amber-700">
          {err}
          {err.includes('WISBURG_API_KEY') && (
            <p className="mt-1 text-xs text-amber-600">
              在项目根目录 .env.local 添加 <code className="font-mono bg-amber-100 px-1 rounded">WISBURG_API_KEY=你的key</code> 后重启 server。
              获取地址：https://www.wisburg.com/user/developer?tab=apikeys
            </p>
          )}
        </div>
      ) : loading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
              <div className="h-4 bg-gray-200 rounded w-3/4 mb-2" />
              <div className="h-3 bg-gray-100 rounded w-full mb-1" />
              <div className="h-3 bg-gray-100 rounded w-2/3" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="text-center text-gray-400 text-sm py-16">暂无数据</div>
      ) : (
        <div className="space-y-2.5">
          {items.map(it => {
            const body = it.content || it.description || it.summary || '';
            const title = it.title || body.replace(/\*\*/g, '').replace(/\s+/g, ' ').slice(0, 50);
            const imgs = it.images || [];
            return (
            <div key={it.id} className="bg-white rounded-xl border border-gray-100 hover:border-indigo-200 hover:shadow-sm transition-all p-4 cursor-pointer group"
                 onClick={() => setDetailItem(it)}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 group-hover:text-indigo-700 leading-snug">{title}{it.title ? '' : (body.length > 50 ? '…' : '')}</p>
                  <p className="text-xs text-gray-400 mt-1">{it.datetime}</p>
                  {it.title && preview(it) && <p className="text-xs text-gray-500 mt-1.5 leading-relaxed line-clamp-2">{preview(it)}</p>}
                  {imgs.length > 0 && (
                    <div className="flex gap-1.5 mt-2">
                      {imgs.slice(0, 4).map((src, i) => (
                        <img key={i} src={src} alt="" loading="lazy"
                             className="w-14 h-14 rounded-md object-cover border border-gray-100" />
                      ))}
                      {imgs.length > 4 && <span className="text-[10px] text-gray-400 self-end">+{imgs.length - 4}</span>}
                    </div>
                  )}
                </div>
                <span className="shrink-0 text-xs text-gray-300 group-hover:text-indigo-400 transition-colors">详情 ›</span>
              </div>
            </div>
            );
          })}
        </div>
      )}

      {/* 详情弹层 */}
      {detailItem && meta && (
        <DetailModal
          meta={meta}
          item={detailItem}
          onClose={() => setDetailItem(null)}
          onAnalyze={handleAnalyze}
          aiJob={aiJob}
        />
      )}
    </div>
  );
}
