"""
可视化模块

生成7张图表用于权重优化结果展示：
1. 逃顶风险分走势图
2. 抄底机会分走势图
3. 权重对比柱状图
4. ROC曲线
5. 指标诊断热力图
6. LOCO稳定性图
7. Bootstrap权重分布图
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from pathlib import Path
from typing import Dict, List, Optional
from sklearn.metrics import roc_curve, roc_auc_score

from config import PIC_DIR
from program.weight_optimizer.backtester import (
    compute_composite_score,
    compute_dynamic_threshold,
    smooth_composite,
    generate_alert_signal,
)
from program.weight_optimizer.indicator_preprocessor import (
    TOP_SCORE_GROUPS, BOTTOM_SCORE_GROUPS,
)

# 中文字体设置
rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False


def _setup_figure(figsize=(14, 7)):
    """创建标准化图表"""
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


def plot_top_score_backtest(
    top_grouped: pd.DataFrame,
    index_data: pd.DataFrame,
    daily_labels: pd.Series,
    optimized_weights: np.ndarray,
    output_dir: Optional[Path] = None,
) -> Path:
    """逃顶风险分走势图 + 上证指数 + 历史顶部标注

    Args:
        top_grouped: 逃顶组级矩阵
        index_data: 上证指数数据
        daily_labels: 每日标签
        optimized_weights: 优化后权重
        output_dir: 输出目录

    Returns:
        图片路径
    """
    output_dir = output_dir or PIC_DIR
    composite = compute_composite_score(top_grouped, optimized_weights)

    # V4: 应用EMA平滑
    smoothed = smooth_composite(composite)

    # 对齐
    common = composite.index.intersection(index_data.index)
    comp_raw = composite.loc[common]
    comp = smoothed.loc[common]
    close = index_data.loc[common, "close"]
    labels = daily_labels.reindex(common, fill_value="neutral")

    fig, ax1 = plt.subplots(figsize=(16, 8))

    # 上证指数
    ax1.plot(close.index, close.values, color="gray", alpha=0.6, linewidth=0.8, label="上证指数")
    ax1.set_ylabel("上证指数", color="gray")
    ax1.tick_params(axis="y", labelcolor="gray")

    # 标注顶部区域
    top_mask = labels.isin(["top_zone"])
    pre_top_mask = labels.isin(["pre_top_zone"])
    for mask, color, alpha in [(top_mask, "red", 0.2), (pre_top_mask, "orange", 0.1)]:
        if mask.any():
            ax1.fill_between(common, close.min(), close.max(),
                             where=mask.values, color=color, alpha=alpha)

    ax2 = ax1.twinx()

    # 显示原始分数（淡色）和平滑分数（实色）
    ax2.plot(comp_raw.index, comp_raw.values, color="red", linewidth=0.3,
             alpha=0.25, label="原始分数")
    ax2.plot(comp.index, comp.values, color="red", linewidth=1.2,
             alpha=0.9, label="逃顶风险分(平滑)")
    ax2.axhline(y=80, color="red", linestyle="--", alpha=0.3, linewidth=0.6, label="固定阈值80")

    # 动态阈值线（基于平滑分数）
    dyn_thresh = compute_dynamic_threshold(comp, "top")
    valid_thresh = dyn_thresh.dropna()
    if len(valid_thresh) > 0:
        ax2.plot(valid_thresh.index, valid_thresh.values, color="darkred",
                 linestyle=":", linewidth=1.2, alpha=0.7, label="动态阈值(80%分位)")

    # 警报区域高亮
    alert = generate_alert_signal(comp, "top")
    if alert.any():
        ax2.fill_between(comp.index, 0, 100,
                         where=alert.values, color="red", alpha=0.08)

    ax2.set_ylabel("逃顶风险分", color="red")
    ax2.tick_params(axis="y", labelcolor="red")

    ax1.set_title("逃顶风险分走势 vs 上证指数", fontsize=14)
    ax1.set_xlabel("日期")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

    plt.tight_layout()
    path = output_dir / "top_score_backtest.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ 逃顶走势图: {path}")
    return path


def plot_bottom_score_backtest(
    bottom_grouped: pd.DataFrame,
    index_data: pd.DataFrame,
    daily_labels: pd.Series,
    optimized_weights: np.ndarray,
    output_dir: Optional[Path] = None,
) -> Path:
    """抄底机会分走势图"""
    output_dir = output_dir or PIC_DIR
    composite = compute_composite_score(bottom_grouped, optimized_weights)
    # 翻转为机会分
    opportunity = 100 - composite

    # V4: 应用EMA平滑
    smoothed_opp = smooth_composite(opportunity)

    common = composite.index.intersection(index_data.index)
    opp_raw = opportunity.loc[common]
    opp = smoothed_opp.loc[common]
    close = index_data.loc[common, "close"]
    labels = daily_labels.reindex(common, fill_value="neutral")

    fig, ax1 = plt.subplots(figsize=(16, 8))

    ax1.plot(close.index, close.values, color="gray", alpha=0.6, linewidth=0.8, label="上证指数")
    ax1.set_ylabel("上证指数", color="gray")

    # 标注底部区域
    bottom_mask = labels.isin(["bottom_zone"])
    pre_bottom_mask = labels.isin(["pre_bottom_zone"])
    for mask, color, alpha in [(bottom_mask, "green", 0.2), (pre_bottom_mask, "lightgreen", 0.1)]:
        if mask.any():
            ax1.fill_between(common, close.min(), close.max(),
                             where=mask.values, color=color, alpha=alpha)

    ax2 = ax1.twinx()

    # 显示原始分数（淡色）和平滑分数（实色）
    ax2.plot(opp_raw.index, opp_raw.values, color="green", linewidth=0.3,
             alpha=0.25, label="原始分数")
    ax2.plot(opp.index, opp.values, color="green", linewidth=1.2,
             alpha=0.9, label="抄底机会分(平滑)")
    ax2.axhline(y=80, color="green", linestyle="--", alpha=0.3, linewidth=0.6, label="固定阈值80")

    # 动态阈值线（对于抄底，计算原始分数的低分位，再转换为机会分）
    smoothed_comp = smooth_composite(composite.loc[common])
    dyn_thresh_raw = compute_dynamic_threshold(smoothed_comp, "bottom")
    valid_thresh = dyn_thresh_raw.dropna()
    if len(valid_thresh) > 0:
        # 转为机会分空间
        opp_thresh = 100 - valid_thresh
        ax2.plot(opp_thresh.index, opp_thresh.values, color="darkgreen",
                 linestyle=":", linewidth=1.2, alpha=0.7, label="动态阈值(85%分位)")

    # 警报区域高亮（在risk score空间计算，再映射到opportunity空间）
    alert = generate_alert_signal(smoothed_comp, "bottom")
    if alert.any():
        ax2.fill_between(opp.index, 0, 100,
                         where=alert.values, color="green", alpha=0.08)

    ax2.set_ylabel("抄底机会分", color="green")
    ax2.tick_params(axis="y", labelcolor="green")

    ax1.set_title("抄底机会分走势 vs 上证指数", fontsize=14)
    ax1.set_xlabel("日期")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

    plt.tight_layout()
    path = output_dir / "bottom_score_backtest.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ 抄底走势图: {path}")
    return path


def plot_weight_comparison(
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """逃顶/抄底权重对比柱状图"""
    output_dir = output_dir or PIC_DIR

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for idx, (direction, title) in enumerate([("top", "逃顶风险分"), ("bottom", "抄底机会分")]):
        ax = axes[idx]
        data = optimization_results[direction]
        groups = data["group_names"]
        n = len(groups)
        x = np.arange(n)
        width = 0.25

        prior = data["prior_weights"]
        equal = np.ones(n) / n
        optimized = data["loco"]["final_weights"]

        ax.bar(x - width, prior, width, label="先验权重", color="skyblue", alpha=0.8)
        ax.bar(x, equal, width, label="等权", color="lightgray", alpha=0.8)
        ax.bar(x + width, optimized, width, label="优化后", color="coral", alpha=0.8)

        ax.set_title(title, fontsize=13)
        ax.set_xticks(x)
        ax.set_xticklabels(groups, rotation=15, ha="right")
        ax.set_ylabel("权重")
        ax.legend(fontsize=9)
        ax.set_ylim(0, 0.7)

    plt.tight_layout()
    path = output_dir / "weight_comparison.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ 权重对比图: {path}")
    return path


def plot_roc_curve(
    top_grouped: pd.DataFrame,
    bottom_grouped: pd.DataFrame,
    daily_labels: pd.Series,
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """逃顶/抄底AUC ROC曲线（两组权重对比）"""
    output_dir = output_dir or PIC_DIR

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for idx, (direction, grouped, groups, title) in enumerate([
        ("top", top_grouped, TOP_SCORE_GROUPS, "逃顶风险分 ROC"),
        ("bottom", bottom_grouped, BOTTOM_SCORE_GROUPS, "抄底机会分 ROC"),
    ]):
        ax = axes[idx]
        n = grouped.shape[1]
        opt_weights = optimization_results[direction]["loco"]["final_weights"]

        weight_sets = {
            "等权": np.ones(n) / n,
            "优化后": opt_weights,
        }

        colors = ["gray", "red"]

        for (set_name, weights), color in zip(weight_sets.items(), colors):
            composite = compute_composite_score(grouped, weights)
            common = composite.index.intersection(daily_labels.index)
            comp = composite.loc[common]
            labels = daily_labels.loc[common]

            if direction == "top":
                y_true = labels.isin(["pre_top_zone"]).astype(int)
                y_score = comp / 100.0
            else:
                y_true = labels.isin(["pre_bottom_zone"]).astype(int)
                y_score = 1 - comp / 100.0

            try:
                fpr, tpr, _ = roc_curve(y_true, y_score)
                auc_val = roc_auc_score(y_true, y_score)
                ax.plot(fpr, tpr, color=color, linewidth=1.5,
                        label=f"{set_name} (AUC={auc_val:.3f})")
            except Exception:
                pass

        ax.plot([0, 1], [0, 1], "k--", alpha=0.3)
        ax.set_title(title)
        ax.set_xlabel("False Positive Rate")
        ax.set_ylabel("True Positive Rate")
        ax.legend(fontsize=9)

    plt.tight_layout()
    path = output_dir / "roc_curve.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ ROC曲线图: {path}")
    return path


def plot_diagnostics(
    quality_report: pd.DataFrame,
    output_dir: Optional[Path] = None,
) -> Path:
    """指标诊断热力图"""
    output_dir = output_dir or PIC_DIR

    # 构建热力图数据
    indicators = quality_report["indicator"].values
    top_scores = quality_report["top_quality"].values
    bottom_scores = quality_report["bottom_quality"].values

    data = np.column_stack([top_scores, bottom_scores])

    fig, ax = plt.subplots(figsize=(8, max(6, len(indicators) * 0.6)))
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks([0, 1])
    ax.set_xticklabels(["逃顶质量", "抄底质量"])
    ax.set_yticks(range(len(indicators)))
    ax.set_yticklabels(indicators)

    # 数值标注
    for i in range(len(indicators)):
        for j in range(2):
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center",
                    color="black", fontsize=11)

    ax.set_title("指标诊断质量评分", fontsize=14)
    plt.colorbar(im, ax=ax, shrink=0.8)

    plt.tight_layout()
    path = output_dir / "indicator_diagnostics.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ 诊断热力图: {path}")
    return path


def plot_cv_stability(
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """LOCO各fold权重分布图"""
    output_dir = output_dir or PIC_DIR

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for idx, (direction, title) in enumerate([("top", "逃顶 LOCO权重"), ("bottom", "抄底 LOCO权重")]):
        ax = axes[idx]
        data = optimization_results[direction]
        groups = data["group_names"]
        fold_weights = data["loco"]["fold_weights"]

        if len(fold_weights) == 0:
            ax.text(0.5, 0.5, "无数据", ha="center", va="center")
            ax.set_title(title)
            continue

        fold_arr = np.array(fold_weights)
        n_folds, n_groups = fold_arr.shape
        x = np.arange(n_groups)

        # 箱线图
        bp = ax.boxplot([fold_arr[:, i] for i in range(n_groups)],
                        positions=x, widths=0.5, patch_artist=True)
        for patch in bp["boxes"]:
            patch.set_facecolor("lightblue")

        # 最终权重
        final = data["loco"]["final_weights"]
        ax.scatter(x, final, color="red", zorder=5, s=80, marker="D", label="最终(中位数)")

        ax.set_xticks(x)
        ax.set_xticklabels(groups, rotation=15, ha="right")
        ax.set_ylabel("权重")
        ax.set_title(f"{title} (N={n_folds} folds)")
        ax.legend()

    plt.tight_layout()
    path = output_dir / "cv_stability.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ LOCO稳定性图: {path}")
    return path


def plot_weight_distribution(
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """Bootstrap权重置信区间图"""
    output_dir = output_dir or PIC_DIR

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for idx, (direction, title) in enumerate([("top", "逃顶权重分布"), ("bottom", "抄底权重分布")]):
        ax = axes[idx]
        data = optimization_results[direction]
        groups = data["group_names"]

        if "bootstrap" not in data:
            # 没有bootstrap数据，显示LOCO结果
            final = data["loco"]["final_weights"]
            std = data["loco"]["weight_std"]
            x = np.arange(len(groups))
            ax.bar(x, final, yerr=std, capsize=5, color="coral", alpha=0.7)
            ax.set_xticks(x)
            ax.set_xticklabels(groups, rotation=15, ha="right")
            ax.set_title(f"{title} (LOCO std)")
            ax.set_ylabel("权重")
            continue

        bootstrap = data["bootstrap"]
        all_weights = bootstrap["all_weights"]

        x = np.arange(len(groups))
        median = bootstrap["median_weights"]
        ci_5 = bootstrap["ci_5"]
        ci_95 = bootstrap["ci_95"]

        # 箱线图
        bp = ax.boxplot([all_weights[:, i] for i in range(len(groups))],
                        positions=x, widths=0.5, patch_artist=True)
        for patch in bp["boxes"]:
            patch.set_facecolor("lightyellow")

        ax.scatter(x, median, color="red", zorder=5, s=80, marker="D", label="中位数")

        # CI标注
        for i in range(len(groups)):
            ax.annotate(f"[{ci_5[i]:.2f}, {ci_95[i]:.2f}]",
                        xy=(i, ci_95[i]), xytext=(0, 8),
                        textcoords="offset points", ha="center", fontsize=8)

        ax.set_xticks(x)
        ax.set_xticklabels(groups, rotation=15, ha="right")
        ax.set_title(f"{title} (N={len(all_weights)} bootstrap)")
        ax.set_ylabel("权重")
        ax.legend()

    plt.tight_layout()
    path = output_dir / "weight_distribution.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"✓ 权重分布图: {path}")
    return path


def generate_all_visualizations(
    prep_result: Dict,
    label_result: Dict,
    diag_result: Dict,
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    """生成所有7张可视化图表

    Args:
        prep_result: 预处理结果
        label_result: 标注结果
        diag_result: 诊断结果
        optimization_results: 优化结果
        output_dir: 输出目录

    Returns:
        List[Path]: 生成的图片路径列表
    """
    output_dir = output_dir or PIC_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("生成可视化")
    print("=" * 60)

    paths = []

    # 1. 逃顶走势图
    paths.append(plot_top_score_backtest(
        prep_result["top_grouped"],
        label_result["index_data"],
        label_result["daily_labels"],
        optimization_results["top"]["loco"]["final_weights"],
        output_dir,
    ))

    # 2. 抄底走势图
    paths.append(plot_bottom_score_backtest(
        prep_result["bottom_grouped"],
        label_result["index_data"],
        label_result["daily_labels"],
        optimization_results["bottom"]["loco"]["final_weights"],
        output_dir,
    ))

    # 3. 权重对比图
    paths.append(plot_weight_comparison(optimization_results, output_dir))

    # 4. ROC曲线
    paths.append(plot_roc_curve(
        prep_result["top_grouped"],
        prep_result["bottom_grouped"],
        label_result["daily_labels"],
        optimization_results,
        output_dir,
    ))

    # 5. 诊断热力图
    paths.append(plot_diagnostics(diag_result["quality_report"], output_dir))

    # 6. LOCO稳定性图
    paths.append(plot_cv_stability(optimization_results, output_dir))

    # 7. 权重分布图
    paths.append(plot_weight_distribution(optimization_results, output_dir))

    print(f"\n✓ 共生成 {len(paths)} 张图表")

    return paths


def plot_unified_score(
    unified_series: pd.Series,
    evidence_df: pd.DataFrame,
    phases_df: pd.DataFrame,
    index_data: pd.DataFrame,
    output_dir: Optional[Path] = None,
) -> Path:
    """统一牛熊指标走势图

    上面板: 上证指数走势 + 市场阶段着色
    中面板: 统一分数(0-100) + 多空区域填充
    下面板: 证据分解 (bear/bull evidence)

    Args:
        unified_series: 统一分数序列
        evidence_df: 证据 DataFrame (bear_evidence, bull_evidence, net_evidence)
        phases_df: 阶段 DataFrame (phase, is_conflicted, etc.)
        index_data: 上证指数数据 (close 列)
        output_dir: 输出目录

    Returns:
        Path: 图片保存路径
    """
    output_dir = output_dir or PIC_DIR

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(16, 14), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 1]},
    )

    # 对齐
    close = index_data["close"]
    common = unified_series.index.intersection(close.index)
    if len(common) == 0:
        plt.close(fig)
        raise ValueError("No overlapping dates between unified score and index_data")

    score = unified_series.loc[common]
    price = close.loc[common]
    ev = evidence_df.reindex(common)

    # --- 上面板: 上证指数 + 阶段着色 ---
    ax1.plot(common, price, color="black", linewidth=0.8, label="上证指数")

    phase_colors = {
        "极度低估": "#2ecc71", "偏多": "#a8e6cf",
        "中性": "#f0f0f0", "升温中": "#ffd93d", "降温中": "#b8d4e3",
        "偏空": "#ffb347", "极度高估": "#e74c3c", "冲突": "#9b59b6",
    }
    phases = phases_df.reindex(common)
    if "phase" in phases.columns:
        prev_phase = None
        start_idx = common[0]
        for dt in common:
            phase = phases.loc[dt, "phase"]
            if phase != prev_phase and prev_phase is not None:
                color = phase_colors.get(prev_phase, "#f0f0f0")
                ax1.axvspan(start_idx, dt, alpha=0.15, color=color)
                start_idx = dt
            prev_phase = phase
        # 最后一段
        if prev_phase:
            color = phase_colors.get(prev_phase, "#f0f0f0")
            ax1.axvspan(start_idx, common[-1], alpha=0.15, color=color)

    ax1.set_ylabel("上证指数")
    ax1.set_title("统一牛熊指标（市场温度计）", fontsize=14)
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # --- 中面板: 统一分数 ---
    ax2.fill_between(common, score, 50, where=score >= 50,
                     color="#e74c3c", alpha=0.3, label="偏空区")
    ax2.fill_between(common, score, 50, where=score < 50,
                     color="#2ecc71", alpha=0.3, label="偏多区")
    ax2.plot(common, score, color="#333333", linewidth=1.0)
    ax2.axhline(80, color="red", linestyle="--", alpha=0.5, label="极度高估(80)")
    ax2.axhline(60, color="orange", linestyle="--", alpha=0.3)
    ax2.axhline(50, color="gray", linestyle="-", alpha=0.5)
    ax2.axhline(40, color="lightblue", linestyle="--", alpha=0.3)
    ax2.axhline(20, color="green", linestyle="--", alpha=0.5, label="极度低估(20)")
    ax2.set_ylabel("统一分数")
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)

    # --- 下面板: 证据分解 ---
    ax3.plot(common, ev["bear_evidence"], color="#e74c3c",
             linewidth=0.8, label="空头证据")
    ax3.plot(common, ev["bull_evidence"], color="#2ecc71",
             linewidth=0.8, label="多头证据")
    ax3.axhline(0.3, color="purple", linestyle=":", alpha=0.5,
                label="冲突阈值(0.3)")
    ax3.set_ylabel("证据强度")
    ax3.set_ylim(0, 1)
    ax3.legend(loc="upper left", fontsize=8)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    output_path = output_dir / "unified_bull_bear_score.png"
    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ 统一牛熊指标图已保存: {output_path}")
    return output_path


def plot_cycle_position(
    score_series: pd.Series,
    phases_df: pd.DataFrame,
    index_data: pd.DataFrame,
    signals_df: Optional[pd.DataFrame] = None,
    output_dir: Optional[Path] = None,
    start_date: str = "2014-01-01",
    report_summary: Optional[dict] = None,
    emotion_score_series: Optional[pd.Series] = None,
) -> Path:
    """牛熊周期定位图

    上面板: 上证指数走势 + 阶段着色背景 + 逃顶/抄底信号
    下面板: 周期分数(0-100) + 阶段区域 + 信号标记

    Parameters
    ----------
    start_date : str
        图表起始日期，默认 "2014-01-01"（多数指标从2014年才有数据）。
    report_summary : dict, optional
        当日信号摘要（generate_cycle_report 返回值），用于标题 + 结论文本框。
        含 date/cycle_score/phase/signal_state/advice/last_signal 等键。
        为 None 时图标题保持通用（回测调用不传，行为不变）。
    emotion_score_series : pd.Series, optional
        情绪子系统分数（与主分数同口径 0-100）。传入时下面板叠加情绪线，
        与主分数劈叉处即"情绪 vs 价格"背离（如 2026 情绪爆表而价格降温）。
        为 None 时下面板只画主分数（回测调用不传，行为不变）。
    """
    output_dir = output_dir or PIC_DIR

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 10), sharex=True,
        gridspec_kw={"height_ratios": [2, 1]},
    )

    close = index_data["close"]
    valid_score = score_series.dropna()
    # 过滤起始日期：2014年前指标不足，分数不可靠
    cutoff = pd.Timestamp(start_date)
    valid_score = valid_score.loc[valid_score.index >= cutoff]
    common = valid_score.index.intersection(close.index)
    if len(common) == 0:
        plt.close(fig)
        raise ValueError("No overlapping dates")

    price = close.loc[common]
    score = valid_score.loc[common].astype(float).values

    # --- 上面板: 指数 + 阶段背景着色 ---
    ax1.plot(common, price, color="black", linewidth=0.8)

    # 顶底区域着色（三档：红=高危/极度高估，黄=中等危险/偏热警戒，绿=低估/机会）
    score_arr = np.asarray(score)
    ax1.fill_between(common, price.min(), price.max(),
                     where=score_arr > 70, color="#e74c3c", alpha=0.15, label="偏空/极度高估")
    ax1.fill_between(common, price.min(), price.max(),
                     where=(score_arr >= 50) & (score_arr <= 70),
                     color="#f1c40f", alpha=0.12, label="中等危险(偏热警戒)")
    ax1.fill_between(common, price.min(), price.max(),
                     where=score_arr < 30, color="#2ecc71", alpha=0.15, label="偏多/极度低估")

    ax1.set_ylabel("上证指数")

    # 标题：有当日摘要则展开，否则保持通用
    if report_summary:
        rs = report_summary
        # 标题不放 emoji（matplotlib 字体多缺彩色 emoji 字形，会渲染成豆腐块）
        title = (f"A股牛熊周期定位 | {rs.get('date', '')}  "
                 f"分数 {rs.get('cycle_score', '')} "
                 f"{rs.get('phase', '')}  "
                 f"状态 {rs.get('signal_state', 'normal')}")
        ax1.set_title(title, fontsize=14)

        # 结论文本框（最近信号 + 当前建议）。放右上角，避开左上角图例。
        lines = [f"操作建议: {rs.get('advice', '')}"]
        last_sig = rs.get('last_signal')
        if last_sig:
            # 显式映射4类信号，避免认错回补/回补止损被误显示成"抄底"（M4）
            sig_cn = {
                'escape_top': '重仓逃顶',
                'buy_bottom': '重仓抄底',
                're_entry': '认错回补',
                're_entry_stop': '回补止损',
            }.get(last_sig.get('type'), last_sig.get('type'))
            lines.append(f"最近重仓: {sig_cn} ({last_sig.get('date')})")
        last_light = rs.get('last_light_signal')
        if last_light:
            light_cn = ("轻仓逃顶" if last_light.get('type') == 'light_escape_top'
                        else "轻仓抄底")
            lines.append(f"最近轻仓: {light_cn} ({last_light.get('date')})")
        # 数据可信度（残缺指标集时在图上明示，避免误读分数）
        fr = rs.get('freshness')
        if fr:
            lv = {'high': '高', 'medium': '中', 'low': '低'}.get(fr.get('level'), fr.get('level'))
            lines.append(f"数据可信度: {lv} ({fr.get('fresh_count')}/{fr.get('total')}指标)")
            if fr.get('level') == 'low':
                lines.append("注意: 指标不足,分数仅供参考")
        # 可信度低时文本框用红色边框警示
        box_color = 'mistyrose' if (fr and fr.get('level') == 'low') else 'wheat'
        ax1.text(
            0.988, 0.97, "\n".join(lines),
            transform=ax1.transAxes, va='top', ha='right', fontsize=10,
            bbox=dict(boxstyle='round', facecolor=box_color, alpha=0.6),
        )
    else:
        ax1.set_title("A股牛熊周期定位器（指标等权平均）", fontsize=14)
    ax1.grid(True, alpha=0.3)

    # --- 信号标记 ---
    if signals_df is not None:
        # 信号日期可能是节假日（如春节），用最近的交易日价格。
        # 限制在 common（>= start_date 且有价格）范围内：早于 cutoff 的信号
        # （如 2010 年）不应出现在本图，否则 reindex(method='nearest') 会把它们
        # 吸附到 2014 初的价格、画成脱离曲线的“孤儿”标记，并把 X 轴拉到 2010。
        price_full = close.reindex(common, method='nearest')

        def _sig_dates(col, value):
            """取某类信号日期并裁剪到 common 范围（与下面板口径一致）。"""
            return signals_df.index[signals_df[col] == value].intersection(common)

        # === 轻仓信号（小标记，半透明）===
        if 'signal_light' in signals_df.columns:
            light_escape = _sig_dates('signal_light', 'light_escape_top')
            light_buy = _sig_dates('signal_light', 'light_buy_bottom')

            if len(light_escape) > 0:
                lesc_prices = price_full.reindex(light_escape, method='nearest').dropna()
                ax1.scatter(lesc_prices.index, lesc_prices,
                           marker='v', s=50, color='#ff9999', zorder=4,
                           edgecolors='red', linewidths=0.8, alpha=0.7, label='轻仓逃顶')

            if len(light_buy) > 0:
                lbuy_prices = price_full.reindex(light_buy, method='nearest').dropna()
                ax1.scatter(lbuy_prices.index, lbuy_prices,
                           marker='^', s=50, color='#99ff99', zorder=4,
                           edgecolors='green', linewidths=0.8, alpha=0.7, label='轻仓抄底')
                for d, p in zip(lbuy_prices.index, lbuy_prices):
                    ax1.annotate('轻仓抄底', (d, p), textcoords="offset points",
                               xytext=(0, -16), ha='center', fontsize=7,
                               color='#2e7d32', alpha=0.9)

        # === 重仓信号（大标记，带标注）===
        escape_dates = _sig_dates('signal', 'escape_top')
        buy_dates = _sig_dates('signal', 'buy_bottom')
        reentry_dates = _sig_dates('signal', 're_entry')
        reentry_stop_dates = _sig_dates('signal', 're_entry_stop')

        if len(escape_dates) > 0:
            escape_prices = price_full.reindex(escape_dates, method='nearest').dropna()
            escape_dates = escape_prices.index
            ax1.scatter(escape_dates, escape_prices,
                       marker='v', s=150, color='red', zorder=5,
                       edgecolors='darkred', linewidths=1.5, label='重仓逃顶')
            for d, p in zip(escape_dates, escape_prices):
                ax1.annotate('重仓逃顶', (d, p), textcoords="offset points",
                           xytext=(0, 15), ha='center', fontsize=8,
                           color='red', fontweight='bold')

        if len(buy_dates) > 0:
            buy_prices = price_full.reindex(buy_dates, method='nearest').dropna()
            buy_dates = buy_prices.index
            ax1.scatter(buy_dates, buy_prices,
                       marker='^', s=150, color='green', zorder=5,
                       edgecolors='darkgreen', linewidths=1.5, label='重仓抄底')
            for d, p in zip(buy_dates, buy_prices):
                ax1.annotate('重仓抄底', (d, p), textcoords="offset points",
                           xytext=(0, -18), ha='center', fontsize=8,
                           color='green', fontweight='bold')

        # === 认错回补信号（蓝色标记）===
        if len(reentry_dates) > 0:
            re_prices = price_full.reindex(reentry_dates, method='nearest').dropna()
            reentry_dates = re_prices.index
            ax1.scatter(reentry_dates, re_prices,
                       marker='^', s=150, color='#3498db', zorder=5,
                       edgecolors='darkblue', linewidths=1.5, label='认错回补')
            for d, p in zip(reentry_dates, re_prices):
                ax1.annotate('回补', (d, p), textcoords="offset points",
                           xytext=(0, -18), ha='center', fontsize=8,
                           color='#3498db', fontweight='bold')

        if len(reentry_stop_dates) > 0:
            rs_prices = price_full.reindex(reentry_stop_dates, method='nearest').dropna()
            reentry_stop_dates = rs_prices.index
            ax1.scatter(reentry_stop_dates, rs_prices,
                       marker='x', s=120, color='orange', zorder=5,
                       linewidths=2.5, label='回补止损')
            for d, p in zip(reentry_stop_dates, rs_prices):
                ax1.annotate('止损', (d, p), textcoords="offset points",
                           xytext=(0, 15), ha='center', fontsize=8,
                           color='orange', fontweight='bold')

    ax1.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.85)

    # --- 下面板: 周期分数 ---
    ax2.fill_between(common, score, 50, where=score >= 50,
                     color="#e74c3c", alpha=0.3)
    ax2.fill_between(common, score, 50, where=score < 50,
                     color="#2ecc71", alpha=0.3)
    ax2.plot(common, score, color="#333333", linewidth=1.0, label="主分数(8指标)")

    # 情绪子分数（与主分数同口径）。两线劈叉=情绪vs价格背离：情绪线在上=情绪过热但
    # 价格/技术未跟（如2026 herding/换手爆表而年线乖离降温），是顶部派发的危险结构。
    # NaN 日（数据残缺）由 matplotlib 自动断线；全空则跳过，避免画无意义的空图例。
    if emotion_score_series is not None:
        emo_aligned = emotion_score_series.reindex(common).astype(float)
        if emo_aligned.notna().any():
            ax2.plot(common, emo_aligned.values, color="#e67e22", linewidth=1.0,
                     linestyle="--", alpha=0.9, label="情绪分数(4指标)")

    # 关键线精简为系统真正用到的3条（原80/60/40/20太挤，且非信号阈值）：
    #   顶区/进入线75(arm_high) 撤离线50(top_clear) 退烧线62.5(回补门控,两线中点)
    ax2.axhline(75, color="red", linestyle="--", alpha=0.55, label="顶区/进入线(75)")
    ax2.axhline(62.5, color="#8e44ad", linestyle=":", alpha=0.7, label="退烧线(62.5)")
    ax2.axhline(50, color="gray", linestyle="-", alpha=0.35, label="撤离线(50)")
    ax2.axhline(20, color="green", linestyle="--", alpha=0.5, label="极度低估(20)")

    # 信号标记在分数图上
    if signals_df is not None:
        # 轻仓信号（小标记）
        if 'signal_light' in signals_df.columns:
            light_escape = _sig_dates('signal_light', 'light_escape_top')
            light_buy = _sig_dates('signal_light', 'light_buy_bottom')

            if len(light_escape) > 0:
                lesc_scores = valid_score.loc[light_escape].astype(float)
                ax2.scatter(light_escape, lesc_scores,
                           marker='v', s=40, color='#ff9999', zorder=4,
                           edgecolors='red', linewidths=0.8, alpha=0.7)
            if len(light_buy) > 0:
                lbuy_scores = valid_score.loc[light_buy].astype(float)
                ax2.scatter(light_buy, lbuy_scores,
                           marker='^', s=40, color='#99ff99', zorder=4,
                           edgecolors='green', linewidths=0.8, alpha=0.7)

        # 重仓信号（大标记）
        escape_dates = _sig_dates('signal', 'escape_top')
        buy_dates = _sig_dates('signal', 'buy_bottom')
        reentry_dates = _sig_dates('signal', 're_entry')
        reentry_stop_dates = _sig_dates('signal', 're_entry_stop')

        if len(escape_dates) > 0:
            escape_scores = valid_score.loc[escape_dates].astype(float)
            ax2.scatter(escape_dates, escape_scores,
                       marker='v', s=100, color='red', zorder=5, edgecolors='darkred')
        if len(buy_dates) > 0:
            buy_scores = valid_score.loc[buy_dates].astype(float)
            ax2.scatter(buy_dates, buy_scores,
                       marker='^', s=100, color='green', zorder=5, edgecolors='darkgreen')
        if len(reentry_dates) > 0:
            re_scores = valid_score.loc[reentry_dates].astype(float)
            ax2.scatter(reentry_dates, re_scores,
                       marker='^', s=100, color='#3498db', zorder=5, edgecolors='darkblue')
        if len(reentry_stop_dates) > 0:
            rs_scores = valid_score.loc[reentry_stop_dates].astype(float)
            ax2.scatter(reentry_stop_dates, rs_scores,
                       marker='x', s=80, color='orange', zorder=5, linewidths=2.5)

    ax2.set_ylabel("周期分数")
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    output_path = output_dir / "cycle_position.png"
    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ 周期定位图已保存: {output_path}")
    return output_path
