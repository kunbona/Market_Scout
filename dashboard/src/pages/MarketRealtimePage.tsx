import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { apiFetch } from '../lib/api';

// ─── 格式化 ───────────────────────────────────────────────────
const fmtYi   = (v?: number | null) => v != null ? `${(v / 1e8).toFixed(2)}亿` : '--';
const fmtWan  = (v?: number | null) => v != null ? `${(v / 1e4).toFixed(0)}万` : '--';
const fmtPct  = (v?: number | null, plus = true) =>
  v != null ? `${plus && v > 0 ? '+' : ''}${v.toFixed(1)}%` : '--';
const fmtTime = (t?: string | null) =>
  t && t.length >= 4 ? `${t.slice(0, 2)}:${t.slice(2, 4)}` : (t ?? '--');
const cleanCode = (c: string) => c.replace(/^(SZ|SH|sz|sh)/i, '');
const fmtFollow = (n?: number | null) =>
  n != null ? (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n))) : '--';

// A 股：红涨绿跌
function numColor(v?: number | null) {
  if (v == null) return 'text-gray-600';
  if (v > 0) return 'text-red-600';
  if (v < 0) return 'text-green-600';
  return 'text-gray-600';
}

// ─── 迷你条形图（各自用自己列的最大值）──────────────────────
function MiniBar({ ratio, isInflow }: { ratio: number; isInflow: boolean }) {
  return (
    <div className="w-7 h-1.5 bg-gray-100 rounded-full overflow-hidden">
      <div
        className="h-full rounded-full"
        style={{
          width: `${Math.min(100, Math.max(2, ratio * 100))}%`,
          background: isInflow ? '#dc262666' : '#16a34a66',
        }}
      />
    </div>
  );
}

// ─── 折叠卡片容器 ────────────────────────────────────────────
// 默认显示前 HEAD 条 + 后 TAIL 条，中间折叠
const HEAD = 10;
const TAIL = 10;

