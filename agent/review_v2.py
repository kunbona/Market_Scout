"""
复盘分析 (Review) v2 — 9 维度编排
- 调 quant/daily_compute.py 的 14 个 compute 函数 + review_compute.py 的 compute_daily_analysis
- 9 维度框架 (L0/L0.5/L1/L2/L3/L3.5/L4/L5/L6/L7), 剔除因子
- 落库 review_daily (INSERT OR REPLACE) + 渲染 HTML
- 无 LLM (纯确定性量化分析)
"""
import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# 9 维度 compute 函数映射 (L4 单独处理)
DAILY_COMPUTES = [
    ("L0",  "市场情绪",      "compute_market_emotion"),
    ("L0.5", "连板统计",      "compute_lianzban_stats"),
    ("L0.5", "连板链",        "compute_lianzban_chain"),
    ("L1",  "成交额统计",    "compute_turnover_stats"),
    ("L1",  "涨跌平",        "compute_advance_decline"),
    ("L2",  "市值分布",      "compute_market_cap_dist"),
    ("L3",  "板块涨停密度",  "compute_sector_zt_density"),
    ("L3",  "板块资金流加速", "compute_sector_flow_acceleration"),
    ("L3.5", "概念涨停密度",  "compute_concept_zt_density"),
    ("L5",  "筹码状态",      "compute_chip_status"),
    ("L5",  "板块筹码压力",  "compute_sector_chip_pressure"),
    ("L5",  "集合竞价",      "compute_call_auction_stats"),
    ("L5",  "板块竞价情绪",  "compute_sector_auction_sentiment"),
    ("L6",  "成交量突破",    "compute_volume_breakout"),
    ("L7",  "研报活跃",      "compute_research_activity"),
]


def run_daily_computes(trade_date: str) -> dict:
    """跑 14 个 daily_compute, 每个独立 try/except。返回 {layer: {task: ok/error}}"""
    from quant import daily_compute

    results = {}
    for layer, name, fn_name in DAILY_COMPUTES:
        fn = getattr(daily_compute, fn_name, None)
        if fn is None:
            results.setdefault(layer, {})[name] = f"missing"
            continue
        try:
            fn(trade_date)
            results.setdefault(layer, {})[name] = "ok"
        except Exception as e:
            results.setdefault(layer, {})[name] = f"error: {e}"
            logger.warning("[review] %s.%s failed: %s", layer, name, e)
    return results


def run_sector_effect(trade_date: str) -> dict | None:
    """跑 L4 板块效应 (单独慢任务), 失败不阻断"""
    from quant.review_compute import compute_daily_analysis
    try:
        data = compute_daily_analysis(trade_date)
        return data
    except Exception as e:
        logger.warning("[review] L4 sector effect failed: %s", e)
        return None


