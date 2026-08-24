import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { apiFetch } from '../lib/api';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, TooltipComponent,
  DataZoomComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

// ─── 类型 (/api/industry-trend/kline) ─────────────────────────────────────

interface KlineData {
  industry: string;
  end_date: string;
  dates: string[];
  kline: [number, number, number, number][];  // [open, close, low, high]
  amount: number[];
  ma: Record<string, (number | null)[]>;
  pos: Record<string, number | null>;          // y1/y3/y5 → 0~100
  levels: Record<string, { hi: number; hi_date: string; lo: number; lo_date: string; dd: number; rb: number }>;
}

const UP = '#d8392b';    // A股惯例: 涨红
const DOWN = '#1a9e5f';  // 跌绿
const MA_COLOR: Record<string, string> = {
  ma5: '#CE9B54', ma20: '#534AB7', ma60: '#0F6E56', ma120: '#888780',
};
const MA_LABEL: Record<string, string> = {
  ma5: 'MA5', ma20: 'MA20', ma60: 'MA60', ma120: 'MA120',
};
const POS_META: { key: string; label: string }[] = [
  { key: 'y1', label: '1年位置' }, { key: 'y3', label: '3年位置' }, { key: 'y5', label: '5年位置' },
];

const posColor = (v: number) => (v >= 70 ? UP : v >= 30 ? '#CE9B54' : DOWN);

// ─── 组件 ─────────────────────────────────────────────────────────────────

/** 行业申万官方指数 K 线图: 均线 + 1/3/5年位置分位条 + 周期高低点标注。
 *  date 传入存档日时 K 线截断到该日 (与当日截面位置%口径一致, 不偷看未来)。 */