function DataRows({ rows, cols }: { rows: React.ReactNode[][]; cols: number }) {
  return (
    <div className="divide-y divide-gray-50">
      {rows.map((row, ri) => (
        <div
          key={ri}
          className="grid px-3 py-2 hover:bg-gray-50 transition-colors text-xs"
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {row.map((cell, ci) => (
            <div key={ci} className={`flex items-center min-w-0 ${ci === 0 ? '' : 'justify-center'}`}>
              <span className="truncate">{cell}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function Widget({
  title, dot, count, headers, rows,
}: {
  title: string;
  dot: string;
  count?: number;
  headers: string[];
  rows: React.ReactNode[][];
}) {
  const [expanded, setExpanded] = useState(false);
  const cols = headers.length;
  const total = rows.length;
  const needFold = total > HEAD + TAIL;
  const hiddenCount = needFold ? total - HEAD - TAIL : 0;

  const head = rows.slice(0, HEAD);
  const tail = needFold ? rows.slice(total - TAIL) : [];
  const middle = needFold ? rows.slice(HEAD, total - TAIL) : [];

  return (
    <div className="widget-card bg-white rounded-xl border border-gray-100 overflow-hidden">
      {/* 标题栏：折叠按钮移到右上角 */}
      <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: dot }} />
        <span className="text-sm font-semibold text-gray-900">{title}</span>
        <span className="ml-auto flex items-center gap-2">
          {count != null && (
            <span className="text-xs font-mono text-gray-400">{count} 条</span>
          )}
          {needFold && (
            <button
              onClick={() => setExpanded(e => !e)}
              className="flex items-center gap-0.5 px-2 py-1 text-xs text-gray-400
                         hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
            >
              {expanded
                ? <><ChevronUp className="w-3 h-3" />收起</>
                : <><ChevronDown className="w-3 h-3" />展开全部</>}
            </button>
          )}
        </span>
      </div>
      {/* 列头 */}
      <div
        className="grid px-3 py-1.5 bg-gray-50 border-b border-gray-100 text-xs text-gray-400 font-medium"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      >
        {headers.map((h, i) => (
          <span key={i} className={i === 0 ? '' : 'text-center'}>{h}</span>
        ))}
      </div>

      {/* 前 HEAD 条 */}
      <DataRows rows={needFold ? head : rows} cols={cols} />

      {/* 中间折叠区 */}
      {needFold && (
        <>
          {expanded ? (
            <DataRows rows={middle} cols={cols} />
          ) : (
            <div className="flex items-center justify-center py-2 border-y border-dashed border-gray-200 bg-gray-50">
              <span className="text-xs text-gray-400">—— 中间 {hiddenCount} 条已折叠 ——</span>
            </div>
          )}
          {/* 后 TAIL 条 */}
          <DataRows rows={tail} cols={cols} />
        </>
      )}
    </div>
  );
}

function EmptyCard({ msg }: { msg: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8
                    flex items-center justify-center text-sm text-gray-400">
      {msg}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-base font-semibold text-gray-800 mb-3">{children}</h3>;
}

// ─── 大单成交额辅助 ────────────────────────────────────────────
// akshare 返回百万元单位，≥100百万（即≥1亿）显示亿，否则换算万
const fmtDealAmt = (v?: number | null) =>
  v == null ? '--' : v >= 100 ? `${(v / 100).toFixed(2)}亿` : `${(v * 100).toFixed(0)}万`;

// ─── 类型 ─────────────────────────────────────────────────────
interface SF     { sector_name: string; change_pct: number; main_inflow: number; }
interface CF     { concept: string; change_pct: number; net_amount: number; lead_stock: string; lead_pct: number; }
interface ZT     { stock_code: string; stock_name: string; zt_count: number; first_zt_time: string; sector: string; zb_count: number; seal_amount: number; }
interface DT     { stock_code: string; stock_name: string; sector: string; first_dt_time: string; }
interface ZBGC   { stock_code: string; stock_name: string; first_zt_time: string; zb_count: number; amplitude: number; sector: string; }
interface LHB    { stock_code: string; stock_name: string; net_buy: number; change_pct: number; interpret: string; }
interface STRONG { stock_code: string; stock_name: string; change_pct: number; is_new_high: string; volume_ratio: number; reason: string; }
interface HR     { stock_code: string; stock_name: string; rank_change: number; current_rank: number; change_pct: number; }
interface NB     { channel: string; direction: string; net_buy: number; net_inflow: number; }
interface XQ     { rank: number; stock_code: string; stock_name: string; follow_cnt: number; }
interface BigDeal {
  deal_time: string;
  stock_code: string;
  stock_name: string;
  price: number;
  volume: number;
  amount: number;
  deal_type: string;
  change_pct: number | null;
}
interface Margin {
  stock_code: string; stock_name: string;
  trade_date: string;
  rzye: number; rzmre: number; rzrqye: number;
}
interface BlockTrade {
  trade_date: string; stock_code: string; stock_name: string;
  deal_price: number; close_price: number;
  deal_volume: number; deal_amt: number;
  buyer_name: string; seller_name: string;
}
interface HolderCount {
  stock_code: string; stock_name: string; end_date: string;
  holder_num: number; holder_num_change: number; holder_num_ratio: number;
}

export function MarketRealtimePage() {
  const [sf,     setSf]     = useState<SF[]>([]);
  const [cf,     setCf]     = useState<CF[]>([]);
  const [zt,     setZt]     = useState<ZT[]>([]);
  const [dt,     setDt]     = useState<DT[]>([]);
  const [zbgc,   setZbgc]   = useState<ZBGC[]>([]);
  const [lhb,    setLhb]    = useState<LHB[]>([]);
  const [strong, setStrong] = useState<STRONG[]>([]);
  const [hr,     setHr]     = useState<HR[]>([]);
  const [nb,     setNb]     = useState<NB[]>([]);
  const [xq,     setXq]     = useState<XQ[]>([]);
  const [bigDeal,  setBigDeal]  = useState<BigDeal[]>([]);
  const [margin,   setMargin]   = useState<Margin[]>([]);
  const [blockTrd, setBlockTrd] = useState<BlockTrade[]>([]);
  const [holder,   setHolder]   = useState<HolderCount[]>([]);

  useEffect(() => {
    apiFetch<SF[]>('/api/sector-flow?type=industry').then(d => setSf(d ?? [])).catch(() => {});
    apiFetch<CF[]>('/api/concept-flow?top_n=50').then(d => setCf(d ?? [])).catch(() => {});
    apiFetch<ZT[]>('/api/zt-pool?date=').then(d => setZt(d ?? [])).catch(() => {});
    apiFetch<DT[]>('/api/dt-pool?date=').then(d => setDt(d ?? [])).catch(() => {});
    apiFetch<ZBGC[]>('/api/zbgc-pool?date=').then(d => setZbgc(d ?? [])).catch(() => {});
    apiFetch<LHB[]>('/api/lhb?date=').then(d => setLhb(d ?? [])).catch(() => {});
    apiFetch<STRONG[]>('/api/strong-pool?date=').then(d => setStrong(d ?? [])).catch(() => {});
    apiFetch<HR[]>('/api/hot-rank-up?top_n=50').then(d => setHr(d ?? [])).catch(() => {});
    apiFetch<NB[]>('/api/northbound-flow').then(d => setNb(d ?? [])).catch(() => {});
    apiFetch<XQ[]>('/api/xq-hot?top_n=50').then(d => setXq(d ?? [])).catch(() => {});
  }, []);

  useEffect(() => {
    const load = () =>
      apiFetch<BigDeal[]>('/api/big-deal?limit=100')
        .then(d => setBigDeal(d ?? []))
        .catch(() => {});
    load();
    const timer = setInterval(load, 3 * 60 * 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    Promise.allSettled([
      apiFetch<Margin[]>('/api/margin?top_n=100'),
      apiFetch<BlockTrade[]>('/api/block-trade?limit=100'),
      apiFetch<HolderCount[]>('/api/holder-count?top_n=100'),
    ]).then(([r0, r1, r2]) => {
      if (r0.status === 'fulfilled' && r0.value) setMargin(r0.value);
      if (r1.status === 'fulfilled' && r1.value) setBlockTrd(r1.value);
      if (r2.status === 'fulfilled' && r2.value) setHolder(r2.value);
    });
  }, []);

  // ── 汇总 KPI 计算 ────────────────────────────────────────────
  const boardDist = zt.reduce<Record<number, number>>((acc, r) => {
    const n = r.zt_count ?? 1;
    acc[n] = (acc[n] ?? 0) + 1;
    return acc;
  }, {});
  const zt1 = boardDist[1] ?? 0;
  const ztMulti = Object.entries(boardDist)
    .filter(([k]) => Number(k) >= 2)
    .reduce((s, [, v]) => s + v, 0);
  const sfInCount  = sf.filter(d => d.main_inflow > 0).length;
  const sfOutCount = sf.filter(d => d.main_inflow < 0).length;
  const nbNorth    = nb.filter(d => d.direction === '北向').reduce((s, d) => s + (d.net_buy ?? 0), 0);
  const nbSouth    = nb.filter(d => d.direction === '南向').reduce((s, d) => s + (d.net_buy ?? 0), 0);
  const lhbNetBuy  = lhb.reduce((s, d) => s + (d.net_buy ?? 0), 0);
  const strongNewHigh = strong.filter(d => d.is_new_high === '是').length;
  const cfTopConcept  = [...cf].sort((a, b) => b.net_amount - a.net_amount)[0];

  const zbRate = (zt.length + zbgc.length) > 0
    ? (zbgc.length / (zt.length + zbgc.length) * 100).toFixed(1)
    : '--';
  const sfNetTotal = sf.reduce((s, d) => s + (d.main_inflow ?? 0), 0);

  // ── 行业资金流（各自最大值） ─────────────────────────────────
  const sfSorted  = [...sf].sort((a, b) => b.main_inflow - a.main_inflow);
  const sfIn      = sfSorted.filter(d => d.main_inflow >= 0);
  const sfOut     = sfSorted.filter(d => d.main_inflow < 0).reverse();
  const sfInMax   = Math.max(...sfIn.map(d => d.main_inflow), 1);
  const sfOutMax  = Math.max(...sfOut.map(d => Math.abs(d.main_inflow)), 1);

  // ── 概念资金流（各自最大值） ─────────────────────────────────
  const cfSorted   = [...cf].sort((a, b) => b.net_amount - a.net_amount);
  const cfIn       = cfSorted.filter(d => d.net_amount >= 0);
  const cfOut      = cfSorted.filter(d => d.net_amount < 0).reverse();
  const cfFallback = [...cf].sort((a, b) => (a.change_pct ?? 0) - (b.change_pct ?? 0));
  const cfOutDisp  = cfOut.length > 0 ? cfOut : cfFallback;
  const cfOutTitle = cfOut.length > 0 ? '概念资金流出' : '概念涨幅最低';
  const cfInMax    = Math.max(...cfIn.map(d => d.net_amount), 1);
  const cfOutMax   = Math.max(...cfOutDisp.map(d => Math.abs(d.net_amount)), 1);

  const north = nb.filter(d => d.direction === '北向');
  const south = nb.filter(d => d.direction === '南向');

  return (
    <div className="space-y-8">

      {/* 0. 汇总 KPI 栏 */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">

        {/* 涨停：红色 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">今日涨停</div>
          <div className="text-2xl font-bold text-red-600">{zt.length}</div>
          <div className="flex gap-2 mt-1.5 text-xs">
            <span className="text-gray-500">1板 <b className="text-gray-700">{zt1}</b></span>
            <span className="text-gray-500">连板 <b className="text-red-500">{ztMulti}</b></span>
          </div>
        </div>

        {/* 跌停：绿色 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">今日跌停</div>
          <div className="text-2xl font-bold text-green-600">{dt.length}</div>
          <div className="text-xs text-gray-500 mt-1.5">
            炸板 <b className="text-amber-500">{zbgc.length}</b>
            <span className="ml-1 text-gray-400">（{zbRate}%）</span>
          </div>
        </div>

        {/* 行业资金 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">行业资金净额</div>
          <div className={`text-lg font-bold ${sfNetTotal >= 0 ? 'text-red-600' : 'text-green-600'}`}>
            {sfNetTotal >= 0 ? '+' : ''}{(sfNetTotal / 1e8).toFixed(0)}亿
          </div>
          <div className="flex gap-1.5 mt-1.5 text-xs">
            <span className="text-red-400">↑{sfInCount}板块</span>
            <span className="text-green-500">↓{sfOutCount}板块</span>
          </div>
        </div>

        {/* 最热概念 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">概念资金最热</div>
          <div className="text-sm font-bold text-purple-600 truncate">
            {cfTopConcept?.concept ?? '--'}
          </div>
          <div className="text-xs text-gray-400 mt-1.5 truncate">
            {cfTopConcept
              ? <><span className="text-red-500">+{cfTopConcept.net_amount.toFixed(1)}亿</span>
                  <span className="ml-1">{cfTopConcept.lead_stock}</span></>
              : '--'}
          </div>
        </div>

        {/* 龙虎榜 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">龙虎榜机构净买</div>
          <div className={`text-lg font-bold ${lhbNetBuy >= 0 ? 'text-red-600' : 'text-green-600'}`}>
            {fmtWan(lhbNetBuy)}
          </div>
          <div className="text-xs text-gray-400 mt-1.5">共 {lhb.length} 只上榜</div>
        </div>

        {/* 强势股 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">强势股</div>
          <div className="text-2xl font-bold text-red-600">{strong.length}</div>
          <div className="text-xs text-gray-500 mt-1.5">
            创新高 <b className="text-red-500">{strongNewHigh}</b>
            <span className="ml-1 text-gray-400">
              ({strong.length > 0 ? ((strongNewHigh / strong.length) * 100).toFixed(0) : '--'}%)
            </span>
          </div>
        </div>

        {/* 北向/南向 */}
        <div className="kpi-card card-hover bg-white rounded-xl border border-gray-100 p-3">
          <div className="text-xs text-gray-400 mb-2">沪深港通资金</div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs px-1.5 py-0.5 rounded font-bold"
                style={{ background: '#eff6ff', color: '#3b82f6' }}>北向</span>
              <span className={`text-sm font-bold font-mono ${nbNorth >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                {nbNorth >= 0 ? '+' : ''}{nbNorth.toFixed(1)}亿
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs px-1.5 py-0.5 rounded font-bold"
                style={{ background: '#fff7ed', color: '#f97316' }}>南向</span>
              <span className={`text-sm font-bold font-mono ${nbSouth >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                {nbSouth >= 0 ? '+' : ''}{nbSouth.toFixed(1)}亿
              </span>
            </div>
          </div>
        </div>

      </div>

      {/* 1. 行业资金流 */}
      <div>
        <SectionTitle>行业资金流</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget title="行业资金流入" dot="#ef4444" count={sfIn.length}
            headers={['行业', '净流入', '迷你图', '涨跌幅']}
            rows={sfIn.map((d, i) => [
              <span className="flex items-center gap-1.5">
                <span className="text-gray-400 font-mono w-4 flex-shrink-0">{i+1}</span>
                <span className="font-medium text-gray-800">{d.sector_name}</span>
              </span>,
              <span className="font-mono text-red-600">+{fmtYi(d.main_inflow)}</span>,
              <MiniBar ratio={d.main_inflow / sfInMax} isInflow={true} />,
              <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
            ])}
          />
          <Widget title="行业资金流出" dot="#22c55e" count={sfOut.length}
            headers={['行业', '净流出', '迷你图', '涨跌幅']}
            rows={sfOut.map((d, i) => [
              <span className="flex items-center gap-1.5">
                <span className="text-gray-400 font-mono w-4 flex-shrink-0">{i+1}</span>
                <span className="font-medium text-gray-800">{d.sector_name}</span>
              </span>,
              <span className="font-mono text-green-600">{fmtYi(d.main_inflow)}</span>,
              <MiniBar ratio={Math.abs(d.main_inflow) / sfOutMax} isInflow={false} />,
              <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
            ])}
          />
        </div>
      </div>

      {/* 2. 涨停池 & 跌停池 */}
      <div>
        <SectionTitle>涨停池 & 跌停池</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget title="今日涨停" dot="#ef4444" count={zt.length}
            headers={['代码', '名称', '连板', '封板时间', '炸板', '封单', '板块']}
            rows={zt.map(d => [
              <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
              <span className="font-medium text-gray-800">{d.stock_name}</span>,
              <span className="px-1.5 py-0.5 rounded font-bold text-xs"
                style={{ background: '#fee2e2', color: '#dc2626' }}>{d.zt_count}板</span>,
              <span className="font-mono text-gray-500">{fmtTime(d.first_zt_time)}</span>,
              (d.zb_count ?? 0) > 0
                ? <span className="font-bold text-green-600">炸板{d.zb_count}次</span>
                : <span className="text-gray-300">—</span>,
              <span className="font-mono text-gray-600">{d.seal_amount ? fmtYi(d.seal_amount) : '—'}</span>,
              <span className="text-gray-500 truncate">{d.sector}</span>,
            ])}
          />
          {dt.length > 0 ? (
            <Widget title="今日跌停" dot="#22c55e" count={dt.length}
              headers={['代码', '名称', '状态', '封板时间', '板块']}
              rows={dt.map(d => [
                <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                <span className="font-medium text-gray-800">{d.stock_name}</span>,
                <span className="px-1.5 py-0.5 rounded font-bold text-xs"
                  style={{ background: '#dcfce7', color: '#16a34a' }}>跌停</span>,
                <span className="font-mono text-gray-500">{fmtTime(d.first_dt_time)}</span>,
                <span className="text-gray-500 truncate">{d.sector}</span>,
              ])}
            />
          ) : (
            <EmptyCard msg="今日暂无跌停股票" />
          )}
        </div>
      </div>

      {/* 3. 炸板池 & 雪球热度 */}
      <div>
        <SectionTitle>炸板池 & 雪球关注热度</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget title="炸板池" dot="#f59e0b" count={zbgc.length}
            headers={['代码', '名称', '炸板', '首封时间', '振幅', '板块']}
            rows={zbgc.map(d => [
              <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
              <span className="font-medium text-gray-800">{d.stock_name}</span>,
              <span className="px-1.5 py-0.5 rounded font-bold text-xs"
                style={{ background: '#fef3c7', color: '#d97706' }}>炸板{d.zb_count}次</span>,
              <span className="font-mono text-gray-500">{fmtTime(d.first_zt_time)}</span>,
              <span className="font-mono text-gray-600">
                {d.amplitude != null ? `${d.amplitude.toFixed(1)}%` : '—'}
              </span>,
              <span className="text-gray-500 truncate">{d.sector}</span>,
            ])}
          />
          <Widget title="雪球关注热度" dot="#22c55e" count={xq.length}
            headers={['排名', '代码', '名称', '关注数']}
            rows={xq.map(d => [
              <span className="font-mono text-gray-400">{d.rank}</span>,
              <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
              <span className="font-medium text-gray-800">{d.stock_name}</span>,
              <span className="font-mono text-gray-600">{fmtFollow(d.follow_cnt)}</span>,
            ])}
          />
        </div>
      </div>

      {/* 4. 概念资金流 */}
      <div>
        <SectionTitle>概念资金流</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget title="概念资金流入" dot="#ef4444" count={cfIn.length}
            headers={['概念', '迷你图', '净额', '主力股 / 涨幅']}
            rows={cfIn.map((d, i) => [
              <span className="flex items-center gap-1.5">
                <span className="text-gray-400 font-mono w-4 flex-shrink-0">{i+1}</span>
                <span className="font-medium text-gray-800">{d.concept}</span>
              </span>,
              <MiniBar ratio={d.net_amount / cfInMax} isInflow={true} />,
              <span className="font-mono text-red-600">+{d.net_amount.toFixed(1)}亿</span>,
              <span className="text-gray-500 truncate">
                {d.lead_stock}{d.lead_pct != null ? ` ${fmtPct(d.lead_pct)}` : ''}
              </span>,
            ])}
          />
          <Widget title={cfOutTitle} dot="#22c55e" count={cfOutDisp.length}
            headers={['概念', '迷你图', '净额', '主力股 / 涨幅']}
            rows={cfOutDisp.map((d, i) => [
              <span className="flex items-center gap-1.5">
                <span className="text-gray-400 font-mono w-4 flex-shrink-0">{i+1}</span>
                <span className="font-medium text-gray-800">{d.concept}</span>
              </span>,
              <MiniBar ratio={Math.abs(d.net_amount) / cfOutMax} isInflow={false} />,
              <span className={`font-mono ${numColor(d.net_amount)}`}>
                {d.net_amount >= 0 ? '+' : ''}{d.net_amount.toFixed(1)}亿
              </span>,
              <span className="text-gray-500 truncate">
                {d.lead_stock}{d.lead_pct != null ? ` ${fmtPct(d.lead_pct)}` : ''}
              </span>,
            ])}
          />
        </div>
      </div>

      {/* 5. 龙虎榜 & 强势股 */}
      <div>
        <SectionTitle>龙虎榜 & 强势股</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {lhb.length > 0 ? (
            <Widget title="龙虎榜" dot="#3b82f6" count={lhb.length}
              headers={['代码', '名称', '涨跌幅', '机构解读', '净买额']}
              rows={lhb.map(d => [
                <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                <span className="font-medium text-gray-800">{d.stock_name}</span>,
                <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
                <span className="text-gray-500 truncate">{(d.interpret ?? '').slice(0, 14)}</span>,
                <span className={`font-mono ${numColor(d.net_buy)}`}>{fmtWan(d.net_buy)}</span>,
              ])}
            />
          ) : (
            <EmptyCard msg="今日龙虎榜数据暂未更新" />
          )}
          <Widget title="强势股" dot="#ef4444" count={strong.length}
            headers={['代码', '名称', '涨跌幅', '新高', '量比', '理由']}
            rows={strong.map(d => [
              <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
              <span className="font-medium text-gray-800">{d.stock_name}</span>,
              <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
              d.is_new_high === '是'
                ? <span className="font-bold text-red-600">新高</span>
                : <span className="text-gray-300">—</span>,
              <span className="font-mono text-gray-600">
                {d.volume_ratio != null ? `${d.volume_ratio.toFixed(1)}x` : '—'}
              </span>,
              <span className="text-gray-500 truncate">{d.reason}</span>,
            ])}
          />
        </div>
      </div>

      {/* 6. 人气飙升 & 北向/南向 */}
      <div>
        <SectionTitle>人气飙升 & 北向/南向资金</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget title="人气飙升" dot="#f97316" count={hr.length}
            headers={['排名变化', '代码', '名称', '涨跌幅', '当前排名']}
            rows={hr.map(d => [
              <span className="font-mono font-bold"
                style={{ color: (d.rank_change ?? 0) > 0 ? '#dc2626' : '#6b7280' }}>
                ↑{d.rank_change}
              </span>,
              <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
              <span className="font-medium text-gray-800">{d.stock_name}</span>,
              <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
              <span className="font-mono text-gray-400">#{d.current_rank}</span>,
            ])}
          />
          <Widget title="北向/南向资金（沪深港通）" dot="#3b82f6" count={nb.length}
            headers={['渠道', '方向', '净买额']}
            rows={[...north, ...south].map(d => {
              const isNorth = d.direction === '北向';
              return [
                <span className="font-medium text-gray-800">{d.channel}</span>,
                <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                  style={{
                    background: isNorth ? '#eff6ff' : '#fff7ed',
                    color: isNorth ? '#3b82f6' : '#f97316',
                  }}>{d.direction}</span>,
                <span className={`font-mono ${numColor(d.net_buy)}`}>
                  {d.net_buy >= 0 ? '+' : ''}{d.net_buy.toFixed(2)}亿
                </span>,
              ];
            })}
          />
        </div>
      </div>

      {/* 7. 大单异动 */}
      <div>
        <SectionTitle>大单实时异动</SectionTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Widget
            title="大单买盘"
            dot="#ef4444"
            count={bigDeal.filter(d => d.deal_type === '买盘').length}
            headers={['时间', '代码', '名称', '成交额', '涨跌幅']}
            rows={bigDeal
              .filter(d => d.deal_type === '买盘')
              .map(d => [
                <span className="font-mono text-gray-400">{d.deal_time?.slice(11, 16)}</span>,
                <span className="font-mono text-gray-500">{d.stock_code}</span>,
                <span className="font-medium text-gray-800">{d.stock_name}</span>,
                <span className="font-mono text-red-600">{fmtDealAmt(d.amount)}</span>,
                <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
              ])}
          />
          <Widget
            title="大单卖盘"
            dot="#22c55e"
            count={bigDeal.filter(d => d.deal_type === '卖盘').length}
            headers={['时间', '代码', '名称', '成交额', '涨跌幅']}
            rows={bigDeal
              .filter(d => d.deal_type === '卖盘')
              .map(d => [
                <span className="font-mono text-gray-400">{d.deal_time?.slice(11, 16)}</span>,
                <span className="font-mono text-gray-500">{d.stock_code}</span>,
                <span className="font-medium text-gray-800">{d.stock_name}</span>,
                <span className="font-mono text-green-600">{fmtDealAmt(d.amount)}</span>,
                <span className={`font-mono ${numColor(d.change_pct)}`}>{fmtPct(d.change_pct)}</span>,
              ])}
          />
        </div>
      </div>

      {/* 8. 融资融券 & 大宗交易 */}
      {(margin.length > 0 || blockTrd.length > 0) && (
        <div>
          <SectionTitle>融资融券 & 大宗交易</SectionTitle>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {margin.length > 0 ? (
              <Widget title="融资融券余额 Top" dot="#8b5cf6" count={margin.length}
                headers={['代码', '名称', '融资余额', '融资买入', '两融合计']}
                rows={margin.map(d => [
                  <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                  <span className="font-medium text-gray-800">{d.stock_name}</span>,
                  <span className="font-mono text-red-500">{fmtYi(d.rzye)}</span>,
                  <span className="font-mono text-gray-600">{fmtYi(d.rzmre)}</span>,
                  <span className="font-mono text-purple-600">{fmtYi(d.rzrqye)}</span>,
                ])}
              />
            ) : (
              <EmptyCard msg="融资融券数据暂无（交易时段内自动更新）" />
            )}
            {blockTrd.length > 0 ? (
              <Widget title="今日大宗交易" dot="#6366f1" count={blockTrd.length}
                headers={['代码', '名称', '成交价', '折溢价', '金额']}
                rows={blockTrd.map(d => {
                  const disc = d.close_price > 0
                    ? ((d.deal_price - d.close_price) / d.close_price * 100)
                    : null;
                  return [
                    <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                    <span className="font-medium text-gray-800">{d.stock_name}</span>,
                    <span className="font-mono text-gray-700">{d.deal_price?.toFixed(2)}</span>,
                    disc != null
                      ? <span className={`font-mono ${numColor(disc)}`}>{fmtPct(disc)}</span>
                      : <span className="text-gray-300">—</span>,
                    <span className="font-mono text-indigo-600">{fmtYi(d.deal_amt * 1e4)}</span>,
                  ];
                })}
              />
            ) : (
              <EmptyCard msg="大宗交易数据暂无（交易时段内自动更新）" />
            )}
          </div>
        </div>
      )}

      {/* 9. 股东人数变化 */}
      {holder.length > 0 && (
        <div>
          <SectionTitle>股东人数变化（最新报告期）</SectionTitle>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Widget
              title="股东人数减少（筹码集中）"
              dot="#ef4444"
              count={holder.filter(d => d.holder_num_change < 0).length}
              headers={['代码', '名称', '股东人数', '变化', '变化率']}
              rows={holder
                .filter(d => d.holder_num_change < 0)
                .map(d => [
                  <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                  <span className="font-medium text-gray-800">{d.stock_name}</span>,
                  <span className="font-mono text-gray-600">
                    {d.holder_num != null ? `${(d.holder_num / 1e4).toFixed(1)}万` : '--'}
                  </span>,
                  <span className="font-mono text-red-600">
                    {d.holder_num_change > 0 ? '+' : ''}{(d.holder_num_change / 1e4).toFixed(2)}万
                  </span>,
                  <span className={`font-mono ${numColor(d.holder_num_ratio)}`}>
                    {fmtPct(d.holder_num_ratio)}
                  </span>,
                ])}
            />
            <Widget
              title="股东人数增加（筹码分散）"
              dot="#22c55e"
              count={holder.filter(d => d.holder_num_change >= 0).length}
              headers={['代码', '名称', '股东人数', '变化', '变化率']}
              rows={holder
                .filter(d => d.holder_num_change >= 0)
                .map(d => [
                  <span className="font-mono text-gray-500">{cleanCode(d.stock_code)}</span>,
                  <span className="font-medium text-gray-800">{d.stock_name}</span>,
                  <span className="font-mono text-gray-600">
                    {d.holder_num != null ? `${(d.holder_num / 1e4).toFixed(1)}万` : '--'}
                  </span>,
                  <span className="font-mono text-green-600">
                    +{(d.holder_num_change / 1e4).toFixed(2)}万
                  </span>,
                  <span className={`font-mono ${numColor(d.holder_num_ratio)}`}>
                    {fmtPct(d.holder_num_ratio)}
                  </span>,
                ])}
            />
          </div>
        </div>
      )}

    </div>
  );
}