def collect_dimensions(trade_date: str) -> dict:
    """从各表读 9 维度汇总数据"""
    from db.storage import (
        get_market_emotion_summary,
        get_lianzban_stats,
        get_sector_zt_density,
        get_sector_flow_accel,
        get_concept_zt_density,
        get_turnover_stats,
        get_advance_decline,
        get_market_cap_dist,
        get_volume_breakout,
        get_chip_status,
        get_call_auction_stats,
        get_sector_chip_pressure,
        get_sector_auction_sentiment,
        get_research_activity,
    )

    dims = {"trade_date": trade_date}

    # L0 情绪周期
    try:
        dims["L0"] = get_market_emotion_summary(trade_date)
    except Exception as e:
        dims["L0"] = {"error": str(e)}

    # L0.5 连板统计 (近 30 天, 取当日)
    try:
        rows = get_lianzban_stats(days=30)
        today = next((r for r in rows if r.get("trade_date") == trade_date), None)
        dims["L0.5_lianzban"] = today or {"trade_date": trade_date, "empty": True}
    except Exception as e:
        dims["L0.5_lianzban"] = {"error": str(e)}

    # L1 宽度 + 成交额
    try:
        dims["L1_breadth"] = get_advance_decline(trade_date)
    except Exception as e:
        dims["L1_breadth"] = {"error": str(e)}
    try:
        dims["L1_turnover"] = get_turnover_stats(trade_date)
    except Exception as e:
        dims["L1_turnover"] = {"error": str(e)}

    # L2 风格
    try:
        dims["L2_style"] = get_market_cap_dist(trade_date)
    except Exception as e:
        dims["L2_style"] = {"error": str(e)}

    # L3 行业增强
    try:
        dims["L3_sector_density"] = get_sector_zt_density(trade_date)
    except Exception as e:
        dims["L3_sector_density"] = {"error": str(e), "rows": []}
    try:
        dims["L3_sector_flow"] = get_sector_flow_accel(trade_date)
    except Exception as e:
        dims["L3_sector_flow"] = {"error": str(e), "rows": []}

    # L3.5 概念
    try:
        dims["L3.5_concept"] = get_concept_zt_density(trade_date, top_n=10)
    except Exception as e:
        dims["L3.5_concept"] = {"error": str(e), "rows": []}

    # L5 筹码 + 竞价
    try:
        dims["L5_chip"] = get_chip_status(trade_date)
    except Exception as e:
        dims["L5_chip"] = {"error": str(e), "rows": []}
    try:
        dims["L5_chip_pressure"] = get_sector_chip_pressure(trade_date)
    except Exception as e:
        dims["L5_chip_pressure"] = {"error": str(e), "rows": []}
    # auction 返回 list (按股多行), 聚合成 summary
    try:
        rows = get_call_auction_stats(trade_date) or []
        if rows:
            high_open = sum(1 for r in rows if (r.get("auction_ratio") or 0) > 1.0 and (r.get("pct_chg_open") or 0) > 0)
            low_open = sum(1 for r in rows if (r.get("pct_chg_open") or 0) < 0)
            flat_open = len(rows) - high_open - low_open
            dims["L5_auction"] = {
                "total": len(rows),
                "high_open": high_open,
                "low_open": low_open,
                "flat_open": flat_open,
                "up_ratio": (high_open / len(rows) * 100) if rows else 0,
                "_raw_count": len(rows),
            }
        else:
            dims["L5_auction"] = {"total": 0, "high_open": 0, "low_open": 0, "flat_open": 0, "up_ratio": 0}
    except Exception as e:
        dims["L5_auction"] = {"error": str(e)}
    try:
        dims["L5_auction_sentiment"] = get_sector_auction_sentiment(trade_date)
    except Exception as e:
        dims["L5_auction_sentiment"] = {"error": str(e), "rows": []}

    # L6 突破 + 涨停股池
    try:
        dims["L6_breakout"] = get_volume_breakout(trade_date)
    except Exception as e:
        dims["L6_breakout"] = {"error": str(e), "rows": []}

    # L7 研报活跃
    try:
        dims["L7_research"] = get_research_activity(trade_date)
    except Exception as e:
        dims["L7_research"] = {"error": str(e), "rows": []}

    return dims


def synthesize_core(dims: dict, sector_data: dict | None) -> str:
    """规则化综合判断 (不调 LLM), 返回 1 句核心判断。

    规则:
    - 情绪阶段: zt > 50 = 修复/高潮, 30-50 = 常态, < 30 = 冰点
    - 宽度信号: MA20 上方占比 > 60% = 健康, 30-60% = 震荡, < 30% = 弱势
    - 板块轮动: 涨停密度 Top 3 行业, 标"新晋"/"掉队" (本次只展示 Top 3, 持续性靠人判断)
    - 资金切换: 资金流加速 Top 3 + 减速 Top 3
    """
    parts = []

    # 情绪阶段
    l0 = dims.get("L0") or {}
    if "error" not in l0:
        zt = l0.get("zt_total", 0)
        dt = l0.get("dt_total", 0)
        if zt >= 50:
            stage = "修复→高潮"
        elif zt >= 30:
            stage = "常态"
        else:
            stage = "冰点"
        parts.append(f"情绪{stage}(zt={zt}/dt={dt})")
    else:
        parts.append("情绪-暂无数据")

    # 宽度
    breadth = dims.get("L1_breadth") or {}
    if "error" not in breadth:
        ma20 = breadth.get("above_ma20_pct") or breadth.get("ma20_above_pct")
        if ma20 is not None:
            if ma20 >= 60:
                width = "健康"
            elif ma20 >= 30:
                width = "震荡"
            else:
                width = "弱势"
            parts.append(f"宽度{width}(MA20上{ma20:.0f}%)")
    # 成交额
    turnover = dims.get("L1_turnover") or {}
    if "error" not in turnover:
        total = turnover.get("total_turnover") or turnover.get("total")
        if total:
            parts.append(f"成交{total/1e8:.0f}亿")

    # 板块资金切换
    flow = dims.get("L3_sector_flow") or {}
    if isinstance(flow, list) and flow:
        top = flow[:3]
        bot = flow[-3:] if len(flow) > 3 else []
        if top:
            top_str = "/".join(f"{it.get('sector', '')}({it.get('flow_accel', 0):+.0f})" for it in top)
            parts.append(f"流入{top_str}")
        if bot:
            bot_str = "/".join(f"{it.get('sector', '')}({it.get('flow_accel', 0):+.0f})" for it in bot)
            parts.append(f"流出{bot_str}")

    if not parts:
        return "复盘数据缺失, 建议人工核查"
    return " | ".join(parts)