export function IndustryKline({ industry, date, onClose }: {
  industry: string; date?: string; onClose?: () => void;
}) {
  const [data, setData] = useState<KlineData | null>(null);
  const [error, setError] = useState('');
  const [posTab, setPosTab] = useState<'y1' | 'y3' | 'y5'>('y1');
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    setData(null); setError('');
    const qs = `industry=${encodeURIComponent(industry)}&days=1600${date ? `&date=${date}` : ''}`;
    apiFetch<KlineData | null>(`/api/industry-trend/kline?${qs}`)
      .then(d => {
        if (!d) setError('无该行业指数数据');
        else setData(d);
      })
      .catch(e => setError(e.message || '加载失败'));
  }, [industry, date]);

  // 图表实例生命周期
  useEffect(() => {
    if (!chartRef.current) return;
    chartInst.current = echarts.init(chartRef.current);
    const onResize = () => chartInst.current?.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chartInst.current?.dispose();
      chartInst.current = null;
    };
  }, []);

  // 渲染 (数据或周期 tab 变化时)
  useEffect(() => {
    const chart = chartInst.current;
    if (!chart || !data?.dates?.length) return;

    const kl = data.kline;
    const chg = kl.map((k, i) => i === 0 ? null
      : Number((((k[1] - kl[i - 1][1]) / kl[i - 1][1]) * 100).toFixed(2)));
    const maSeries = Object.keys(MA_COLOR).map(key => ({
      name: MA_LABEL[key], type: 'line' as const, data: data.ma[key] ?? [],
      showSymbol: false, smooth: true, lineStyle: { width: 1, color: MA_COLOR[key] },
      itemStyle: { color: MA_COLOR[key] }, emphasis: { disabled: true },
      z: 3,
    }));
    const lv = data.levels[posTab];
    const markLine = lv ? {
      silent: true, symbol: 'none', animation: false,
      lineStyle: { type: 'dashed' as const, width: 1 },
      label: { position: 'insideEndTop', fontSize: 10, formatter: (p: any) =>
        `${p.name}: ${Number(p.value).toFixed(0)}` },
      data: [
        { name: `${POS_META.find(m => m.key === posTab)?.label}高(${lv.hi_date.slice(2)})`,
          yAxis: lv.hi, lineStyle: { color: UP }, label: { color: UP } },
        { name: `${POS_META.find(m => m.key === posTab)?.label}低(${lv.lo_date.slice(2)})`,
          yAxis: lv.lo, lineStyle: { color: DOWN }, label: { color: DOWN } },
      ],
    } : undefined;

    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#ddd',
        textStyle: { fontSize: 11, color: '#333' },
        formatter: (params: any) => {
          const i = params[0]?.dataIndex ?? 0;
          const k = kl[i];
          if (!k) return '';
          const c = chg[i];
          const maTxt = Object.keys(MA_COLOR)
            .map(key => `${MA_LABEL[key]} ${data.ma[key]?.[i] != null ? Number(data.ma[key]![i]).toFixed(1) : '--'}`)
            .join('  ');
          const amt = data.amount[i];
          return `<b>${data.dates[i]}</b><br/>` +
            `开 ${k[0].toFixed(1)}  高 ${k[3].toFixed(1)}<br/>` +
            `收 <b style="color:${c == null ? '#333' : c >= 0 ? UP : DOWN}">${k[1].toFixed(1)} (${c == null ? '--' : (c >= 0 ? '+' : '') + c + '%'})</b>  低 ${k[2].toFixed(1)}<br/>` +
            `成交额 ${(amt / 1e8).toFixed(1)}亿<br/>` +
            `<span style="color:#888">${maTxt}</span>`;
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 56, right: 16, top: 30, height: '58%' },
        { left: 56, right: 16, top: '76%', height: '16%' },
      ],
      legend: {
        top: 2, textStyle: { fontSize: 10 },
        data: Object.values(MA_LABEL),
      },
      xAxis: [
        { type: 'category', data: data.dates, gridIndex: 0, boundaryGap: true,
          axisLine: { lineStyle: { color: '#ccc' } }, axisLabel: { show: false } },
        { type: 'category', data: data.dates, gridIndex: 1, boundaryGap: true,
          axisLine: { lineStyle: { color: '#ccc' } },
          axisLabel: { fontSize: 9, color: '#888' } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#f0f0ee' } },
          axisLabel: { fontSize: 9, color: '#888' } },
        { scale: true, gridIndex: 1, splitNumber: 2,
          axisLabel: { fontSize: 9, color: '#888', formatter: (v: number) => (v / 1e8).toFixed(0) + '亿' },
          splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 16,
          start: 60, end: 100, textStyle: { fontSize: 9 } },
      ],
      series: [
        { name: 'K线', type: 'candlestick', data: kl, xAxisIndex: 0, yAxisIndex: 0,
          itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
          markLine, z: 2 },
        ...maSeries,
        { name: '成交额', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
          data: data.amount.map((a, i) => ({
            value: a, itemStyle: { color: kl[i][1] >= kl[i][0] ? UP + '55' : DOWN + '55' },
          })), z: 1 },
      ],
    }, { notMerge: true });
  }, [data, posTab]);

  return (
    <div>
      {/* 位置分位条 — 均线在图上, 位置在这里 + 周期高低点线在图上 */}
      <div className="flex flex-wrap items-end gap-4 mb-2">
        {POS_META.map(({ key, label }) => {
          const v = data?.pos?.[key];
          const lv = data?.levels?.[key];
          const active = posTab === key;
          return (
            <button key={key} onClick={() => setPosTab(key as any)}
              className={`text-left rounded-lg px-2.5 py-1.5 border transition-colors ${active ? 'border-blue-400 bg-blue-50' : 'border-gray-200 hover:bg-gray-50'}`}>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500">{label}</span>
                <span className="text-sm font-bold" style={{ color: v == null ? '#999' : posColor(v) }}>
                  {v == null ? '--' : `${v.toFixed(0)}%`}
                </span>
              </div>
              <div className="w-32 h-1.5 bg-gray-200 rounded-full overflow-hidden mt-1">
                <div className="h-full rounded-full transition-all"
                  style={{ width: `${Math.min(100, Math.max(0, v ?? 0))}%`, background: v == null ? '#ccc' : posColor(v) }} />
              </div>
              {lv && (
                <div className="text-[10px] text-gray-400 mt-0.5">
                  距高 {lv.dd}% · 距低 +{lv.rb}%
                </div>
              )}
            </button>
          );
        })}
        {onClose && (
          <button onClick={onClose}
            className="ml-auto text-xs text-gray-400 hover:text-gray-700 border border-gray-200 rounded px-2 py-1">
            收起 ✕
          </button>
        )}
      </div>

      {error && <div className="text-sm text-amber-700 py-6 text-center">{error}</div>}
      {!error && !data && <div className="text-sm text-gray-400 py-6 text-center">K 线加载中…</div>}
      <div ref={chartRef} style={{ width: '100%', height: 420 }} />
      <div className="text-[10px] text-gray-400 mt-1">
        申万官方指数真实点位 · 均线 MA5/20/60/120 · 点位置条切换周期高低点线 · 数据截至 {data?.end_date || '—'}
        {date && date !== data?.end_date ? ` (按存档日 ${date} 截断)` : ''}
      </div>
    </div>
  );
}
