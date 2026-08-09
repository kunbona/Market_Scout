"""周期信号生成 runner

组装输入 → generate_cycle_report → 出图 + 写 JSON 摘要。

输入全部来自每日链路已写好的产物：
- data/processed/*.csv（11个指标的 risk_percentage）
- data/raw/stock_zh_index_daily_sh000001.csv（上证收盘价）
- data/processed/limit_up_down_ratio.csv（可选，breadth thrust 抄底确认）

limit_up 缺失时优雅降级：少一条确认路径，分数回撤路径仍兜底。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

from config import RAW_DATA_DIR, PROCESSED_DATA_DIR, RESULTS_DIR, PIC_DIR
from program.weight_optimizer.indicator_preprocessor import (
    load_indicator_data,
    composite_pe,
)
from program.weight_optimizer.cycle_position import generate_cycle_report
from program.weight_optimizer.label_generator import load_index_data
from program.weight_optimizer.visualizer import plot_cycle_position


SIGNAL_JSON_PATH = RESULTS_DIR / "cycle_signal_latest.json"

# 周期定位使用的指标集（与 run_weight_optimization.py 保持一致）
CYCLE_INDICATOR_KEYS = [
    "hs300_equity_premium",
    "ma250_bias",
    "below_net_asset",
    "market_crowdedness",
    "herding_rate",
    "market_turnover_percentile",
    "price_percentile",
]


def _load_limit_up_ratio() -> Optional[pd.Series]:
    """加载每日涨停占比(0-1)，用于 breadth thrust 抄底确认。缺失返回 None。

    注意：CSV 的 up_ratio 列是百分比尺度（如 0.5 表示 0.5%，limit_up/total*100），
    而 generate_signals 期望 0-1 比例（内部再 *100 比阈值），故此处需 /100 转回比例。
    """
    path = PROCESSED_DATA_DIR / "limit_up_down_ratio.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        if "交易日期" not in df.columns or "up_ratio" not in df.columns:
            return None
        df["交易日期"] = pd.to_datetime(df["交易日期"])
        series = df.set_index("交易日期")["up_ratio"].sort_index()
        series = series[~series.index.duplicated(keep="last")]
        # 百分比 → 0-1 比例
        return series / 100.0
    except Exception as e:
        print(f"⚠ 涨停占比加载失败，跳过 breadth thrust 确认: {e}")
        return None


def _load_breadth_pct() -> Optional[pd.Series]:
    """加载站上MA60的历史分位(0-100)，用于广度背离分类器。缺失返回 None。

    高分位=广度健康(普涨)，低分位=广度背离(结构顶)。
    缺失时优雅降级：不做广度分类，纯靠情绪+主分数进入临界。
    """
    path = PROCESSED_DATA_DIR / "breadth_above_ma60.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        if "交易日期" not in df.columns or "breadth_pct" not in df.columns:
            return None
        df["交易日期"] = pd.to_datetime(df["交易日期"])
        series = df.set_index("交易日期")["breadth_pct"].sort_index()
        series = series[~series.index.duplicated(keep="last")]
        return series
    except Exception as e:
        print(f"⚠ 广度分位加载失败，跳过广度分类: {e}")
        return None


def _assemble_cycle_inputs() -> Tuple[Dict[str, pd.Series], pd.Series, Optional[pd.Series], Optional[pd.Series], Optional[pd.Timestamp], Dict]:
    """组装周期定位的全部输入（指标字典、收盘价、涨停占比、广度分位、基准日、截断前最新日）。

    从 build_cycle_report 抽出，使实验脚本能复用同一套输入装配逻辑（避免装配口径漂移），
    再以不同信号参数重跑 generate_signals。行为与原 build_cycle_report 完全一致。

    Returns:
        (cycle_indicators, close, limit_up_ratio, breadth_pct, baseline_date, pre_truncate_latest)
    """
    # 1. 加载所有指标 + 合成PE
    indicator_data = load_indicator_data()
    if not indicator_data:
        raise ValueError("无法加载任何指标数据，请先运行 run_indicators.py")

    pe_composite = composite_pe(indicator_data)

    # 2. 组装周期指标字典
    cycle_indicators: Dict[str, pd.Series] = {"pe_composite": pe_composite}
    for key in CYCLE_INDICATOR_KEYS:
        if key in indicator_data:
            cycle_indicators[key] = indicator_data[key]
        else:
            print(f"⚠ 周期指标缺失，跳过: {key}")

    # 4. 上证收盘价
    index_data = load_index_data()
    close = index_data["close"]

    # 4.5 截断到指标基准日（所有周期指标都齐全的最后共同交易日）。
    pre_truncate_latest = {
        name: (s.dropna().index[-1] if not s.dropna().empty else None)
        for name, s in cycle_indicators.items()
    }
    valid_latest = [d for d in pre_truncate_latest.values() if d is not None]
    baseline_date = min(valid_latest) if valid_latest else None

    if baseline_date is not None:
        cycle_indicators = {
            name: s[s.index <= baseline_date] for name, s in cycle_indicators.items()
        }
        close = close[close.index <= baseline_date]

    # 5. 可选：涨停占比（breadth thrust 抄底确认）
    limit_up_ratio = _load_limit_up_ratio()
    if baseline_date is not None and limit_up_ratio is not None:
        limit_up_ratio = limit_up_ratio[limit_up_ratio.index <= baseline_date]

    # 5.5 可选：站上MA60历史分位（广度背离分类器）
    breadth_pct = _load_breadth_pct()
    if baseline_date is not None and breadth_pct is not None:
        breadth_pct = breadth_pct[breadth_pct.index <= baseline_date]

    return cycle_indicators, close, limit_up_ratio, breadth_pct, baseline_date, pre_truncate_latest


def build_cycle_report() -> Tuple[Dict, pd.DataFrame]:
    """组装输入并生成周期定位报告

    Returns:
        (report, index_data)
        report: generate_cycle_report 的返回字典
        index_data: 上证指数 DataFrame（含 close 列），供出图复用
    """
    # 注：new_high_ratio(创新高占比) 已移除。它是原始占比(中位3.9)而非0-100百分位,
    # 混入等权平均会系统性拉低分数(81%天数,平均-4.46),且在2018/2021结构牛顶失效
    # (只少数权重股创新高→占比反而低),共识过滤器里>75只占0.06%形同废票。
    # 移除后9/9历史捕获率不变,且2015逃顶回到06-12(5166)历史最高点。详见 bug-review-decisions.md S2。

    # 输入装配（与实验脚本共用，避免口径漂移）
    cycle_indicators, close, limit_up_ratio, breadth_pct, baseline_date, pre_truncate_latest = _assemble_cycle_inputs()

    # 出图复用完整 index_data（含 close 列、不截断，与历史行为一致）
    index_data = load_index_data()

    # 6. 生成报告
    report = generate_cycle_report(
        cycle_indicators,
        close,
        limit_up_ratio=limit_up_ratio,
        breadth_pct=breadth_pct,
    )

    # 7. 数据新鲜度评估：当某些指标长期停更时，近期分数其实是用残缺指标集算的，
    #    系统不能静默——把每个指标的最新日期 + 当前可用指标数 + 可信度等级写进 report。
    report["freshness"] = _assess_freshness(cycle_indicators, report.get("date"))

    # 8. 数据一致性：哪些指标在基准日之后其实还有更新（被截断丢弃），明示给用户。
    report["consistency"] = _assess_consistency(pre_truncate_latest, baseline_date)

    return report, index_data


def _assess_consistency(
    pre_truncate_latest: Dict[str, Optional[pd.Timestamp]],
    baseline_date: Optional[pd.Timestamp],
) -> Dict:
    """对比截断前各指标最新日与基准日，列出超前（已更新但被丢弃）的指标。

    Returns:
        {consistent, baseline, newest, ahead: [{indicator, latest, lead_days}]}
    """
    valid = {k: v for k, v in pre_truncate_latest.items() if v is not None}
    if not valid or baseline_date is None:
        return {"consistent": True, "baseline": None, "newest": None, "ahead": []}

    newest = max(valid.values())
    ahead = []
    for name, d in valid.items():
        lead = (d - baseline_date).days
        if lead > 0:
            ahead.append({
                "indicator": name,
                "latest": d.strftime("%Y-%m-%d"),
                "lead_days": int(lead),
            })
    ahead.sort(key=lambda x: x["lead_days"], reverse=True)

    return {
        "consistent": baseline_date == newest,
        "baseline": baseline_date.strftime("%Y-%m-%d"),
        "newest": newest.strftime("%Y-%m-%d"),
        "ahead": ahead,
    }


# 数据新鲜度阈值（自然日）：指标最新日期距报告日超过此值视为"过期"
FRESHNESS_STALE_DAYS = 14
# 可信度分级：有效(未过期)指标数的阈值
FRESHNESS_MIN_RELIABLE = 7   # >=7 个新鲜指标 → 高可信
FRESHNESS_MIN_USABLE = 5     # 5-6 → 中等；<5 → 低可信


def _assess_freshness(
    cycle_indicators: Dict[str, pd.Series], report_date: Optional[str]
) -> Dict:
    """评估各指标的数据新鲜度，返回 {level, fresh_count, total, stale: [...], as_of}

    判断逻辑：以 report_date 为基准，每个指标的最新有效日期若落后超过
    FRESHNESS_STALE_DAYS 天，标记为 stale。fresh_count 决定整体可信度等级。
    """
    if report_date is None:
        ref = max(
            (s.dropna().index[-1] for s in cycle_indicators.values() if not s.dropna().empty),
            default=None,
        )
    else:
        ref = pd.Timestamp(report_date)

    total = len(cycle_indicators)
    stale = []
    latest_map = {}
    for name, s in cycle_indicators.items():
        sv = s.dropna()
        if sv.empty:
            stale.append({"indicator": name, "latest": None, "lag_days": None})
            latest_map[name] = None
            continue
        latest = sv.index[-1]
        latest_map[name] = latest.strftime("%Y-%m-%d")
        if ref is not None:
            lag = (ref - latest).days
            if lag > FRESHNESS_STALE_DAYS:
                stale.append({
                    "indicator": name,
                    "latest": latest.strftime("%Y-%m-%d"),
                    "lag_days": int(lag),
                })

    fresh_count = total - len(stale)
    if fresh_count >= FRESHNESS_MIN_RELIABLE:
        level = "high"
    elif fresh_count >= FRESHNESS_MIN_USABLE:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "fresh_count": fresh_count,
        "total": total,
        "stale": stale,
        "as_of": ref.strftime("%Y-%m-%d") if ref is not None else None,
        "latest_per_indicator": latest_map,
    }


def _write_signal_json(report: Dict) -> Path:
    """把当日信号摘要写成机器可读 JSON"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "date": report.get("date"),
        "cycle_score": report.get("cycle_score"),
        "phase": report.get("phase"),
        "emoji": report.get("emoji"),
        "advice": report.get("advice"),
        "signal_state": report.get("signal_state"),
        "current_signal": report.get("current_signal"),
        "current_light_signal": report.get("current_light_signal"),
        "last_signal": report.get("last_signal"),
        "last_light_signal": report.get("last_light_signal"),
        # 权威事件 + 当前仓位（单一事实源；live 链路此前无仓位概念）
        "current_event": report.get("current_event"),
        "current_position": report.get("current_position"),
        "last_event": report.get("last_event"),
        "indicator_count": report.get("indicator_count"),
        "freshness": report.get("freshness"),
        "consistency": report.get("consistency"),
        "generated_at": datetime.now().isoformat(),
    }

    tmp_path = SIGNAL_JSON_PATH.with_suffix(".tmp.json")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    if SIGNAL_JSON_PATH.exists():
        SIGNAL_JSON_PATH.unlink()
    tmp_path.rename(SIGNAL_JSON_PATH)

    return SIGNAL_JSON_PATH