def render_html(dims: dict, sector_data: dict | None, compute_results: dict, trade_date: str) -> str:
    """渲染 9 维度 HTML 报告 (浅色主调)"""
    css = """
    body { font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f9fafb; color: #111; }
    .header { background: linear-gradient(135deg, #0f766e 0%, #14b8a6 100%); color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 16px; }
    .header h1 { margin: 0; font-size: 20px; }
    .header .sub { font-size: 12px; opacity: 0.85; margin-top: 4px; }
    .core { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); padding: 16px 22px; border-radius: 12px; border: 1px solid #f59e0b; margin-bottom: 16px; }
    .core-label { font-size: 12px; font-weight: 700; color: #92400e; margin-bottom: 6px; letter-spacing: 0.5px; }
    .core-text { font-size: 15px; line-height: 1.7; color: #1f2937; font-weight: 500; }
    .section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 12px; }
    .section h3 { margin: 0 0 12px 0; font-size: 15px; color: #1e293b; display: flex; align-items: center; gap: 6px; }
    .sec-icon { font-size: 16px; }
    .layer-tag { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #d1fae5; color: #047857; font-size: 11px; font-weight: 600; }
    .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
    .kpi-cell { background: #f9fafb; padding: 10px 12px; border-radius: 8px; }
    .kpi-cell .k { font-size: 11px; color: #6b7280; }
    .kpi-cell .v { font-size: 18px; font-weight: 700; color: #111; margin-top: 2px; }
    .kpi-cell .v.up { color: #dc2626; }
    .kpi-cell .v.down { color: #16a34a; }
    .tbl { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
    .tbl th, .tbl td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; }
    .tbl th { background: #f3f4f6; font-weight: 600; color: #374151; font-size: 11px; }
    .status-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; font-size: 11px; }
    .status-cell { padding: 6px 8px; border-radius: 6px; text-align: center; }
    .status-cell.ok { background: #d1fae5; color: #065f46; }
    .status-cell.err { background: #fee2e2; color: #991b1b; }
    .status-cell.miss { background: #f3f4f6; color: #6b7280; }
    .footer { color: #6b7280; font-size: 12px; margin-top: 16px; padding: 12px; border-top: 1px solid #e5e7eb; }
    """

    # 核心判断
    core = synthesize_core(dims, sector_data)

    h = ['<!DOCTYPE html><html><head><meta charset="utf-8"><title>复盘分析</title><style>', css,
         '</style></head><body>',
         f'<div class="header"><h1>📊 复盘分析 (Review) — {trade_date}</h1>',
         '<div class="sub">9 维度编排 · 剔除因子 · 纯本地日线 + 板块数据 · 无 LLM 量化复盘</div></div>',
         '<div class="core"><div class="core-label">▎综合判断 (规则化, 非 LLM)</div>',
         f'<div class="core-text">{core}</div></div>']

    # ── L0 情绪周期 ──
    l0 = dims.get("L0") or {}
    h.append('<div class="section"><h3><span class="sec-icon">🔥</span> L0 情绪周期 <span class="layer-tag">market_emotion</span></h3>')
    if "error" in l0:
        h.append(f'<div>暂无数据: {l0.get("error", "")}</div>')
    else:
        zt = l0.get("zt_total", 0)
        dt = l0.get("dt_total", 0)
        zb = l0.get("zb_total", 0)
        max_lb = l0.get("max_lianzban", 0)
        zb_rate = l0.get("zb_rate", 0)
        prem = l0.get("zt_yesterday_premium")
        real_zt = l0.get("real_zt", zt)
        h.append('<div class="kpi-grid">')
        for label, val, cls in [
            ("涨停", zt, ""), ("跌停", dt, ""),
            ("炸板", zb, ""), ("炸板率", f"{zb_rate*100:.1f}%" if zb_rate else "—", ""),
            ("最高板", f"{max_lb}B", ""),
            ("非一字涨停", real_zt, ""),
            ("昨涨停溢价", f"{prem:+.2f}%" if prem is not None else "—", "up" if prem and prem > 0 else "down"),
        ]:
            h.append(f'<div class="kpi-cell"><div class="k">{label}</div><div class="v {cls}">{val}</div></div>')
        h.append('</div>')
    h.append('</div>')

    # ── L0.5 涨停生态 ──
    lb = dims.get("L0.5_lianzban") or {}
    h.append('<div class="section"><h3><span class="sec-icon">🎯</span> L0.5 涨停生态 <span class="layer-tag">lianzban_stats</span></h3>')
    if lb.get("empty") or "error" in lb:
        h.append(f'<div>暂无数据</div>')
    else:
        # lianzban_stats 字段看实际表结构, 简单展示
        h.append(f'<pre style="font-size:11px;background:#f9fafb;padding:8px;border-radius:6px;overflow:auto">{json.dumps(lb, ensure_ascii=False, indent=1)[:600]}</pre>')
    h.append('</div>')

    # ── L1 指数层 + 宽度 + 成交额 ──
    h.append('<div class="section"><h3><span class="sec-icon">📈</span> L1 指数层 + 宽度 <span class="layer-tag">advance_decline + turnover</span></h3>')
    breadth = dims.get("L1_breadth") or {}
    turnover = dims.get("L1_turnover") or {}
    h.append('<div class="kpi-grid">')
    if "error" not in breadth:
        for k, label in [("above_ma5_pct", "MA5上"), ("above_ma10_pct", "MA10上"), ("above_ma20_pct", "MA20上"),
                          ("up_pct", "上涨占比"), ("median_pct", "中位收益%")]:
            v = breadth.get(k)
            if v is not None:
                h.append(f'<div class="kpi-cell"><div class="k">{label}</div><div class="v">{v:.1f}{"%" if k != "median_pct" else "%"}</div></div>')
    if "error" not in turnover:
        for k, label in [("total_turnover", "总成交(亿)"), ("sh_turnover", "沪市(亿)"), ("sz_turnover", "深市(亿)")]:
            v = turnover.get(k)
            if v is not None:
                v_yi = v / 1e8 if "turnover" in k else v
                h.append(f'<div class="kpi-cell"><div class="k">{label}</div><div class="v">{v_yi:.0f}</div></div>')
    h.append('</div>')
    h.append('</div>')

    # ── L2 风格层 ──
    h.append('<div class="section"><h3><span class="sec-icon">🎨</span> L2 风格层 <span class="layer-tag">market_cap_dist</span></h3>')
    style = dims.get("L2_style") or {}
    if "error" in style:
        h.append(f'<div>暂无数据</div>')
    else:
        h.append(f'<pre style="font-size:11px;background:#f9fafb;padding:8px;border-radius:6px;overflow:auto">{json.dumps(style, ensure_ascii=False, indent=1)[:600]}</pre>')
    h.append('</div>')

    # ── L3 行业增强 ──
    h.append('<div class="section"><h3><span class="sec-icon">🏭</span> L3 行业增强 <span class="layer-tag">sector_zt_density + sector_flow_accel</span></h3>')
    density = dims.get("L3_sector_density") or []
    flow = dims.get("L3_sector_flow") or []
    if isinstance(density, list) and density:
        h.append('<div style="font-size:12px;color:#6b7280;margin-bottom:4px">▎板块涨停密度 Top 10</div>')
        h.append('<table class="tbl"><thead><tr><th>#</th><th>行业</th><th>涨停</th><th>总数</th><th>密度</th></tr></thead><tbody>')
        for i, r in enumerate(density[:10], 1):
            h.append(f'<tr><td>{i}</td><td>{r.get("sector", "")}</td>'
                     f'<td>{r.get("zt_count", 0)}</td><td>{r.get("total_count", 0)}</td>'
                     f'<td>{r.get("zt_density", 0)*100:.1f}%</td></tr>')
        h.append('</tbody></table>')
    if isinstance(flow, list) and flow:
        h.append('<div style="font-size:12px;color:#6b7280;margin:8px 0 4px">▎板块资金流加速 Top 8 (含减速 Top 3)</div>')
        h.append('<table class="tbl"><thead><tr><th>#</th><th>行业</th><th>加速</th><th>方向</th></tr></thead><tbody>')
        for i, r in enumerate((flow[:5] + flow[-3:])[:8], 1):
            accel = r.get("flow_accel", 0) or 0
            direction = "🟢" if accel > 0 else "🔴"
            h.append(f'<tr><td>{i}</td><td>{r.get("sector", "")}</td>'
                     f'<td>{accel:+.2f}</td><td>{direction}</td></tr>')
        h.append('</tbody></table>')
    h.append('</div>')

    # ── L3.5 概念 ──
    h.append('<div class="section"><h3><span class="sec-icon">💡</span> L3.5 概念热度 <span class="layer-tag">concept_zt_density</span></h3>')
    concept = dims.get("L3.5_concept") or []
    if isinstance(concept, list) and concept:
        h.append('<table class="tbl"><thead><tr><th>#</th><th>概念</th><th>涨停</th><th>密度</th></tr></thead><tbody>')
        for i, r in enumerate(concept[:10], 1):
            h.append(f'<tr><td>{i}</td><td>{r.get("concept", "")}</td>'
                     f'<td>{r.get("zt_count", 0)}</td>'
                     f'<td>{r.get("zt_density", 0)*100 if r.get("zt_density") else 0:.1f}%</td></tr>')
        h.append('</tbody></table>')
    h.append('</div>')

    # ── L4 板块效应深度 ──
    h.append('<div class="section"><h3><span class="sec-icon">💰</span> L4 板块效应深度 <span class="layer-tag">analyze_sector_from_raw (review_compute)</span></h3>')
    if sector_data:
        h.append(f'<pre style="font-size:11px;background:#f9fafb;padding:8px;border-radius:6px;overflow:auto;max-height:300px">{json.dumps(sector_data, ensure_ascii=False, default=str)[:2000]}</pre>')
    else:
        h.append('<div>板块效应数据缺失 (compute_daily_analysis 失败)</div>')
    h.append('</div>')

    # ── L5 筹码 + 竞价 ──
    h.append('<div class="section"><h3><span class="sec-icon">🎰</span> L5 筹码 + 竞价 <span class="layer-tag">chip + call_auction</span></h3>')
    chip = dims.get("L5_chip") or []
    auction = dims.get("L5_auction") or {}
    if isinstance(chip, list) and chip:
        h.append('<div style="font-size:12px;color:#6b7280;margin-bottom:4px">▎筹码状态摘要 (Top 5 集中度)</div>')
        h.append('<table class="tbl"><thead><tr><th>代码</th><th>名称</th><th>集中度%</th><th>获利盘%</th></tr></thead><tbody>')
        for r in chip[:5]:
            h.append(f'<tr><td>{r.get("code", "")}</td><td>{r.get("name", "")}</td>'
                     f'<td>{r.get("concentration", 0):.1f}</td>'
                     f'<td>{r.get("profit_pct", 0):.1f}</td></tr>')
        h.append('</tbody></table>')
    if "error" not in auction and auction:
        h.append('<div class="kpi-grid">')
        for k, label in [("high_open", "高开家数"), ("low_open", "低开家数"),
                          ("flat_open", "平开家数"), ("up_ratio", "高开占比%")]:
            v = auction.get(k)
            if v is not None:
                h.append(f'<div class="kpi-cell"><div class="k">{label}</div><div class="v">{v}</div></div>')
        h.append('</div>')
    h.append('</div>')

    # ── L6 个股层 ──
    h.append('<div class="section"><h3><span class="sec-icon">🎯</span> L6 个股层 <span class="layer-tag">volume_breakout + zt_pool + dt_pool</span></h3>')
    breakout = dims.get("L6_breakout") or []
    if isinstance(breakout, list) and breakout:
        h.append(f'<div style="font-size:12px;color:#6b7280;margin-bottom:4px">▎成交量突破 (Top 5, 共 {len(breakout)} 条)</div>')
        h.append('<table class="tbl"><thead><tr><th>代码</th><th>名称</th><th>涨幅%</th><th>量比</th></tr></thead><tbody>')
        for r in breakout[:5]:
            h.append(f'<tr><td>{r.get("code", "")}</td><td>{r.get("name", "")}</td>'
                     f'<td>{r.get("pct_chg", 0):.1f}</td>'
                     f'<td>{r.get("vol_ratio", 0):.1f}</td></tr>')
        h.append('</tbody></table>')
    h.append('</div>')

    # ── L7 研报活跃 ──
    h.append('<div class="section"><h3><span class="sec-icon">📑</span> L7 研报活跃 <span class="layer-tag">research_activity</span></h3>')
    research = dims.get("L7_research") or []
    if isinstance(research, list) and research:
        h.append(f'<div style="font-size:12px;color:#6b7280;margin-bottom:4px">▎研报覆盖 Top 5 (共 {len(research)} 条)</div>')
        h.append('<table class="tbl"><thead><tr><th>代码</th><th>名称</th><th>研报数</th></tr></thead><tbody>')
        for r in research[:5]:
            h.append(f'<tr><td>{r.get("code", "")}</td><td>{r.get("name", "")}</td>'
                     f'<td>{r.get("report_count", 0)}</td></tr>')
        h.append('</tbody></table>')
    h.append('</div>')

    # ── compute 状态 ──
    h.append('<div class="section"><h3><span class="sec-icon">⚙️</span> 14 compute 任务状态</h3>')
    h.append('<div class="status-grid">')
    for layer, task, fn in DAILY_COMPUTES:
        status = compute_results.get(layer, {}).get(task, "miss")
        cls = "ok" if status == "ok" else ("err" if "error" in str(status) else "miss")
        short = f"L{layer}/{task[:4]}"
        h.append(f'<div class="status-cell {cls}" title="{fn}: {status}">{short}</div>')
    h.append('</div></div>')

    h.append(f'<div class="footer">方法: 9 维度 (L0 情绪 / L0.5 涨停生态 / L1 指数宽度 / L2 风格 / L3 行业 / L3.5 概念 / L4 板块效应 / L5 筹码竞价 / L6 个股 / L7 研报) | 剔除 L2.5 因子 ICIR | 数据: 14 个 daily_compute + 1 个 review_compute | 框架借鉴: dark-magician select-stock-pro 7 层<br>')
    h.append('<script>window.addEventListener("load", function(){parent.postMessage({type:"mra-report-height", height: document.body.scrollHeight}, "*")});</script>')
    h.append('</div></body></html>')
    return "".join(h)


