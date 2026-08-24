"""诊断: 医药生物 5年区间位置为何在后复权口径下偏高 (vs 申万医药 4000->2000)?
构建 5 种口径的行业指数对比:
  A 等权·后复权   (当前 industry_ma_trend.py 的口径)
  B 等权·前复权   (验证: 前/后复权是同一因子的不同缩放, 收益率应与 A 完全一致)
  C 等权·原始价   (不含分红送股)
  D 市值加权·后复权 (含分红送股, 对齐申万加权方式)
  E 市值加权·原始价  (最接近申万价格指数的行为)
另统计: 成分股 2021-06 -> 现在 后复权总收益的截面分布 (中位数/均值/上涨家数)。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

QUANT = Path("/Users/kun/Documents/market-radar/quant")
sys.path.insert(0, str(QUANT))
from snapshot import iter_stock_frames  # noqa: E402
from industry_ma_trend import cal_fuquan_price, COL_DATE, COL_CLOSE, COL_PREV, COL_MC, COL_IND  # noqa: E402

IND = "医药生物"
TAIL = 2100          # ~8.5年: 覆盖 2021-06 大顶 + 1320日窗口
HL5Y = 1320
REF = pd.Timestamp("2021-06-01")   # 医药上一轮大顶参照日

frames = []
fq_maxdiff = 0.0          # 前/后复权收益率最大差异 (理论应为 0)
stock_tot_ret = []        # 2021-06 -> 现在 后复权总收益
n_stocks = 0
n_new_after_ref = 0

for code, df in iter_stock_frames([COL_DATE, COL_CLOSE, COL_PREV, COL_MC, COL_IND], tail_rows=TAIL):
    if code.lower().startswith("bj") or df is None or df.empty:
        continue
    df = df.dropna(subset=[COL_IND])
    if df.empty or str(df[COL_IND].iloc[-1]) != IND:
        continue
    n_stocks += 1
    df = df.sort_values(COL_DATE)
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    close = df[COL_CLOSE].astype(float)
    prev = df[COL_PREV].astype(float)
    mc = df[COL_MC].astype(float)

    fq_h = cal_fuquan_price(close, prev, "后复权")
    fq_q = cal_fuquan_price(close, prev, "前复权")
    ret_h = fq_h.pct_change()
    ret_q = fq_q.pct_change()
    fq_maxdiff = max(fq_maxdiff, float((ret_h - ret_q).abs().max()))
    ret_raw = close.pct_change()

    # 2021-06 -> 现在 的后复权总收益 (个股截面)
    after = (df[COL_DATE] >= REF).values
    if after.any():
        base = fq_h[after].iloc[0]
        if base == fq_h.iloc[-1] and after.sum() == 1:
            n_new_after_ref += 1     # 顶点之后才上市, 无完整区间
        else:
            stock_tot_ret.append(fq_h.iloc[-1] / base - 1)
    else:
        n_new_after_ref += 1

    frames.append(pd.DataFrame({
        COL_DATE: df[COL_DATE].values,
        "ret_h": ret_h.values,
        "ret_raw": ret_raw.values,
        "mc": mc.values,
    }))

all_df = pd.concat(frames, ignore_index=True).dropna(subset=["ret_h", "ret_raw"])

def eq_index(col):
    r = all_df.groupby(COL_DATE)[col].mean()
    return (1 + r).cumprod() * 100

def cap_index(col):
    def wavg(d):
        return np.average(d[col], weights=d["mc"])
    r = all_df.groupby(COL_DATE).apply(wavg, include_groups=False)
    return (1 + r).cumprod() * 100

idxA = eq_index("ret_h")
idxB = (1 + all_df.groupby(COL_DATE)["ret_h"].mean()).cumprod() * 100  # 前复权收益率==后复权收益率
idxC = eq_index("ret_raw")
idxD = cap_index("ret_h")
idxE = cap_index("ret_raw")

def report(name, idx):
    idx = idx.dropna()
    last_d = idx.index[-1]
    win = idx.tail(HL5Y)
    hi, lo = win.max(), win.min()
    pos = (idx.iloc[-1] - lo) / (hi - lo)
    hi_date = win.idxmax()
    lo_date = win.idxmin()
    v_ref = idx.asof(REF) if idx.index[0] <= REF else np.nan
    chg = idx.iloc[-1] / v_ref - 1 if pd.notna(v_ref) else np.nan
    print(f"{name:<22} 现值={idx.iloc[-1]:8.1f}  2021-06={v_ref if pd.isna(v_ref) else round(v_ref,1):>7}  "
          f"区间变动={chg*100 if pd.notna(chg) else float('nan'):6.1f}%  "
          f"5y高={hi:7.1f}({hi_date.date()}) 5y低={lo:7.1f}({lo_date.date()})  位置={pos*100:5.1f}%")

print(f"成分股数={n_stocks}  (其中 2021-06 后上市、无完整区间 {n_new_after_ref} 只)")
print(f"前复权 vs 后复权 收益率最大差异 = {fq_maxdiff:.2e}  (应为0: 同一因子不同缩放)\n")
for nm, ix in [("A 等权·后复权", idxA), ("B 等权·前复权", idxB), ("C 等权·原始价", idxC),
               ("D 市值加权·后复权", idxD), ("E 市值加权·原始价", idxE)]:
    report(nm, ix)

sr = pd.Series(stock_tot_ret)
print(f"\n个股后复权总收益 2021-06->现在 (n={len(sr)}): 中位数={sr.median()*100:.1f}%  "
      f"均值={sr.mean()*100:.1f}%  上涨家数占比={(sr>0).mean()*100:.0f}%  "
      f"P25={sr.quantile(.25)*100:.1f}%  P75={sr.quantile(.75)*100:.1f}%")

# ---- 与真实申万医药指数(801150, westockdata pt01801150)合并输出 ----
sw = pd.read_csv("/tmp/sw_pharma_kline.txt", sep="|", skiprows=2, header=None,
                 usecols=[1, 3], names=["date", "sw"], skipinitialspace=True)
sw["date"] = pd.to_datetime(sw["date"].str.strip())
sw["sw"] = sw["sw"].astype(float)
sw = sw.set_index("date").sort_index()

merged = pd.DataFrame({
    "等权_后复权": idxA, "等权_原始价": idxC,
    "市值加权_后复权": idxD, "市值加权_原始价": idxE,
}).join(sw, how="outer")
# 统一以 2021-06-01 (或各自首个>=该日) 为基期=100
base_date = pd.Timestamp("2021-06-01")
out = {}
for c in merged.columns:
    s = merged[c].dropna()
    base = s[s.index >= base_date]
    base = base.iloc[0] if len(base) else s.iloc[0]
    out[c] = s / base * 100
pd.DataFrame(out).to_csv("/tmp/diag_pharma_pos5y_series.csv")
print("\n序列已保存 /tmp/diag_pharma_pos5y_series.csv (基期 2021-06=100)")
# 真实申万的多周期位置
for w, name in [(1320, "申万真实·1320日"), (750, "申万真实·750日"), (250, "申万真实·250日")]:
    d = sw.tail(w)
    hi, lo = d["sw"].max(), d["sw"].min()
    pos = (sw["sw"].iloc[-1] - lo) / (hi - lo)
    print(f"{name:<14} 高={hi:9.1f}({d['sw'].idxmax().date()})  低={lo:9.1f}({d['sw'].idxmin().date()})  位置={pos*100:5.1f}%")
print(f"申万真实 2021-08顶={sw[sw.index<='2021-09-01']['sw'].max():.1f} -> 现={sw['sw'].iloc[-1]:.1f} "
      f"({sw['sw'].iloc[-1]/sw[sw.index<='2021-09-01']['sw'].max()-1:+.1%})")
