"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：市场分析行业增强分析 — BIAS20热度时序 + 行业抱团检测 + 二级行业热点 + 多周期持续性

参考来源: kun/行业/行业热力.py (BIAS20热力) + kun/行业/抱团股_一级行业.py (抱团检测) + kun/行业/抱团股_覆盖二级行业.py (二级行业)

用法:
    python tools/industry_enhanced_analyzer.py [--date YYYY-MM-DD] [--days N] [--summary-only]
"""

import pandas as pd
import numpy as np
import os
import sys
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
import warnings

from ._paths import require_quant_data_root

warnings.filterwarnings("ignore")

# 热力图可视化（可选）
try:
    import matplotlib
    matplotlib.use("Agg")  # 非交互式后端
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ==================== 配置 ====================
DATA_DIR = require_quant_data_root() + "/stock-trading-data-pro"
LOOKBACK_CALENDAR = 150  # 日历天数，确保覆盖~100个交易日
OUTPUT_DIR = None  # 动态计算

# ==================== CSV 列名（GBK编码后） ====================
COL_CODE = "股票代码"
COL_NAME = "股票名称"
COL_DATE = "交易日期"
COL_CLOSE = "收盘价"
COL_PREV_CLOSE = "前收盘价"
COL_VOLUME = "成交量"
COL_AMOUNT = "成交额"
COL_HIGH = "最高价"
COL_INDUSTRY1 = "新版申万一级行业名称"
COL_INDUSTRY2 = "新版申万二级行业名称"

# ==================== 涨停阈值（按板块） ====================
def get_limit_up_threshold(code: str) -> float:
    """根据股票代码返回涨停阈值。主板10%，创业板/科创板20%，北交所30%。"""
    if code.startswith("bj"):
        return 29.95
    if code.startswith("sz3") or code.startswith("sh688"):
        return 19.95
    return 9.95

# ==================== 抱团检测阈值（多维度加权评分） ====================
# 核心思路：不走单一硬阈值，而是多维度加权评分：
#   广度维度（3选2）：上涨占比≥55% / MA10上方≥55% / MA20上方≥50%
#   强度维度（3选1）：大肉(>5%)占比≥10% / 涨停≥3家 / 大肉≥5家
#   总分≥3 + 大盘条件 = 抱团

# — 广度维度 —
GROUP_UP_RATIO_MIN = 0.55     # 上涨占比最低要求（显著高于市场均值~50%）
GROUP_MA10_ABOVE_MIN = 0.55   # MA10上方占比最低要求
GROUP_MA20_ABOVE_MIN = 0.50   # MA20上方占比最低要求

# — 强度维度 —
BIG_GAINER_PCT = 5.0          # "大肉"涨幅阈值（统一用5%，不区分板块）
GROUP_BIG_GAINER_RATIO = 0.10 # 大肉占比最低要求（10%个股涨超5%）
GROUP_BIG_GAINER_ABS = 5      # 大肉绝对家数（小行业中绝对数更有意义）
GROUP_LIMIT_UP_ABS = 3        # 涨停绝对家数

# — 行业门槛 —
GROUP1_MIN_SIZE = 15          # 一级行业最小样本数
GROUP2_MIN_SIZE = 5           # 二级行业最小样本数
GROUP_LEADER_VOL = 5e8        # 龙头股成交量门槛（5亿股，降低门槛）

# — 大盘条件 —
MARKET_UP_RATIO = 0.55        # 全市场上涨比例（略高于中性50%）
MARKET_LIMIT_UP_MIN = 15      # 全市场涨停家数（适当降低）

# ==================== 工具函数 ====================

def read_csv_safe(file_path: str, n_days: int = 400) -> pd.DataFrame | None:
    """安全读取股票CSV，自动跳过版权行。返回最近 n_days 行。"""
    try:
        file_name = os.path.basename(file_path)
        if file_name.lower().startswith("bj"):
            return None

        # 探测跳过行
        skip_rows = 0
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            first_line = f.readline()
            if "股票代码" not in first_line and "交易日期" not in first_line:
                skip_rows = 1

        df = pd.read_csv(file_path, encoding="gbk", skiprows=skip_rows)

        if df.empty:
            return None
        # 确保列名正确
        if COL_DATE not in df.columns:
            return None
        if COL_CLOSE not in df.columns:
            return None

        # 只保留最近 n_days 行
        if len(df) > n_days:
            df = df.tail(n_days).copy()
        return df
    except Exception:
        return None


def process_stock_file(args_tuple: tuple) -> dict | None:
    """单文件处理（供 ProcessPoolExecutor 调用）。
    返回该股票的 BIAS20 序列 + 每日涨跌幅 + 行业信息。
    """
    file_path, end_date_str = args_tuple
    try:
        df = read_csv_safe(file_path, n_days=LOOKBACK_CALENDAR + 30)
        if df is None or df.empty:
            return None

        # 筛选必需列（含成交额，供热力图右侧标注市场总成交额）
        needed = [COL_DATE, COL_CLOSE, COL_PREV_CLOSE, COL_CODE, COL_NAME,
                   COL_HIGH, COL_VOLUME, COL_INDUSTRY1, COL_AMOUNT]
        available = [c for c in needed if c in df.columns]
        df = df[available].copy()

        if len(df) < 25:
            return None

        # 过滤行业为空的行
        if COL_INDUSTRY1 in df.columns:
            df = df[df[COL_INDUSTRY1].notna()].copy()
            if df.empty:
                return None

        # 仅保留 end_date 之前的数据
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        end_dt = pd.Timestamp(end_date_str)
        df = df[df[COL_DATE] <= end_dt].copy()
        if len(df) < 20:
            return None

        df = df.sort_values(COL_DATE).reset_index(drop=True)

        # 计算 MA20 和 BIAS20
        close_s = df[COL_CLOSE].astype(float)
        df["MA10"] = close_s.rolling(window=10, min_periods=1).mean()
        df["MA20"] = close_s.rolling(window=20, min_periods=1).mean()
        df["BIAS20"] = (close_s - df["MA20"]) / df["MA20"] * 100

        # 涨跌幅
        prev_close_s = df[COL_PREV_CLOSE].astype(float)
        df["涨跌幅"] = (close_s - prev_close_s) / prev_close_s * 100

        # 涨停标记（按板块区分阈值）
        code = str(df[COL_CODE].iloc[0]) if COL_CODE in df.columns else "sh000000"
        limit_threshold = get_limit_up_threshold(code)
        df["是否涨停"] = (df["涨跌幅"] >= limit_threshold).astype(int)

        # 大肉标记（涨幅>5%，不区分板块）
        df["大肉"] = (df["涨跌幅"] > BIG_GAINER_PCT).astype(int)

        # MA10/MA20 上方标记
        df["above_MA10"] = (close_s > df["MA10"]).astype(int)
        df["above_MA20"] = (close_s > df["MA20"]).astype(int)

        # 返回精简数据
        out_cols = [COL_DATE, "BIAS20", "涨跌幅", "是否涨停", "大肉", "above_MA10", "above_MA20"]
        if COL_INDUSTRY1 in df.columns:
            out_cols.append(COL_INDUSTRY1)
        if COL_INDUSTRY2 in df.columns:
            out_cols.append(COL_INDUSTRY2)
        if COL_CODE in df.columns:
            out_cols.append(COL_CODE)
        if COL_NAME in df.columns:
            out_cols.append(COL_NAME)
        if COL_VOLUME in df.columns:
            out_cols.append(COL_VOLUME)
        if COL_AMOUNT in df.columns:
            out_cols.append(COL_AMOUNT)

        result_df = df[out_cols].copy()
        result_df[COL_DATE] = result_df[COL_DATE].dt.strftime("%Y-%m-%d")
        result_df["BIAS20"] = result_df["BIAS20"].astype(float).round(4)
        result_df["涨跌幅"] = result_df["涨跌幅"].astype(float).round(4)

        code_str = str(df[COL_CODE].iloc[0]) if COL_CODE in df.columns else "unknown"
        return {"code": code_str, "data": result_df}
    except Exception:
        return None


def detect_analysis_date(data_dir: str, target_date: str | None) -> str:
    """检测最新交易日或使用指定日期。"""
    if target_date:
        return target_date

    # 抽样几个文件检测最新日期
    files = sorted([f for f in os.listdir(data_dir)
                    if f.endswith(".csv") and not f.startswith("bj")])[:50]
    latest = None
    for fname in files:
        fp = os.path.join(data_dir, fname)
        df = read_csv_safe(fp, n_days=5)
        if df is not None and not df.empty:
            df[COL_DATE] = pd.to_datetime(df[COL_DATE])
            max_d = df[COL_DATE].max()
            if latest is None or max_d > latest:
                latest = max_d
    if latest:
        return latest.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


# ==================== 分析模块 ====================

def generate_industry_heatmap(heatmap_data: pd.DataFrame, analysis_date: str,
                               output_dir: str, top_n: int = 31,
                               daily_amounts: dict | None = None) -> str | None:
    """生成行业BIAS20热力图PNG。参考 kun/行业/行业热力.py。
    top_n 默认 31 = 申万一级全量行业，避免 Bottom（电子/通信等抱团瓦解主角）被截断。
    daily_amounts: {日期: 全市场成交额(亿)}，若有则在热力图右侧加一列标注成交额。
    返回PNG文件路径，若matplotlib不可用则返回None。
    """
    if not HAS_MATPLOTLIB or heatmap_data is None or heatmap_data.empty:
        return None

    try:
        # 设置中文字体
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False

        hd = heatmap_data.copy()
        hd.index = pd.to_datetime(hd.index)
        hd = hd.sort_index()

        # 取最近60个交易日 + 前15个行业（按最新日占比排序）
        hd_plot = hd.iloc[-60:]
        latest = hd_plot.iloc[-1].sort_values(ascending=False)
        top_inds = list(latest.head(top_n).index)
        hd_plot = hd_plot[top_inds]

        # 日期格式化
        hd_plot.index = hd_plot.index.strftime("%m-%d")
        hd_plot = hd_plot.iloc[::-1]  # 最新在上

        # 右侧成交额列：对齐 hd_plot 的日期 index（原始 datetime -> 成交额亿）
        amt_labels = None
        if daily_amounts:
            # hd_plot.index 已转成 "MM-DD" 字符串，反查 daily_amounts
            # daily_amounts 的 key 是 Timestamp，先按 MM-DD 映射
            amt_map = {pd.Timestamp(d).strftime("%m-%d"): v for d, v in daily_amounts.items()}
            amt_labels = [amt_map.get(idx, None) for idx in hd_plot.index]

        fig = plt.figure(figsize=(max(14, top_n * 0.9), 18))
        # gridspec 3 列：主热力图 + 成交额列（紧贴最后行业列）+ colorbar
        import matplotlib.gridspec as gridspec
        gs = gridspec.GridSpec(1, 3, width_ratios=[top_n, 1.2, 0.6], wspace=0.05)
        ax = fig.add_subplot(gs[0, 0])
        ax_amt = fig.add_subplot(gs[0, 1])
        ax_cbar = fig.add_subplot(gs[0, 2])

        sns_heatmap = __import__("seaborn", globals={}, locals={}, fromlist=["heatmap"]).heatmap

        # 自定义 colormap：低→灰色，中→白色，高→红色
        colors_list = ["#555555", "#aaaaaa", "#ffffff", "#ffaaaa", "#ff4444", "#cc0000"]
        cmap = mcolors.LinearSegmentedColormap.from_list("bias_heat", colors_list, N=256)

        sns_heatmap(
            hd_plot * 100,  # 转为百分比
            cmap=cmap,
            annot=True,
            fmt=".0f",
            annot_kws={"size": 7, "weight": "bold"},
            linewidths=0.3,
            center=50,
            vmin=0, vmax=100,
            cbar=False,  # 关掉自带 colorbar，手动画到 ax_cbar（避免占用右侧位置）
            ax=ax,
        )

        # 手动 colorbar（放最右 ax_cbar，让成交额列紧贴主图）
        import matplotlib.colorbar as cbar_mod
        norm = mcolors.Normalize(vmin=0, vmax=100)
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        fig.colorbar(sm, cax=ax_cbar, label="BIAS20>0 占比 (%)")

        ax.xaxis.tick_top()
        ax.xaxis.set_label_position("top")
        plt.setp(ax.get_xticklabels(), rotation=45, fontsize=9, ha="left")
        plt.setp(ax.get_yticklabels(), fontsize=8)
        ax.set_title(f"行业BIAS20热度热力图 — {analysis_date}（近60日，Top {top_n}行业）", fontsize=14, pad=20)
        ax.set_xlabel("行业", fontsize=12)
        ax.set_ylabel("交易日期", fontsize=12)

        # 右侧成交额列：和主热力图同款红深→灰浅配色，数值大=红、小=灰
        n_rows = len(hd_plot)
        ax_amt.set_xlim(0, 1)
        ax_amt.set_ylim(0, n_rows)
        ax_amt.set_title("市场成交额", fontsize=11, pad=20)
        ax_amt.set_xticks([])
        ax_amt.tick_params(axis="y", length=0, labelsize=8)
        # 成交额归一化到 0-100 配色（min=灰, max=红）
        if amt_labels:
            valid = [a for a in amt_labels if a is not None]
            amt_min = min(valid) if valid else 0
            amt_max = max(valid) if valid else 1
            # 用主 cmap 画每行颜色（成交额列单列矩阵）
            import numpy as np
            mat = np.full((n_rows, 1), np.nan)
            for i, amt in enumerate(amt_labels):
                if amt is None:
                    continue
                # 归一化到 0-100
                norm = (amt - amt_min) / (amt_max - amt_min + 1e-9) * 100
                mat[n_rows - 1 - i, 0] = norm
            ax_amt.imshow(mat, cmap=cmap, vmin=0, vmax=100, aspect="auto",
                          extent=(0, 1, 0, n_rows), origin="lower")
            # 标注数值
            for i, amt in enumerate(amt_labels):
                if amt is None:
                    continue
                y = n_rows - 1 - i
                # 文字颜色：深红底用白字、浅底用黑字
                txt_color = "white" if mat[y, 0] > 65 else "black"
                ax_amt.text(0.5, y + 0.5, f"{amt:.0f}",
                            ha="center", va="center", fontsize=8,
                            weight="bold", color=txt_color)
        # 外框
        import matplotlib.patches as patches
        ax_amt.add_patch(patches.Rectangle((0, 0), 1, n_rows, fill=False, edgecolor="#cccccc", linewidth=0.5))

        date_tag = analysis_date.replace("-", "")
        png_path = os.path.join(output_dir, f"industry_heatmap_{date_tag}.png")
        plt.savefig(png_path, bbox_inches="tight", dpi=200)
        plt.close(fig)
        return png_path
    except Exception as e:
        print(f"  [WARN] 热力图生成失败: {e}")
        return None


def compute_bias20_heat(data_dir: str, analysis_date: str, n_jobs: int = 12,
                        verbose: bool = True) -> dict:
    """主入口：加载全量数据，计算所有分析模块的结果。"""
    t0 = datetime.now()
    if verbose:
        print(f"[行业增强分析] 开始加载数据...")

    # 收集所有股票文件
    all_files = sorted([
        os.path.join(data_dir, f) for f in os.listdir(data_dir)
        if f.endswith(".csv") and not f.startswith("bj")
    ])

    if verbose:
        print(f"  股票文件数: {len(all_files)}")
        print(f"  分析截止日期: {analysis_date}")
        print(f"  并行进程数: {n_jobs}")

    # 并行处理
    args_list = [(f, analysis_date) for f in all_files]
    all_results = []
    failed = 0

    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        futures = {executor.submit(process_stock_file, args): args[0] for args in args_list}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result is not None:
                    all_results.append(result)
                else:
                    failed += 1
            except Exception:
                failed += 1

    if verbose:
        t1 = datetime.now()
        print(f"  数据加载完成: {len(all_results)} 只有效股票, {failed} 只跳过")
        print(f"  加载耗时: {(t1 - t0).total_seconds():.1f}秒")

    if not all_results:
        print("[行业增强分析] 错误：没有有效股票数据")
        return {}

    # 合并所有数据
    all_dfs = []
    for r in all_results:
        all_dfs.append(r["data"])
    full_df = pd.concat(all_dfs, ignore_index=True)
    full_df[COL_DATE] = pd.to_datetime(full_df[COL_DATE])
    full_df = full_df.sort_values(COL_DATE)

    if verbose:
        t2 = datetime.now()
        print(f"  合并完成: {len(full_df)} 行, merge耗时: {(t2 - t1).total_seconds():.1f}秒")

    # 确定有效交易日
    unique_dates = sorted(full_df[COL_DATE].unique())
    selected_dates = unique_dates[-100:] if len(unique_dates) >= 100 else unique_dates
    selected_dates = pd.DatetimeIndex(selected_dates)
    filtered_df = full_df[full_df[COL_DATE].isin(selected_dates)].copy()

    if verbose:
        print(f"  交易日范围: {selected_dates[0].strftime('%Y-%m-%d')} ~ {selected_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  共 {len(selected_dates)} 个交易日")

    # ===== Module 1: BIAS20 热度时序 =====
    m1_result = module1_bias20_heat(filtered_df, selected_dates, verbose)

    # ===== Module 2: 一级行业抱团检测 =====
    m2_result = module2_group_behavior(filtered_df, selected_dates, COL_INDUSTRY1, "一级",
                                       GROUP1_MIN_SIZE, verbose)

    # ===== Module 3: 二级行业抱团检测 =====
    m3_result = module2_group_behavior(filtered_df, selected_dates, COL_INDUSTRY2, "二级",
                                       GROUP2_MIN_SIZE, verbose)

    # ===== Module 4: 多周期持续性 =====
    m4_result = module4_persistence(m1_result.get("heatmap_data"), selected_dates, verbose)

    # ===== 最新日行业综合快照（涨停/大肉/MA10/MA20） =====
    latest_date = selected_dates[-1]
    latest_df = filtered_df[filtered_df[COL_DATE] == latest_date]
    daily_snapshot = []
    for ind, grp in latest_df.groupby(COL_INDUSTRY1):
        n = len(grp)
        if n < 3 or pd.isna(ind) or str(ind).strip() == "":
            continue
        daily_snapshot.append({
            "行业": ind,
            "样本数": n,
            "上涨占比": round((grp["涨跌幅"] > 0).sum() / n, 4),
            "涨停家数": int(grp["是否涨停"].sum()),
            "大肉家数": int(grp["大肉"].sum()),
            "MA10上方占比": round(grp["above_MA10"].sum() / n, 4) if "above_MA10" in grp.columns else 0,
            "MA20上方占比": round(grp["above_MA20"].sum() / n, 4) if "above_MA20" in grp.columns else 0,
            "平均涨跌幅": round(grp["涨跌幅"].mean(), 2),
        })
    daily_snapshot_df = pd.DataFrame(daily_snapshot).sort_values("上涨占比", ascending=False)

    if verbose:
        t3 = datetime.now()
        print(f"  分析总耗时: {(t3 - t0).total_seconds():.1f}秒")

    return {
        "analysis_date": analysis_date,
        "selected_dates": selected_dates,
        "m1_bias20_heat": m1_result,
        "m2_group_primary": m2_result,
        "m3_group_secondary": m3_result,
        "m4_persistence": m4_result,
        "daily_snapshot": daily_snapshot_df.to_dict("records") if not daily_snapshot_df.empty else [],
        "heatmap_png": None,  # 由 main() 填充
    }


def module1_bias20_heat(filtered_df: pd.DataFrame, selected_dates: pd.DatetimeIndex,
                         verbose: bool = True) -> dict:
    """M1: 每日每行业 BIAS20>0 占比矩阵。"""
    if verbose:
        print("  [M1] 计算 BIAS20 热度时序...")

    rows = []
    daily_amounts = {}  # 日期 -> 全市场成交额(亿)，供热力图右侧标注
    for date in selected_dates:
        daily = filtered_df[filtered_df[COL_DATE] == date]
        if COL_INDUSTRY1 not in daily.columns:
            continue
        # 当日全市场成交额（亿），用于热力图最后一列标注
        if COL_AMOUNT in daily.columns:
            daily_amounts[date] = float(daily[COL_AMOUNT].sum()) / 1e8
        for ind, grp in daily.groupby(COL_INDUSTRY1):
            total = len(grp)
            if total < 3:
                continue
            positive = (grp["BIAS20"] > 0).sum()
            ratio = positive / total
            mean_bias = grp["BIAS20"].mean()
            rows.append({
                "日期": date,
                "行业": ind,
                "BIAS20占比": round(ratio, 4),
                "平均BIAS20": round(mean_bias, 2),
                "样本数": total,
            })

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        return {"error": "no industry data", "heatmap_data": None, "latest_ranking": []}

    # 构建热力图矩阵
    heatmap_data = result_df.pivot_table(
        index="日期", columns="行业", values="BIAS20占比"
    )
    heatmap_data.index = pd.to_datetime(heatmap_data.index)

    # 最新日排名
    latest_date = selected_dates[-1]
    latest = result_df[result_df["日期"] == latest_date].copy()
    latest = latest.sort_values("BIAS20占比", ascending=False)

    # 计算每个行业的 5日均值和 20日均值
    industry_stats = []
    for ind in heatmap_data.columns:
        series = heatmap_data[ind].dropna()
        if len(series) < 5:
            continue
        cur = series.iloc[-1]
        ma5 = series.iloc[-5:].mean() if len(series) >= 5 else cur
        ma20 = series.iloc[-20:].mean() if len(series) >= 20 else series.mean()
        # 趋势方向
        if len(series) >= 10:
            first_half = series.iloc[-10:-5].mean()
            second_half = series.iloc[-5:].mean()
            if second_half > first_half + 0.03:
                trend = "↑加速"
            elif second_half > first_half + 0.01:
                trend = "↑"
            elif second_half < first_half - 0.03:
                trend = "↓减速"
            elif second_half < first_half - 0.01:
                trend = "↓"
            else:
                trend = "→"
        else:
            trend = "→"
        # 持续性：连续 >50% 天数
        above_half = (series > 0.5).astype(int)
        persistence = 0
        for v in reversed(above_half.values):
            if v == 1:
                persistence += 1
            else:
                break
        industry_stats.append({
            "行业": ind,
            "当日占比": round(cur, 4),
            "5日均值": round(ma5, 4),
            "20日均值": round(ma20, 4),
            "趋势": trend,
            "持续天数": persistence,
        })

    stats_df = pd.DataFrame(industry_stats).sort_values("当日占比", ascending=False)

    # Top 5 rising / declining by 5d change (only meaningful changes, abs > 1pp)
    if len(stats_df) >= 10:
        stats_df["5日变化"] = stats_df["当日占比"] - stats_df["5日均值"]
        top_rising = stats_df[stats_df["5日变化"] > 0.01].nlargest(5, "5日变化")
        top_declining = stats_df[stats_df["5日变化"] < -0.01].nsmallest(5, "5日变化")
    else:
        top_rising = pd.DataFrame()
        top_declining = pd.DataFrame()

    return {
        "heatmap_data": heatmap_data,
        "stats_df": stats_df,
        "latest_ranking": latest.to_dict("records"),
        "top_rising": top_rising.to_dict("records") if not top_rising.empty else [],
        "top_declining": top_declining.to_dict("records") if not top_declining.empty else [],
        "daily_amounts": daily_amounts,
    }


def compute_group_score(up_ratio: float, ma10_ratio: float, ma20_ratio: float,
                         big_gainer_ratio: float, big_gainer_count: int,
                         limit_up_count: int, total_count: int) -> tuple[int, str]:
    """多维度加权评分：广度3维度 + 强度3维度，总分≥3 = 抱团。

    Returns (score, reason) where reason explains which dimensions passed.
    """
    score = 0
    reasons = []

    # — 广度维度（3选2即2分，3选3即3分）—
    if up_ratio >= GROUP_UP_RATIO_MIN:
        score += 1
        reasons.append(f"上涨{up_ratio*100:.0f}%")
    if ma10_ratio >= GROUP_MA10_ABOVE_MIN:
        score += 1
        reasons.append(f"MA10{ma10_ratio*100:.0f}%")
    if ma20_ratio >= GROUP_MA20_ABOVE_MIN:
        score += 1
        reasons.append(f"MA20{ma20_ratio*100:.0f}%")

    breadth_score = score  # 记录广度得分

    # — 强度维度（至少1个）—
    intensity_score = 0
    if big_gainer_ratio >= GROUP_BIG_GAINER_RATIO:
        intensity_score += 1
        reasons.append(f"大肉占比{big_gainer_ratio*100:.0f}%")
    if big_gainer_count >= GROUP_BIG_GAINER_ABS:
        if intensity_score == 0:  # 避免重复计数
            intensity_score += 1
            reasons.append(f"大肉{big_gainer_count}家")
    if limit_up_count >= GROUP_LIMIT_UP_ABS:
        intensity_score += 1
        reasons.append(f"涨停{limit_up_count}家")

    total_score = breadth_score + min(intensity_score, 1)  # 强度维度最多贡献1分

    # 得分>=3 且 广度>=2 才算抱团（防止纯强度驱动）
    is_group = total_score >= 3 and breadth_score >= 2

    reason_str = "; ".join(reasons) if reasons else "无"
    return total_score, reason_str, is_group


def module2_group_behavior(filtered_df: pd.DataFrame, selected_dates: pd.DatetimeIndex,
                            industry_col: str, level_label: str,
                            min_size: int,
                            verbose: bool = True) -> dict:
    """M2/M3: 行业抱团检测（多维度加权评分法）。

    评分体系：
      广度（0-3分）：上涨占比≥55% + MA10上方≥55% + MA20上方≥50%
      强度（0-1分）：大肉占比≥10% OR 大肉≥5家 OR 涨停≥3家
      总分≥3 且 广度≥2 = 抱团
    外加：大盘条件（全市场上涨>55% + 涨停≥15家）
    """
    if verbose:
        print(f"  [M2/M3] 计算{level_label}行业抱团检测（多维度评分法）...")

    if industry_col not in filtered_df.columns:
        return {"error": f"no {industry_col} column", "period_results": [], "current_period": {}}

    # 每日抱团检测
    daily_records = []
    for date in selected_dates:
        daily = filtered_df[filtered_df[COL_DATE] == date].copy()
        total_stocks = len(daily)
        if total_stocks < 50:
            continue

        market_up_ratio = (daily["涨跌幅"] > 0).sum() / total_stocks
        total_limit_up = daily["是否涨停"].sum()
        market_condition = market_up_ratio > MARKET_UP_RATIO and total_limit_up >= MARKET_LIMIT_UP_MIN

        for ind, grp in daily.groupby(industry_col):
            total_count = len(grp)
            if pd.isna(ind) or str(ind).strip() == "":
                continue
            if total_count < min_size:
                continue

            # 基础统计
            up_count = (grp["涨跌幅"] > 0).sum()
            up_ratio = up_count / total_count
            limit_up_count = int(grp["是否涨停"].sum())
            big_gainer_count = int(grp["大肉"].sum())
            big_gainer_ratio = big_gainer_count / total_count
            ma10_above = grp["above_MA10"].sum() / total_count if "above_MA10" in grp.columns else 0
            ma20_above = grp["above_MA20"].sum() / total_count if "above_MA20" in grp.columns else 0

            # 成交量龙头
            has_leader = (grp[COL_VOLUME].astype(float) > GROUP_LEADER_VOL).any() if COL_VOLUME in grp.columns else True
            has_leader_or_large = has_leader or total_count >= min_size * 2

            # 多维度评分
            score, reason, industry_ok = compute_group_score(
                up_ratio, ma10_above, ma20_above,
                big_gainer_ratio, big_gainer_count,
                limit_up_count, total_count,
            )

            is_group = market_condition and industry_ok and has_leader_or_large

            daily_records.append({
                "日期": date,
                "行业": ind,
                "上涨比例": round(up_ratio, 4),
                "MA10上方": round(ma10_above, 4),
                "MA20上方": round(ma20_above, 4),
                "大肉家数": big_gainer_count,
                "大肉占比": round(big_gainer_ratio, 4),
                "涨停家数": limit_up_count,
                "样本数": total_count,
                "评分": score,
                "评分理由": reason,
                "大盘条件": market_condition,
                "行业条件": industry_ok,
                "抱团行情": is_group,
            })

    if not daily_records:
        return {"error": "no data after daily grouping", "period_results": [], "current_period": {}}

    daily_df = pd.DataFrame(daily_records)
    daily_df["日期"] = pd.to_datetime(daily_df["日期"])

    # 5日周期聚合
    dates_sorted = sorted(daily_df["日期"].unique())
    num_periods = max(1, len(dates_sorted) // 5)
    period_results = []
    all_periods = []

    for p_idx in range(num_periods):
        start_idx = p_idx * 5
        end_idx = min((p_idx + 1) * 5, len(dates_sorted))
        period_dates = dates_sorted[start_idx:end_idx]
        period_df = daily_df[daily_df["日期"].isin(period_dates)]

        if period_df.empty:
            continue

        # 每个行业在该周期的抱团天数
        summary = period_df.groupby("行业")["抱团行情"].sum()
        qualified = summary[summary >= 2] if level_label == "一级" else summary[summary >= 1]

        p_start = pd.Timestamp(period_dates[0]).strftime("%Y-%m-%d")
        p_end = pd.Timestamp(period_dates[-1]).strftime("%Y-%m-%d")

        for ind in qualified.index:
            ind_data = period_df[period_df["行业"] == ind]
            avg_up = ind_data["上涨比例"].mean() if "上涨比例" in ind_data.columns else 0
            avg_ma10 = ind_data["MA10上方"].mean() if "MA10上方" in ind_data.columns else 0
            avg_ma20 = ind_data["MA20上方"].mean() if "MA20上方" in ind_data.columns else 0
            big_gainer_total = int(ind_data["大肉家数"].sum())
            limit_total = int(ind_data["涨停家数"].sum())
            avg_score = round(float(ind_data["评分"].mean()), 1)
            period_results.append({
                "周期开始": p_start,
                "周期结束": p_end,
                "行业": ind,
                "抱团天数": int(qualified[ind]),
                "周期天数": len(period_dates),
                "平均上涨比例": round(float(avg_up), 4),
                "平均MA10上方": round(float(avg_ma10), 4),
                "平均MA20上方": round(float(avg_ma20), 4),
                "大肉总数": big_gainer_total,
                "涨停总数": limit_total,
                "平均评分": avg_score,
            })

        all_periods.append({
            "周期开始": p_start,
            "周期结束": p_end,
            "抱团行业": list(qualified.index),
            "行业数": len(qualified),
        })

    if not period_results:
        return {"error": "no period results", "period_results": [], "current_period": {}}

    period_df = pd.DataFrame(period_results)

    # 当前周期（最后一个）
    current_period = all_periods[-1] if all_periods else {}
    current_industries = period_df[period_df["周期开始"] == current_period.get("周期开始", "")]

    # 3级标记
    # 构建周期-行业矩阵
    if len(all_periods) >= 5:
        pivot = period_df.pivot_table(
            index="周期开始", columns="行业", values="抱团天数", fill_value=0
        )
        pivot.index = pd.to_datetime(pivot.index)
        pivot = pivot.sort_index()

        # 连续优质抱团：5周期窗口内 >= 3次有抱团
        continuous_strength = {}
        for ind in pivot.columns:
            series = pivot[ind]
            max_streak = 0
            for i in range(len(series) - 4):
                window = series.iloc[i:i + 5]
                score = (window > 0).sum()
                if score > max_streak:
                    max_streak = score
            continuous_strength[ind] = max_streak

        # 标记级别
        current_p_start = current_period.get("周期开始", "")
        tier_labels = {}
        for ind in current_industries["行业"]:
            strength = continuous_strength.get(ind, 0)
            if strength >= 3:
                tier_labels[ind] = "🔴 连续优质"
            elif strength >= 2:
                tier_labels[ind] = "🟠 持续抱团"
            else:
                tier_labels[ind] = "🟡 普通抱团"

        current_industries = current_industries.copy()
        current_industries["级别"] = current_industries["行业"].map(tier_labels)
    else:
        current_industries = current_industries.copy()
        current_industries["级别"] = "🟡 普通抱团"

    # 历史抱团频率
    freq = period_df.groupby("行业").size() / num_periods
    freq = freq.sort_values(ascending=False)

    return {
        "period_results": period_results,
        "all_periods": all_periods,
        "current_period": current_period,
        "current_industries": current_industries.to_dict("records") if not current_industries.empty else [],
        "frequency": freq.head(10).to_dict(),
        "total_periods": num_periods,
    }


def module4_persistence(heatmap_data, selected_dates, verbose: bool = True) -> dict:
    """M4: 多周期排名持续性分析。"""
    if verbose:
        print("  [M4] 计算多周期持续性...")

    if heatmap_data is None or heatmap_data.empty:
        return {"error": "no heatmap data", "correlations": {}, "divergents": []}

    hd = heatmap_data.copy()
    hd = hd.sort_index()

    n = len(hd)
    if n < 20:
        return {"error": f"only {n} days, need >=20", "correlations": {}, "divergents": []}

    # 排名相关性
    def rank_corr(a, b):
        common = list(set(a.index) & set(b.index))
        if len(common) < 10:
            return None
        return a[common].corr(b[common], method="spearman")

    correlations = {}
    windows = [3, 5, 10, 20]
    for w in windows:
        if n > w:
            recent = hd.iloc[-1]
            earlier = hd.iloc[-(w + 1)]
            corr_val = rank_corr(recent, earlier)
            correlations[f"{w}日"] = round(corr_val, 4) if corr_val is not None else None

    # Top 10 留存率
    top10_retention = {}
    for w in windows:
        if n > w:
            recent_top10 = set(hd.iloc[-1].nlargest(10).index)
            earlier_top10 = set(hd.iloc[-(w + 1)].nlargest(10).index)
            retention = len(recent_top10 & earlier_top10) / 10
            top10_retention[f"{w}日"] = round(retention, 2)

    # 排名大幅变化
    divergents = []
    if n >= 5:
        rank_recent = hd.iloc[-1].rank(ascending=False)
        rank_5d_ago = hd.iloc[-6].rank(ascending=False) if n >= 6 else rank_recent
        rank_3d_ago = hd.iloc[-4].rank(ascending=False) if n >= 4 else rank_recent

        common_inds = list(set(rank_recent.index) & set(rank_5d_ago.index))
        for ind in common_inds:
            change_5d = rank_5d_ago[ind] - rank_recent[ind]
            if abs(change_5d) >= 5:
                divergents.append({
                    "行业": ind,
                    "当前排名": int(rank_recent[ind]),
                    "5日前排名": int(rank_5d_ago[ind]),
                    "变化": int(change_5d),
                    "方向": "↑跃升" if change_5d > 0 else "↓下滑",
                })

    divergents.sort(key=lambda x: abs(x["变化"]), reverse=True)

    return {
        "correlations": correlations,
        "top10_retention": top10_retention,
        "divergents": divergents[:15],
    }


# ==================== Markdown 输出 ====================

def generate_markdown(results: dict) -> str:
    """生成可嵌入报告的 Markdown。"""
    m1 = results.get("m1_bias20_heat", {})
    m2 = results.get("m2_group_primary", {})
    m3 = results.get("m3_group_secondary", {})
    m4 = results.get("m4_persistence", {})
    analysis_date = results.get("analysis_date", "")
    selected_dates = results.get("selected_dates", None)

    lines = []
    lines.append("<!-- [AI-GENERATED] 行业增强分析 — 由 tools/industry_enhanced_analyzer.py 自动生成 -->")
    lines.append("")

    # ===== 今日行业综合快照 Top 10 =====
    lines.append("### 今日行业综合快照 Top 10")
    lines.append("")
    daily_snap = results.get("daily_snapshot", [])
    if daily_snap:
        lines.append("| 排名 | 行业 | 上涨占比 | 涨停 | 大肉(>5%) | MA10上方 | MA20上方 | 平均涨跌 |")
        lines.append("|------|------|---------|------|-----------|----------|----------|---------|")
        for rank, item in enumerate(daily_snap[:10], 1):
            lines.append(
                f"| {rank} | {item['行业']} | {item['上涨占比']*100:.0f}% | "
                f"{item['涨停家数']}家 | {item['大肉家数']}家 | "
                f"{item['MA10上方占比']*100:.0f}% | {item['MA20上方占比']*100:.0f}% | "
                f"{item['平均涨跌幅']:+.1f}% |"
            )
        lines.append("")
    else:
        lines.append("> 无快照数据")
        lines.append("")

    # ===== 31行业BIAS20热度排名 =====
    lines.append("### 31行业BIAS20热度排名（中期趋势）")
    lines.append("")
    stats = m1.get("stats_df")
    if stats is not None and not stats.empty:
        lines.append("| 排名 | 行业 | BIAS20>0占比 | 5日均值 | 20日均值 | 趋势 | 持续>50%天数 |")
        lines.append("|------|------|-------------|---------|----------|------|-------------|")
        for rank, (_, row) in enumerate(stats.iterrows(), 1):
            pct = f"{row['当日占比']*100:.1f}%"
            ma5 = f"{row['5日均值']*100:.1f}%"
            ma20 = f"{row['20日均值']*100:.1f}%"
            trend = row['趋势']
            pers = row['持续天数']
            lines.append(f"| {rank} | {row['行业']} | {pct} | {ma5} | {ma20} | {trend} | {pers}天 |")
        lines.append("")
        lines.append(f"**热度加速上升**: " + ", ".join(
            [f"{r['行业']}(变化{r.get('5日变化', 0)*100:+.1f}pp)" for r in m1.get("top_rising", [])]
        ) if m1.get("top_rising") else "无")
        lines.append("")
        lines.append(f"**热度加速下滑**: " + ", ".join(
            [f"{r['行业']}(变化{r.get('5日变化', 0)*100:+.1f}pp)" for r in m1.get("top_declining", [])]
        ) if m1.get("top_declining") else "无")
        lines.append("")
    else:
        lines.append("> 无 BIAS20 热度数据")
        lines.append("")

    # ===== 行业BIAS20热度趋势（近10日） =====
    lines.append("### BIAS20热度趋势（近10日）")
    lines.append("")
    heatmap = m1.get("heatmap_data")
    if heatmap is not None and not heatmap.empty and len(heatmap) >= 10:
        recent10 = heatmap.iloc[-10:]
        # 按最新日排名取 Top 8 和 Bottom 8
        latest = recent10.iloc[-1].sort_values(ascending=False)
        top8 = list(latest.head(8).index)
        bot8 = list(latest.tail(8).index)
        show_inds = top8 + bot8

        # 构建表格
        dates_fmt = [d.strftime("%m-%d") for d in recent10.index]
        header = "| 行业 | " + " | ".join(dates_fmt) + " | 方向 |"
        lines.append(header)
        lines.append("|------|" + "|".join(["------"] * (len(dates_fmt) + 1)) + "|")

        for ind in show_inds:
            if ind not in recent10.columns:
                continue
            series = recent10[ind]
            vals = [f"{v*100:.0f}%" if not pd.isna(v) else "-" for v in series.values]
            # 方向判断
            first_val = series.iloc[0]
            last_val = series.iloc[-1]
            if not pd.isna(first_val) and not pd.isna(last_val):
                if last_val > first_val * 1.05:
                    direction = "↑"
                elif last_val < first_val * 0.95:
                    direction = "↓"
                else:
                    direction = "→"
            else:
                direction = "?"
            row = f"| {ind} | " + " | ".join(vals) + f" | {direction} |"
            lines.append(row)
        lines.append("")
        lines.append(f"*Top 8 + Bottom 8 行业 BIAS20>0 占比近10日演变*")
        lines.append("")
    else:
        lines.append("> 数据不足，跳过")
        lines.append("")

    # ===== 行业抱团检测 =====
    lines.append("### 行业抱团检测（5日周期，多维度评分法）")
    lines.append("")
    lines.append("> 评分体系：广度(上涨占比+MA10上方+MA20上方，0-3分) + 强度(大肉占比/家数+涨停，0-1分)")
    lines.append("> 抱团条件：总分≥3 且 广度≥2 且 大盘条件满足")
    lines.append("")
    current_ind = m2.get("current_industries", [])
    if current_ind:
        lines.append("| 行业 | 级别 | 抱团 | MA10上方 | MA20上方 | 大肉 | 涨停 | 评分 |")
        lines.append("|------|------|------|----------|----------|------|------|------|")
        for item in current_ind:
            lines.append(
                f"| {item['行业']} | {item.get('级别', '-')} | "
                f"{item['抱团天数']}/{item.get('周期天数', 5)} | "
                f"{item.get('平均MA10上方', 0)*100:.0f}% | "
                f"{item.get('平均MA20上方', 0)*100:.0f}% | "
                f"{item.get('大肉总数', 0)}家 | "
                f"{item.get('涨停总数', 0)}家 | "
                f"{item.get('平均评分', 0):.1f} |"
            )
        lines.append("")

        cp = m2.get("current_period", {})
        if cp:
            lines.append(f"**当前周期**: {cp.get('周期开始', '?')} ~ {cp.get('周期结束', '?')}")
            lines.append(f"**抱团行业数**: {len(current_ind)} / 31")
            lines.append("")
    else:
        lines.append("> 当前周期无行业满足抱团条件")
        lines.append("")

    freq = m2.get("frequency", {})
    if freq:
        lines.append("**历史抱团频率 Top 5**: " + ", ".join(
            [f"{ind}({freq_val*100:.0f}%)" for ind, freq_val in list(freq.items())[:5]]
        ))
        lines.append("")

    # ===== 二级行业热点 =====
    lines.append("### 二级行业热点（近5日）")
    lines.append("")
    current_ind2 = m3.get("current_industries", [])
    if current_ind2:
        lines.append("| 行业 | 级别 | 抱团 | MA10上方 | MA20上方 | 大肉 | 涨停 | 评分 |")
        lines.append("|------|------|------|----------|----------|------|------|------|")
        for item in current_ind2[:15]:  # Top 15 sub-industries
            lines.append(
                f"| {item['行业']} | {item.get('级别', '-')} | "
                f"{item['抱团天数']}/{item.get('周期天数', 5)} | "
                f"{item.get('平均MA10上方', 0)*100:.0f}% | "
                f"{item.get('平均MA20上方', 0)*100:.0f}% | "
                f"{item.get('大肉总数', 0)}家 | "
                f"{item.get('涨停总数', 0)}家 | "
                f"{item.get('平均评分', 0):.1f} |"
            )
        lines.append("")

        cp2 = m3.get("current_period", {})
        if cp2:
            lines.append(f"**当前周期**: {cp2.get('周期开始', '?')} ~ {cp2.get('周期结束', '?')}")
            lines.append(f"**二级行业热点数**: {len(current_ind2)}")
            lines.append("")
    else:
        lines.append("> 当前周期无二级行业满足抱团条件")
        lines.append("")

    # ===== 多周期持续性 =====
    lines.append("### 多周期持续性分析")
    lines.append("")

    correlations = m4.get("correlations", {})
    retention = m4.get("top10_retention", {})
    if correlations:
        lines.append("| 窗口 | 排名相关性(Spearman) | Top10留存率 | 说明 |")
        lines.append("|------|---------------------|------------|------|")
        for w in ["3日", "5日", "10日", "20日"]:
            corr = correlations.get(w, "-")
            ret = retention.get(w, "-")
            if corr is not None and corr != "-":
                corr_s = f"{corr:.3f}"
                if abs(corr) >= 0.7:
                    desc = "高持续性"
                elif abs(corr) >= 0.5:
                    desc = "中等轮动"
                else:
                    desc = "快速轮动"
            else:
                corr_s = "-"
                desc = "-"
            ret_s = f"{ret*100:.0f}%" if isinstance(ret, (int, float)) else "-"
            lines.append(f"| {w} | {corr_s} | {ret_s} | {desc} |")
        lines.append("")

    divergents = m4.get("divergents", [])
    if divergents:
        lines.append("**排名大幅变化行业**（5日内变化≥5位）：")
        lines.append("")
        rising = [d for d in divergents if d["方向"] == "↑跃升"]
        falling = [d for d in divergents if d["方向"] == "↓下滑"]
        if rising:
            lines.append(f"- ↑ 跃升: " + ", ".join(
                [f"{d['行业']}(+{d['变化']}位)" for d in rising[:8]]
            ))
        if falling:
            lines.append(f"- ↓ 下滑: " + ", ".join(
                [f"{d['行业']}({d['变化']}位)" for d in falling[:8]]
            ))
        lines.append("")

    return "\n".join(lines)


# ==================== 主入口 ====================

def main():
    global OUTPUT_DIR
    parser = argparse.ArgumentParser(description="行业增强分析 — BIAS20热力+抱团检测+二级热点+持续性")
    parser.add_argument("--date", type=str, default=None,
                        help="分析截止日期 YYYY-MM-DD（默认自动检测最新交易日）")
    parser.add_argument("--days", type=int, default=100,
                        help="分析交易日数（默认100）")
    parser.add_argument("--summary-only", action="store_true",
                        help="仅输出Markdown摘要（静默模式）")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="输出目录（默认 kun/data/）")
    parser.add_argument("--n-jobs", type=int, default=12,
                        help="并行进程数（默认12）")
    parser.add_argument("--png", action="store_true",
                        help="生成BIAS20热度热力图PNG（需要matplotlib+seaborn）")
    args = parser.parse_args()

    # 确定输出目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if args.output_dir:
        OUTPUT_DIR = args.output_dir
    else:
        OUTPUT_DIR = os.path.join(project_root, "kun", "data")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    verbose = not args.summary_only

    if verbose:
        print(f"=" * 60)
        print(f"  行业增强分析 — Industry Enhanced Analyzer")
        print(f"  数据目录: {DATA_DIR}")
        print(f"  输出目录: {OUTPUT_DIR}")
        print(f"=" * 60)

    # 确定分析日期
    analysis_date = detect_analysis_date(DATA_DIR, args.date)
    if verbose:
        print(f"  分析日期: {analysis_date}")

    # 运行全量分析
    results = compute_bias20_heat(
        DATA_DIR, analysis_date,
        n_jobs=args.n_jobs,
        verbose=verbose,
    )

    if not results:
        print("[ERROR] 分析失败，无有效数据")
        sys.exit(1)

    # 生成 Markdown
    md_content = generate_markdown(results)

    # 保存文件
    date_tag = analysis_date.replace("-", "")
    output_file = os.path.join(OUTPUT_DIR, f"industry_enhanced_{date_tag}.md")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 热力图生成
    png_path = None
    if args.png:
        m1 = results.get("m1_bias20_heat", {})
        heatmap_data = m1.get("heatmap_data")
        daily_amounts = m1.get("daily_amounts")
        png_path = generate_industry_heatmap(heatmap_data, analysis_date, OUTPUT_DIR, daily_amounts=daily_amounts)
        if png_path:
            results["heatmap_png"] = png_path
    else:
        # 总是尝试生成（如果matplotlib可用），便于查看
        m1 = results.get("m1_bias20_heat", {})
        heatmap_data = m1.get("heatmap_data")
        daily_amounts = m1.get("daily_amounts")
        png_path = generate_industry_heatmap(heatmap_data, analysis_date, OUTPUT_DIR, daily_amounts=daily_amounts)
        if png_path:
            results["heatmap_png"] = png_path

    if verbose:
        print(f"  输出已保存至: {output_file}")
        if png_path:
            print(f"  热力图已保存至: {png_path}")
        print(f"  文件大小: {len(md_content)} 字符")
        print(f"=" * 60)

    # summary-only 模式下将内容输出到 stdout
    if args.summary_only:
        print(md_content)
        if png_path:
            print(f"\n<!-- HEATMAP: {png_path} -->")


if __name__ == "__main__":
    main()