def _print_report_summary(report: Dict) -> None:
    """打印周期信号摘要（格式参考 run_weight_optimization.py）"""
    score = report["cycle_score"]
    bar_len = int(score / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print()
    print("=" * 70)
    print("📊 A股牛熊周期定位")
    print("=" * 70)
    print(f"  日期:     {report['date']}")
    print(f"  周期分数: {bar} {score}")
    print(f"  市场阶段: {report['emoji']} {report['phase']}")
    print(f"  操作建议: {report['advice']}")

    signal_state = report.get("signal_state", "normal")
    state_emoji = {
        "normal": "⚪",
        "near_top": "🔴",
        "near_bottom": "🟢",
        "cooldown": "⏳",
    }.get(signal_state, "⚪")
    print(f"  信号状态: {state_emoji} {signal_state}")

    last_sig = report.get("last_signal")
    if last_sig:
        # 显式映射4类信号，避免认错回补/回补止损被误显示成"抄底"（M4）
        sig_label = {
            "escape_top": "🔴 重仓逃顶",
            "buy_bottom": "🟢 重仓抄底",
            "re_entry": "🔵 认错回补",
            "re_entry_stop": "🟠 回补止损",
        }.get(last_sig["type"], last_sig["type"])
        print(f"  最近重仓信号: {sig_label} ({last_sig['date']})")

    last_light = report.get("last_light_signal")
    if last_light:
        print(f"  最近轻仓信号: {last_light['type']} ({last_light['date']})")

    # 数据新鲜度警告：分数用残缺指标集算出时必须明示，避免被误导
    fr = report.get("freshness")
    if fr:
        level_cn = {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(fr["level"], fr["level"])
        print()
        print(f"  数据可信度: {level_cn}  (有效指标 {fr['fresh_count']}/{fr['total']}, 截至 {fr.get('as_of')})")
        if fr["stale"]:
            print(f"  ⚠ 以下 {len(fr['stale'])} 个指标已停更，当前分数未纳入它们:")
            for item in fr["stale"]:
                last = item.get("latest") or "无数据"
                lag = item.get("lag_days")
                lag_s = f"落后{lag}天" if lag is not None else ""
                print(f"      - {item['indicator']}: 最新 {last} {lag_s}")
        if fr["level"] == "low":
            print(f"  🔴 警告: 有效指标不足，当前分数可信度低，建议谨慎参考。")

    # 数据一致性警告：分数已停在基准日，但部分指标其实更新到了更新的日期。
    # 明示出来，让用户知道"为什么不是最新日期"，以及哪些数据被暂时丢弃。
    cons = report.get("consistency")
    if cons and not cons.get("consistent"):
        print()
        print(f"  ⚠ 数据不一致: 分数停在基准日 {cons['baseline']}（所有指标齐全的最后一天），")
        print(f"     但以下指标已更新到更新日期、暂未纳入（避免用残缺指标集算错）:")
        for item in cons["ahead"]:
            print(f"      - {item['indicator']}: 已到 {item['latest']}（超前 {item['lead_days']} 天）")
        print(f"     待最落后的指标更新到 {cons['newest']} 后，基准日会自动前移并重算。")
    print()


def run_cycle_signal_step() -> Dict:
    """生产链路 step：生成报告 → 出图 → 写 JSON。返回 report。"""
    report, index_data = build_cycle_report()

    # 出图（复用已验证的 plot_cycle_position，传入当日摘要做标题）
    summary = {
        "date": report.get("date"),
        "cycle_score": report.get("cycle_score"),
        "phase": report.get("phase"),
        "emoji": report.get("emoji"),
        "advice": report.get("advice"),
        "signal_state": report.get("signal_state"),
        "last_signal": report.get("last_signal"),
        "last_light_signal": report.get("last_light_signal"),
        "freshness": report.get("freshness"),
    }
    plot_cycle_position(
        report["score_series"],
        report["phases_df"],
        index_data,
        signals_df=report.get("signals_df"),
        report_summary=summary,
        emotion_score_series=report.get("emotion_score_series"),
    )

    # 写 JSON 摘要
    json_path = _write_signal_json(report)
    print(f"✓ 周期信号摘要已保存: {json_path}")

    _print_report_summary(report)

    return report
