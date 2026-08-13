import { useEffect, useRef, useState, Component } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Loader2,
  Plus,
  Star,
  Trash2,
  Upload,
} from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { PhaseBar, HtmlReportView, safeFetch, formatAlreadyRunningMessage } from '../components/agentShared';
import type { AgentStatus, AlreadyRunningData } from '../components/agentShared';

// ─── Types ────────────────────────────────────────────────────────────────────

interface WatchlistItem {
  id: number;
  pool: string;
  code: string;
  name: string;
  note: string;
  added_at: string;
  sort: number;
}

// QMT 实时报价字段（/api/watchlist/quote 合并后）
interface QuoteFields {
  last_price: number | null;
  last_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  change: number | null;
  change_pct: number | null;
  has_quote: boolean;
}

interface PoolInfo {
  name: string;
  count: number;
  created_at?: string;
}

interface HistoryItem {
  id: number | string;
  run_time: string;
  run_type: string;
  summary?: string;
  has_html?: boolean;
}

// ─── Watchlist Panel ──────────────────────────────────────────────────────────

interface StockCandidate {
  code: string;
  name: string;
}

interface ImportResult {
  added: number;
  skipped_existing: number;
  failed: Array<{ raw: string; reason: string }>;
  total: number;
  pool?: string;
}

