"""回填行业趋势历史截面: 最近 N 个交易日, 从旧到新逐日跑(industry_ma_trend.py 子进程),
保证每天的存档都能与前一交易日对比生成变化清单。"""
import sys
sys.path.insert(0, "/Users/kun/Documents/market-radar")

from quant.industry_trend_daily import run_industry_trend, _sw_dates

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
dates = [d for d in _sw_dates() if d <= "2026-08-20"][-N:]
print(f"回填 {len(dates)} 个交易日: {dates[0]} ~ {dates[-1]}", flush=True)
for i, d in enumerate(dates, 1):
    try:
        p = run_industry_trend(d, force_recompute=True)
        n_chg = len(p["changes"]) if p else -1
        print(f"[{i}/{len(dates)}] {d} OK 变化{n_chg}条 (上期={p['prev_date'] if p else '?'})", flush=True)
    except Exception as e:
        print(f"[{i}/{len(dates)}] {d} FAIL: {str(e)[:200]}", flush=True)
print("回填完毕", flush=True)