def run(trade_date: str | None = None):
    """主入口: 14 compute + 1 sector + 收集 9 维度 + 渲染 + 落库"""
    from quant.review_compute import _latest_trade_date
    from db.storage import insert_review_v2_daily

    if trade_date is None:
        trade_date = _latest_trade_date()
    print(f"[review_v2] start trade_date={trade_date}", flush=True)
    # QUANT_DATA_ROOT 由 server._load_env_local() 注入, 此处只校验不设.
    if not os.environ.get("QUANT_DATA_ROOT"):
        raise FileNotFoundError("QUANT_DATA_ROOT 未设置 (server 启动时 .env.local 应已注入)")

    # Step 1: 跑 14 个 daily_compute
    print(f"[review_v2] running 14 daily_computes...", flush=True)
    compute_results = run_daily_computes(trade_date)
    n_ok = sum(1 for L in compute_results.values() for s in L.values() if s == "ok")
    n_err = sum(1 for L in compute_results.values() for s in L.values() if s != "ok")
    print(f"[review_v2] daily_computes: {n_ok} ok, {n_err} err", flush=True)

    # Step 1b: 跑 L4 板块效应 (慢任务)
    print(f"[review_v2] running L4 sector effect (slow, ~30-60s)...", flush=True)
    sector_data = run_sector_effect(trade_date)
    if sector_data:
        print(f"[review_v2] L4 sector: {len(sector_data)} top-level keys", flush=True)

    # Step 2: 收集 9 维度汇总
    print(f"[review_v2] collecting 9 dimensions...", flush=True)
    dims = collect_dimensions(trade_date)
    if sector_data:
        dims["L4_sector"] = sector_data  # L4 单独

    # Step 3: 综合判断 (在 render_html 内调用)
    # Step 4: 渲染 + 落库
    html = render_html(dims, sector_data, compute_results, trade_date)
    summary_text = synthesize_core(dims, sector_data)[:200]

    payload = {
        "trade_date": trade_date,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "compute_results": compute_results,
        "dimensions": {k: v for k, v in dims.items() if k != "trade_date"},
        "summary": summary_text,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    try:
        insert_review_v2_daily(trade_date, payload_json, html)
        print(f"[review_v2] review_v2_daily upserted for {trade_date}", flush=True)
    except Exception as e:
        print(f"[review_v2] insert failed: {e}", flush=True)

    # HTML 落 tmp
    run_id = os.environ.get("MRA_RUN_ID", "default")
    tmp_dir = Path(f"/tmp/mra-{run_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    html_path = tmp_dir / "review_report.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[review_v2] html={html_path}", flush=True)

    return {
        "trade_date": trade_date,
        "summary": summary_text,
        "n_ok": n_ok,
        "n_err": n_err,
        "html_path": str(html_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", default=None, help="收盘数据日期 (默认自动检测)")
    args = parser.parse_args()
    result = run(args.trade_date)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    main()