function WatchlistPanel({
  running,
  onTrigger,
}: {
  running: boolean;
  onTrigger: (type: 'watchlist', pool?: string) => void;
}) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [pools, setPools] = useState<PoolInfo[]>([]);
  const [currentPool, setCurrentPool] = useState<string>('全部');
  const [codeInput, setCodeInput] = useState('');
  const [noteInput, setNoteInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 实时报价 (QMT tick 合并到 watchlist)
  const [quoteMap, setQuoteMap] = useState<Record<string, QuoteFields>>({});
  const [quoteUpdatedAt, setQuoteUpdatedAt] = useState<string | null>(null);
  const [bridgeState, setBridgeState] = useState<string | null>(null);
  const quoteTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 池管理
  const [manageOpen, setManageOpen] = useState(false);
  const [newPoolName, setNewPoolName] = useState('');

  // 联想下拉
  const [suggestions, setSuggestions] = useState<StockCandidate[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [multiCandidates, setMultiCandidates] = useState<StockCandidate[]>([]);
  const suppressSuggestRef = useRef(false);

  // 导入
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 添加 / 导入的目标池：'全部'视图下落'默认'
  const targetPool = currentPool === '全部' ? '默认' : currentPool;

  const loadPools = async () => {
    const data = await safeFetch<PoolInfo[]>('/api/pools');
    if (data) setPools(data);
  };

  const load = async (pool?: string) => {
    const p = pool ?? currentPool;
    const url = p === '全部' ? '/api/watchlist' : `/api/watchlist?pool=${encodeURIComponent(p)}`;
    const data = await safeFetch<WatchlistItem[]>(url);
    if (data) setItems(data);
    await loadPools();
  };

  useEffect(() => { load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPool]);

  // 实时报价：每 5s 拉一次，QMT 拿全 watchlist tick
  useEffect(() => {
    let cancelled = false;
    const fetchQuote = async () => {
      const url = currentPool === '全部'
        ? '/api/watchlist/quote'
        : `/api/watchlist/quote?pool=${encodeURIComponent(currentPool)}`;
      const data = await safeFetch<{
        items: Array<WatchlistItem & QuoteFields>;
        quote_time: string | null;
        quote_count: number;
        total_count: number;
        bridge_state: string | null;
      }>(url);
      if (cancelled || !data) return;
      const next: Record<string, QuoteFields> = {};
      for (const it of data.items) {
        next[it.code] = {
          last_price: it.last_price,
          last_close: it.last_close,
          open: it.open,
          high: it.high,
          low: it.low,
          volume: it.volume,
          amount: it.amount,
          change: it.change,
          change_pct: it.change_pct,
          has_quote: it.has_quote,
        };
      }
      setQuoteMap(next);
      setQuoteUpdatedAt(new Date().toISOString());
      setBridgeState(data.bridge_state);
    };
    fetchQuote();
    if (quoteTimerRef.current) clearInterval(quoteTimerRef.current);
    quoteTimerRef.current = setInterval(fetchQuote, 5000);
    return () => {
      cancelled = true;
      if (quoteTimerRef.current) {
        clearInterval(quoteTimerRef.current);
        quoteTimerRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPool, items.length]);

  const totalCount = pools.reduce((s, p) => s + p.count, 0);

  // 输入联想（debounce 300ms）
  useEffect(() => {
    if (suppressSuggestRef.current) {
      suppressSuggestRef.current = false;
      return;
    }
    const q = codeInput.trim();
    if (!q) {
      setSuggestions([]);
      setDropdownOpen(false);
      return;
    }
    const timer = setTimeout(async () => {
      const data = await safeFetch<StockCandidate[]>(`/api/watchlist/search?q=${encodeURIComponent(q)}`);
      if (data) {
        setSuggestions(data);
        setDropdownOpen(data.length > 0);
        setActiveIdx(-1);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [codeInput]);

  // ── 池管理操作 ──
  const handleCreatePool = async () => {
    const name = newPoolName.trim();
    if (!name) return;
    setError(null);
    try {
      const res = await fetch('/api/pools', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '创建失败'); return; }
      setNewPoolName('');
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const handleRenamePool = async (name: string) => {
    const newName = window.prompt(`将股池「${name}」改名为：`, name);
    if (!newName || newName.trim() === name) return;
    setError(null);
    try {
      const res = await fetch(`/api/pools/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName.trim() }),
      });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '改名失败'); return; }
      if (currentPool === name) setCurrentPool(newName.trim());
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const handleDeletePool = async (name: string) => {
    if (!window.confirm(`确定删除股池「${name}」？池内所有股票将一并删除，不可恢复。`)) return;
    setError(null);
    try {
      const res = await fetch(`/api/pools/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const json = await res.json();
      if (!json.success) { setError(json.error ?? '删除失败'); return; }
      if (currentPool === name) setCurrentPool('全部');
      await load();
    } catch { setError('请求失败，请检查后端服务'); }
  };

  const addStock = async (payload: { code?: string; q?: string }) => {
    setAdding(true);
    setError(null);
    setMultiCandidates([]);
    try {
      const res = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, note: noteInput.trim(), pool: targetPool }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '添加失败');
        return;
      }
      if (json.data?.multiple) {
        setMultiCandidates(json.data.candidates ?? []);
        return;
      }
      setCodeInput('');
      setNoteInput('');
      setSuggestions([]);
      setDropdownOpen(false);
      await load();
    } catch {
      setError('请求失败，请检查后端服务');
    } finally {
      setAdding(false);
    }
  };

  const handleAdd = () => {
    const q = codeInput.trim();
    if (!q) return;
    addStock({ q });
  };

  const handleSelectSuggestion = (c: StockCandidate) => {
    suppressSuggestRef.current = true;
    setDropdownOpen(false);
    setSuggestions([]);
    addStock({ code: c.code });
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (dropdownOpen && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx(i => Math.min(i + 1, suggestions.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx(i => Math.max(i - 1, -1));
        return;
      }
      if (e.key === 'Escape') {
        setDropdownOpen(false);
        return;
      }
      if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        handleSelectSuggestion(suggestions[activeIdx]);
        return;
      }
    }
    if (e.key === 'Enter') handleAdd();
  };

  const handleRemove = async (item: WatchlistItem) => {
    try {
      await fetch(`/api/watchlist/${item.code}?pool=${encodeURIComponent(item.pool)}`, { method: 'DELETE' });
      await load();
    } catch {
      // graceful: ignore
    }
  };

  const submitImport = async (init: { formData?: FormData; text?: string }) => {
    setImporting(true);
    setError(null);
    try {
      if (init.formData) init.formData.append('pool', targetPool);
      const res = await fetch('/api/watchlist/import', init.formData
        ? { method: 'POST', body: init.formData }
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: init.text, pool: targetPool }),
          });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '导入失败');
        return;
      }
      setImportResult(json.data as ImportResult);
      setPasteText('');
      await load();
    } catch {
      setError('导入请求失败，请检查后端服务');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const tabCls = (active: boolean) =>
    `px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
      active ? 'bg-rose-100 text-rose-700 ring-1 ring-rose-300' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
    }`;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] mb-6">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Star className="w-4 h-4 text-rose-400" />
        <h3 className="text-sm font-semibold text-gray-800">关注股池</h3>
        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
          {currentPool === '全部' ? totalCount : items.length} 只
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setManageOpen(o => !o)}
            title="新建 / 改名 / 删除股池"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                       bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                       transition-colors"
          >
            {manageOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            管理股池
          </button>
          <button
            onClick={() => onTrigger('watchlist', currentPool === '全部' ? undefined : currentPool)}
            disabled={running || items.length === 0}
            title={items.length === 0 ? '请先添加关注股票' : `AI 逐只体检${currentPool === '全部' ? '全部股池' : `「${currentPool}」`}：问题提醒 + 优势亮点`}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                       bg-rose-100 text-rose-700 hover:bg-rose-200 ring-1 ring-rose-300
                       transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Star className="w-3.5 h-3.5" />}
            股池动态分析{currentPool !== '全部' && ` · ${currentPool}`}
          </button>
        </div>
      </div>

      {/* 池切换 Tab */}
      <div className="flex items-center gap-1.5 mb-4 flex-wrap">
        <button onClick={() => setCurrentPool('全部')} className={tabCls(currentPool === '全部')}>
          全部（{totalCount}）
        </button>
        {pools.map(p => (
          <button key={p.name} onClick={() => setCurrentPool(p.name)} className={tabCls(currentPool === p.name)}>
            {p.name}（{p.count}）
          </button>
        ))}
      </div>

      {/* 池管理面板 */}
      {manageOpen && (
        <div className="mb-4 bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <input
              value={newPoolName}
              onChange={e => setNewPoolName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreatePool(); }}
              placeholder="新池名称，如 策略A（≤20字）"
              maxLength={20}
              className="w-52 px-3 py-2 rounded-lg border border-gray-200 text-sm
                         focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
            />
            <button
              onClick={handleCreatePool}
              disabled={!newPoolName.trim()}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                         bg-gray-900 text-white hover:bg-gray-700 transition-colors
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Plus className="w-3.5 h-3.5" />
              创建
            </button>
          </div>
          <div className="space-y-1.5">
            {pools.map(p => (
              <div key={p.name} className="flex items-center gap-2 text-sm">
                <span className="font-semibold text-gray-800">{p.name}</span>
                <span className="text-xs text-gray-400">{p.count} 只</span>
                {p.name !== '默认' && (
                  <span className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => handleRenamePool(p.name)}
                      className="px-2 py-1 rounded-md text-xs text-gray-500 hover:bg-gray-200 transition-colors"
                    >改名</button>
                    <button
                      onClick={() => handleDeletePool(p.name)}
                      className="px-2 py-1 rounded-md text-xs text-red-500 hover:bg-red-50 transition-colors"
                    >删除</button>
                  </span>
                )}
                {p.name === '默认' && <span className="ml-auto text-xs text-gray-300">内置池</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 添加表单（搜索联想） */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative">
          <input
            value={codeInput}
            onChange={e => setCodeInput(e.target.value)}
            onKeyDown={handleInputKeyDown}
            onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
            onFocus={() => { if (suggestions.length > 0) setDropdownOpen(true); }}
            placeholder="代码 / 拼音简写 / 名称，如 gzmt"
            maxLength={20}
            className="w-52 px-3 py-2 rounded-lg border border-gray-200 text-sm font-mono
                       focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
          />
          {dropdownOpen && suggestions.length > 0 && (
            <div className="absolute z-20 mt-1 w-64 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
              {suggestions.map((s, idx) => (
                <button
                  key={s.code}
                  onMouseDown={e => { e.preventDefault(); handleSelectSuggestion(s); }}
                  onMouseEnter={() => setActiveIdx(idx)}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                    idx === activeIdx ? 'bg-rose-50' : 'hover:bg-gray-50'
                  }`}
                >
                  <span className="font-semibold text-gray-900">{s.name || '未知名称'}</span>
                  <span className="text-xs font-mono text-gray-400">{s.code}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <input
          value={noteInput}
          onChange={e => setNoteInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd(); }}
          placeholder="备注（可选），如：成本 1300"
          maxLength={50}
          className="flex-1 min-w-[160px] px-3 py-2 rounded-lg border border-gray-200 text-sm
                     focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300"
        />
        <button
          onClick={handleAdd}
          disabled={adding || !codeInput.trim()}
          title={`添加到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                     bg-gray-900 text-white hover:bg-gray-700 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {adding ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          添加到{targetPool}
        </button>
      </div>

      {/* 多命中候选 */}
      {multiCandidates.length > 0 && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <div className="text-xs text-amber-600 font-semibold mb-2">找到多个匹配，请选择：</div>
          <div className="flex flex-wrap gap-2">
            {multiCandidates.map(c => (
              <button
                key={c.code}
                onClick={() => addStock({ code: c.code })}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                           bg-white border border-amber-200 text-gray-700 hover:bg-amber-100 transition-colors"
              >
                <span className="font-semibold">{c.name || '未知名称'}</span>
                <span className="font-mono text-gray-400">{c.code}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 导入区 */}
      <div className="flex items-center gap-2 mb-4">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.txt"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (!file) return;
            const fd = new FormData();
            fd.append('file', file);
            submitImport({ formData: fd });
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={importing}
          title={`从 CSV/TXT 文件批量导入到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                     bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                     transition-colors disabled:opacity-50"
        >
          {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          导入 CSV → {targetPool}
        </button>
        <button
          onClick={() => setPasteOpen(o => !o)}
          title={`粘贴股票列表文本批量导入到「${targetPool}」池`}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold
                     bg-gray-100 text-gray-600 hover:bg-gray-200 ring-1 ring-gray-300
                     transition-colors"
        >
          {pasteOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          批量粘贴导入
        </button>
      </div>

      {/* 批量粘贴导入 */}
      {pasteOpen && (
        <div className="mb-4 bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-2">
            导入到「{targetPool}」池。每行一只股票，支持 600519 / 600519.SH / sh600519 等写法；可带表头（code,note）；# 开头为注释行
          </div>
          <textarea
            value={pasteText}
            onChange={e => setPasteText(e.target.value)}
            placeholder={'code,note\n600519,成本 1300\n300750.SZ\nsz002594'}
            rows={5}
            className="w-full px-3 py-2 rounded-lg border border-gray-200 text-xs font-mono
                       focus:outline-none focus:ring-2 focus:ring-rose-200 focus:border-rose-300 mb-2"
          />
          <button
            onClick={() => { if (pasteText.trim()) submitImport({ text: pasteText }); }}
            disabled={importing || !pasteText.trim()}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                       bg-gray-900 text-white hover:bg-gray-700 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            导入
          </button>
        </div>
      )}

      {/* 导入结果提示条 */}
      {importResult && (
        <div className="mb-4 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-emerald-800">
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
            <span>
              导入{importResult.pool ? `「${importResult.pool}」` : ''}完成：新增 <b>{importResult.added}</b> 只，
              跳过已存在 <b>{importResult.skipped_existing}</b> 只，
              失败 <b>{importResult.failed.length}</b> 行
              （共解析 {importResult.total} 行）
            </span>
            <button
              onClick={() => setImportResult(null)}
              className="ml-auto text-emerald-500 hover:text-emerald-700 font-semibold"
            >×</button>
          </div>
          {importResult.failed.length > 0 && (
            <div className="mt-2 space-y-1">
              {importResult.failed.map((f, i) => (
                <div key={i} className="text-xs text-red-600 font-mono truncate">
                  ✗ {f.raw} — {f.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700 mb-4">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* 股池列表 */}
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">
          {currentPool === '全部' ? '股池为空' : `「${currentPool}」池为空`}，输入代码 / 拼音简写 / 名称添加关注股票（如 gzmt → 贵州茅台）
        </p>
      ) : (
        <div className="divide-y divide-gray-50">
          {items.map(item => {
            const q = quoteMap[item.code];
            // A 股红涨绿跌
            const chgPct = q?.change_pct ?? null;
            const chg = q?.change ?? null;
            const lastPrice = q?.last_price ?? null;
            const isUp = (chgPct ?? 0) > 0;
            const isDown = (chgPct ?? 0) < 0;
            const chgColor = !q?.has_quote
              ? 'text-gray-300'
              : isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-gray-700';
            return (
              <div key={`${item.pool}-${item.code}`} className="flex items-center gap-3 py-2.5">
                <span className="text-sm font-semibold text-gray-900 shrink-0">
                  {item.name || <span className="text-gray-300">未知名称</span>}
                </span>
                <span className="text-xs font-mono text-gray-400 shrink-0">{item.code}</span>
                {currentPool === '全部' && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-rose-50 text-rose-500 font-medium shrink-0">
                    {item.pool}
                  </span>
                )}
                {item.note && (
                  <span className="text-xs text-gray-500 bg-gray-50 border border-gray-100 px-2 py-0.5 rounded-md truncate max-w-[200px]">
                    {item.note}
                  </span>
                )}

                {/* 实时报价三联 (QMT) */}
                <div className="ml-auto flex items-center gap-3 shrink-0 tabular-nums">
                  <span className={`text-sm font-mono font-semibold ${chgColor}`} title="现价 (QMT)">
                    {lastPrice == null ? '—' : lastPrice.toFixed(lastPrice >= 100 ? 2 : 2)}
                  </span>
                  <span className={`text-sm font-mono font-semibold w-16 text-right ${chgColor}`} title="涨跌幅 (QMT)">
                    {chgPct == null ? '—' : `${isUp ? '+' : ''}${chgPct.toFixed(2)}%`}
                  </span>
                  <span className={`text-xs font-mono w-16 text-right ${chgColor}`} title="涨跌额 (QMT)">
                    {chg == null ? '—' : `${isUp ? '+' : ''}${chg.toFixed(2)}`}
                  </span>
                </div>

                <button
                  onClick={() => handleRemove(item)}
                  title={`移出「${item.pool}」池`}
                  className="shrink-0 p-1.5 rounded-lg text-gray-300
                             hover:text-red-500 hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 报价状态条 */}
      {items.length > 0 && (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-gray-400">
          <span>
            报价: {bridgeState === 'closed' ? 'QMT 连接正常' : bridgeState ? `QMT 桥 ${bridgeState}` : 'QMT 检测中...'}
          </span>
          {quoteUpdatedAt && (
            <span title={quoteUpdatedAt}>
              · 刷新于 {new Date(quoteUpdatedAt).toLocaleTimeString('zh-CN', { hour12: false })}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Watchlist History Panel ──────────────────────────────────────────────────

function WatchlistHistoryPanel({ items }: { items: HistoryItem[] }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | string | null>(null);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-[var(--shadow-sm)] overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-semibold text-gray-800">股池分析历史</span>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
            {items.length} 条
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </button>

      {open && (
        <div className="border-t border-gray-100">
          {items.length === 0 ? (
            <div className="px-6 py-8 text-center text-sm text-gray-400">暂无股池分析记录</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {items.map(item => {
                const isExpanded = expanded === item.id;
                return (
                  <div key={item.id}>
                    <button
                      onClick={() => setExpanded(isExpanded ? null : item.id)}
                      className="w-full flex items-center gap-3 px-6 py-3.5 hover:bg-gray-50 transition-colors text-left"
                    >
                      <span className="shrink-0 text-xs px-2 py-0.5 rounded font-semibold bg-rose-50 text-rose-600">
                        股池动态
                      </span>
                      <span className="shrink-0 text-xs font-mono text-gray-500">{item.run_time}</span>
                      {item.summary && (
                        <span className="text-xs text-gray-400 truncate flex-1">{item.summary}</span>
                      )}
                      <span className="ml-auto shrink-0">
                        {isExpanded
                          ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                          : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
                      </span>
                    </button>

                    {isExpanded && (
                      <div className="px-6 pb-6 pt-4 bg-gray-50 border-t border-gray-100">
                        {item.has_html && typeof item.id === 'number' ? (
                          <HtmlReportView reportId={item.id} />
                        ) : item.summary ? (
                          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{item.summary}</p>
                        ) : (
                          <div className="py-6 text-center text-sm text-gray-400">该记录无可用内容</div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Error Boundary ───────────────────────────────────────────────────────────

class WatchlistErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
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

// ─── Main Page ────────────────────────────────────────────────────────────────

function WatchlistPageInner() {
  const [status, setStatus]     = useState<AgentStatus | null>(null);
  const [history, setHistory]   = useState<HistoryItem[]>([]);
  const [error, setError]       = useState<string | null>(null);

  const pollRef         = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopConfirmRef  = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const loadHistory = async () => {
    const raw = await safeFetch<Array<{ id: number | string; run_time: string; run_type: string; summary_text?: string; summary_time?: string; has_html?: boolean }>>('/api/agent/history?limit=50');
    if (raw) {
      setHistory(raw
        .filter(item => item.run_type === 'watchlist')
        .map(item => ({
          id: item.id,
          run_time: item.run_time,
          run_type: item.run_type,
          summary: item.summary_text,
          has_html: item.has_html,
        })));
    }
  };

  const loadStatus = async () => {
    const data = await safeFetch<AgentStatus>('/api/agent/status');
    if (data) setStatus(data);
    return data;
  };

  const startPolling = () => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      const s = await loadStatus();
      if (s && !s.running) {
        stopPoll();
        await loadHistory();
      }
    }, 2000);
  };

  useEffect(() => {
    loadHistory();
    loadStatus().then(s => {
      if (s?.running) startPolling();
    });
    return () => {
      stopPoll();
      if (stopConfirmRef.current) clearInterval(stopConfirmRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleTrigger = async (run_type: 'watchlist', pool?: string) => {
    setError(null);
    try {
      const res = await fetch('/api/agent/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pool ? { run_type, pool } : { run_type }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error ?? '启动失败');
        return;
      }
      // 已有其他管道在跑（盘中 / 盘后 / 研报 / 政策 …）：后端返回 success=true
      // 但 data.status='already_running'，给用户友好提示而不是显示别家的进度条
      if (json.data?.status === 'already_running') {
        setError(formatAlreadyRunningMessage(json.data as AlreadyRunningData));
        return;
      }
      const newStatus = await loadStatus();
      if (newStatus?.running) startPolling();
    } catch {
      setError('请求失败，请检查后端服务');
    }
  };

  const handleStop = async () => {
    if (stopConfirmRef.current) {
      clearInterval(stopConfirmRef.current);
      stopConfirmRef.current = null;
    }
    try {
      await fetch('/api/agent/stop', { method: 'POST' });
    } catch {
      // graceful: ignore errors
    }
    let attempts = 0;
    const confirm = setInterval(async () => {
      attempts++;
      const s = await loadStatus();
      if ((s && !s.running) || attempts >= 8) {
        clearInterval(confirm);
        stopPoll();
        if (s && !s.running) await loadHistory();
      }
    }, 2000);
    stopConfirmRef.current = confirm;
  };

  const isRunning = status?.running ?? false;

  const lastRunLabel = status?.last_run && status.last_run_type === 'watchlist'
    ? `${status.last_run}（股池动态）`
    : undefined;

  const latestReport = history.length > 0 ? history[0] : null;

  return (
    <div className="space-y-0">
      <TabHeader
        title="关注股池"
        subtitle="股池管理与 AI 股池动态分析"
        lastUpdate={lastRunLabel}
      />

      {error && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-800 mb-6">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {status?.last_error && !isRunning && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800 mb-6">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-amber-500" />
          <div>
            <div className="font-semibold mb-0.5">上次运行出现错误</div>
            <div className="text-amber-700">{status.last_error}</div>
          </div>
        </div>
      )}

      {isRunning && status && (
        <PhaseBar status={status} onStop={handleStop} />
      )}

      <WatchlistPanel running={isRunning} onTrigger={handleTrigger} />

      {/* 最新股池分析结果 */}
      {latestReport ? (
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Star className="w-4 h-4 text-rose-400" />
            <h3 className="text-sm font-semibold text-gray-800">最新股池分析结果</h3>
            <span className="text-xs font-mono text-gray-400">{latestReport.run_time}</span>
          </div>
          {latestReport.has_html && typeof latestReport.id === 'number' ? (
            <HtmlReportView reportId={latestReport.id} />
          ) : (
            <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
              <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{latestReport.summary ?? ''}</p>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-gray-100 p-12 shadow-[var(--shadow-sm)]
                        flex flex-col items-center justify-center gap-3 text-center mb-6">
          <Brain className="w-10 h-10 text-gray-200" />
          <p className="text-sm font-semibold text-gray-400">暂无股池分析报告</p>
          <p className="text-xs text-gray-300">点击「股池动态分析」按钮启动 AI 逐只体检</p>
        </div>
      )}

      <div className="mt-6">
        <WatchlistHistoryPanel items={history} />
      </div>
    </div>
  );
}

export function WatchlistPage() {
  return (
    <WatchlistErrorBoundary>
      <WatchlistPageInner />
    </WatchlistErrorBoundary>
  );
}
