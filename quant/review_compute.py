"""
板块效应分析 — 基于本地日线数据（stock-trading-data-pro/CSV）的每日复盘计算。

数据源：quant/loader.py 的 get_trading_data() / get_trading_data_batch()
分析逻辑：移植自 select-stock-pro 的 analyze_sector_from_raw.py
输出：JSON（非 Markdown），供前端 ReviewPage 展示
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

import quant.loader as _loader
from quant.loader import get_trading_data, _get_workers

logger = logging.getLogger(__name__)

# 回看天数
HISTORY_DAYS = 250


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _latest_trade_date() -> str:
    """从 loader 的 DATA_ROOT 找最新交易日（扫一个文件即可）。"""
    data_root = _loader.DATA_ROOT
    if not data_root or not data_root.exists():
        return _today_str()
    trading_dir = data_root / "stock-trading-data-pro"
    if not trading_dir.exists():
        return _today_str()
    # 取一个典型文件读最后一行日期
    for f in sorted(trading_dir.glob("sh*.csv")):
        try:
            df = pd.read_csv(f, encoding="gbk", skiprows=1)
            if "交易日期" in df.columns and not df.empty:
                return str(df["交易日期"].iloc[-1])[:10]
        except Exception:
            continue
    return _today_str()


def _load_all_stocks(target_date: str | None = None) -> pd.DataFrame:
    """批量加载全市场股票日线数据（排除北交所），返回合并后的 DataFrame。"""
    data_root = _loader.DATA_ROOT
    if not data_root or not data_root.exists():
        raise FileNotFoundError("QUANT_DATA_ROOT 未配置或路径不存在")

    trading_dir = data_root / "stock-trading-data-pro"
    files = sorted(trading_dir.glob("*.csv"))
    logger.info("[review] 扫描到 %d 个股票文件", len(files))

    frames: list[pd.DataFrame] = []
    skipped_bj = 0
    for fp in files:
        name = fp.name.lower()
        if name.startswith("bj"):
            skipped_bj += 1
            continue
        try:
            df = pd.read_csv(fp, encoding="gbk", skiprows=1)
        except Exception:
            continue
        if df.empty:
            continue
        if "交易日期" not in df.columns:
            continue
        df["交易日期"] = pd.to_datetime(df["交易日期"], errors="coerce")
        df = df.dropna(subset=["交易日期"]).sort_values("交易日期").tail(HISTORY_DAYS)
        frames.append(df)

    if not frames:
        raise RuntimeError("未成功读取任何股票数据")

    full = pd.concat(frames, ignore_index=True)
    logger.info("[review] 加载完成: %d 只股票, 跳过 %d 只北交所", len(frames), skipped_bj)

    if target_date:
        cutoff = pd.to_datetime(target_date)
        full = full[full["交易日期"] <= cutoff].copy()

    # 成交额 → 亿
    if "成交额" in full.columns:
        full["成交额(亿)"] = full["成交额"].fillna(0) / 1e8

    # 资金流列（万元 → 亿）
    money_cols = ["机构资金买入额", "机构资金卖出额", "大户资金买入额", "大户资金卖出额",
                  "散户资金买入额", "散户资金卖出额"]
    for mc in money_cols:
        if mc in full.columns:
            full[mc] = full[mc].fillna(0) / 10000  # 万元→亿

    # 主力净流入 = 机构+大户买入 - 机构+大户卖出（亿）
    has = lambda c: c in full.columns
    if has("机构资金买入额") and has("大户资金买入额"):
        full["主力净流入(亿)"] = (
            full["机构资金买入额"] + full["大户资金买入额"]
            - full["机构资金卖出额"] - full["大户资金卖出额"]
        )
    if has("散户资金买入额"):
        full["散户净流入(亿)"] = full["散户资金买入额"] - full["散户资金卖出额"]

    # 申万一级行业列
    for cand in ["新版申万一级行业名称", "申万一级行业"]:
        if cand in full.columns:
            full["行业"] = full[cand]
            break
    if "行业" not in full.columns:
        full["行业"] = "未知"

    # 指标计算
    if "收盘价" in full.columns:
        full["MA10"] = full.groupby("股票代码")["收盘价"].transform(
            lambda x: x.rolling(10, min_periods=1).mean()
        )
        full["MA20"] = full.groupby("股票代码")["收盘价"].transform(
            lambda x: x.rolling(20, min_periods=1).mean()
        )
        full["BIAS20"] = (full["收盘价"] - full["MA20"]) / full["MA20"] * 100

    if "成交额" in full.columns:
        full["Amount_MA5"] = full.groupby("股票代码")["成交额"].transform(
            lambda x: x.rolling(5, min_periods=1).mean()
        )
        full["量比"] = full["成交额"] / full["Amount_MA5"].replace(0, np.nan)

    # 涨跌幅
    if "前收盘价" in full.columns:
        full["涨跌幅"] = (full["收盘价"] / full["前收盘价"] - 1) * 100

    # 涨停判定
    full["涨停"] = False
    full["跌停"] = False
    for idx, row in full.iterrows():
        code = str(row.get("股票代码", ""))
        try:
            chg = row["涨跌幅"]
        except (KeyError, TypeError):
            continue
        if pd.isna(chg):
            continue
        if code.startswith("3") or code.startswith("688"):
            threshold = 20.0
        elif code.startswith("8") or code.startswith("4") or code.startswith("bj"):
            threshold = 30.0
        else:
            threshold = 10.0
        if chg >= threshold * 0.995:
            full.at[idx, "涨停"] = True
        if chg <= -threshold * 0.995:
            full.at[idx, "跌停"] = True

    return full


def _get_latest_df(full: pd.DataFrame) -> pd.DataFrame:
    """最新交易日所有个股的快照。"""
    latest_date = full["交易日期"].max()
    return full[full["交易日期"] == latest_date].copy()


def _get_period_df(full: pd.DataFrame, days: int) -> pd.DataFrame:
    """最近 N 个交易日的数据。"""
    dates = sorted(full["交易日期"].unique())
    if len(dates) <= days:
        return full.copy()
    cutoff = dates[-(days + 1)]
    return full[full["交易日期"] > cutoff].copy()


# ─── 分析函数 ─────────────────────────────────────────────────


def _market_overview(latest: pd.DataFrame) -> dict:
    total_amount = float(latest["成交额(亿)"].sum()) if "成交额(亿)" in latest.columns else 0.0
    main_net = float(latest["主力净流入(亿)"].sum()) if "主力净流入(亿)" in latest.columns else 0.0
    retail_net = float(latest["散户净流入(亿)"].sum()) if "散户净流入(亿)" in latest.columns else 0.0
    up_count = int((latest["涨跌幅"] > 0).sum()) if "涨跌幅" in latest.columns else 0
    down_count = int((latest["涨跌幅"] < 0).sum()) if "涨跌幅" in latest.columns else 0
    stock_count = len(latest)
    return {
        "total_amount_yi": round(total_amount, 2),
        "main_net_yi": round(main_net, 2),
        "retail_net_yi": round(retail_net, 2),
        "up_count": up_count,
        "down_count": down_count,
        "stock_count": stock_count,
    }


def _sector_flow(latest: pd.DataFrame) -> list[dict]:
    """行业资金流排名（主力净流入排序）。"""
    if "行业" not in latest.columns or "主力净流入(亿)" not in latest.columns:
        return []
    g = latest.groupby("行业").agg(
        主力净流入=("主力净流入(亿)", "sum"),
        散户净流入=("散户净流入(亿)", "sum") if "散户净流入(亿)" in latest.columns else ("主力净流入(亿)", lambda x: 0.0),
        成交额=("成交额(亿)", "sum"),
        平均涨跌幅=("涨跌幅", "mean") if "涨跌幅" in latest.columns else ("涨跌幅", lambda x: 0.0),
        家数=("股票代码", "count"),
    ).reset_index()
    g = g.sort_values("主力净流入", ascending=False)
    return [
        {
            "sector": row["行业"],
            "main_net_yi": round(float(row["主力净流入"]), 2),
            "retail_net_yi": round(float(row["散户净流入"]), 2),
            "amount_yi": round(float(row["成交额"]), 2),
            "avg_change_pct": round(float(row["平均涨跌幅"]), 2),
            "stock_count": int(row["家数"]),
        }
        for _, row in g.iterrows()
    ]


def _limit_analysis(latest: pd.DataFrame, period: pd.DataFrame) -> dict:
    """涨跌停分析。"""
    zu = latest[latest["涨停"] == True]
    zd = latest[latest["跌停"] == True]

    def _dist(df: pd.DataFrame, col: str) -> list[dict]:
        if "行业" not in df.columns:
            return []
        dist = df.groupby("行业").size().sort_values(ascending=False).head(10)
        return [{"sector": k, "count": int(v)} for k, v in dist.items()]

    trend_dates = sorted(period["交易日期"].unique())[-5:]
    zu_trend = []
    zd_trend = []
    for d in trend_dates:
        day = period[period["交易日期"] == d]
        zu_trend.append({"date": str(d)[:10], "count": int(day["涨停"].sum())})
        zd_trend.append({"date": str(d)[:10], "count": int(day["跌停"].sum())})

    return {
        "limit_up_count": int(len(zu)),
        "limit_down_count": int(len(zd)),
        "limit_up_sectors": _dist(zu, "行业"),
        "limit_down_sectors": _dist(zd, "行业"),
        "limit_up_trend": zu_trend,
        "limit_down_trend": zd_trend,
    }


def _market_cap_groups(latest: pd.DataFrame) -> list[dict]:
    """市值分组表现。"""
    if "总市值" not in latest.columns:
        return []
    bins = [
        ("大盘(>200亿)", 200e8, float("inf")),
        ("中盘(50-200亿)", 50e8, 200e8),
        ("小盘(<50亿)", 0, 50e8),
    ]
    results = []
    for label, lo, hi in bins:
        group = latest[(latest["总市值"] >= lo) & (latest["总市值"] < hi)]
        if group.empty:
            continue
        results.append({
            "group": label,
            "avg_change_pct": round(float(group["涨跌幅"].mean()), 2) if "涨跌幅" in group.columns else 0.0,
            "count": int(len(group)),
            "up_ratio": round(float((group["涨跌幅"] > 0).mean() * 100), 1) if "涨跌幅" in group.columns else 0.0,
            "amount_yi": round(float(group["成交额(亿)"].sum()), 2) if "成交额(亿)" in group.columns else 0.0,
        })
    return results


def _price_groups(latest: pd.DataFrame) -> list[dict]:
    if "收盘价" not in latest.columns:
        return []
    bins = [
        ("低价(<10元)", 0, 10),
        ("中低价(10-50)", 10, 50),
        ("中高价(50-100)", 50, 100),
        ("高价(>100元)", 100, float("inf")),
    ]
    results = []
    for label, lo, hi in bins:
        group = latest[(latest["收盘价"] >= lo) & (latest["收盘价"] < hi)]
        if group.empty:
            continue
        results.append({
            "group": label,
            "avg_change_pct": round(float(group["涨跌幅"].mean()), 2) if "涨跌幅" in group.columns else 0.0,
            "count": int(len(group)),
            "up_ratio": round(float((group["涨跌幅"] > 0).mean() * 100), 1) if "涨跌幅" in group.columns else 0.0,
            "amount_yi": round(float(group["成交额(亿)"].sum()), 2) if "成交额(亿)" in group.columns else 0.0,
        })
    return results


def _turnover_groups(latest: pd.DataFrame) -> list[dict]:
    if "换手率" not in latest.columns:
        return []
    bins = [
        ("低迷(<1%)", 0, 1),
        ("一般(1-3%)", 1, 3),
        ("较活(3-5%)", 3, 5),
        ("活跃(5-10%)", 5, 10),
        ("高度活跃(>10%)", 10, float("inf")),
    ]
    results = []
    for label, lo, hi in bins:
        group = latest[(latest["换手率"] >= lo) & (latest["换手率"] < hi)]
        if group.empty:
            continue
        results.append({
            "group": label,
            "avg_change_pct": round(float(group["涨跌幅"].mean()), 2) if "涨跌幅" in group.columns else 0.0,
            "count": int(len(group)),
            "amount_yi": round(float(group["成交额(亿)"].sum()), 2) if "成交额(亿)" in group.columns else 0.0,
        })
    return results


def _flow_trend(period: pd.DataFrame) -> list[dict]:
    """近5日主力 vs 散户资金流向。"""
    dates = sorted(period["交易日期"].unique())[-5:]
    result = []
    for d in dates:
        day = period[period["交易日期"] == d]
        main = float(day["主力净流入(亿)"].sum()) if "主力净流入(亿)" in day.columns else 0.0
        retail = float(day["散户净流入(亿)"].sum()) if "散户净流入(亿)" in day.columns else 0.0
        result.append({"date": str(d)[:10], "main_yi": round(main, 2), "retail_yi": round(retail, 2)})
    return result


def _huddle_ratio(period: pd.DataFrame) -> dict:
    """抱团度：Top5%成交额占总成交额比例 + 20日均值。"""
    if "成交额(亿)" not in period.columns:
        return {"current": 0.0, "ma20": 0.0, "trend": []}
    dates = sorted(period["交易日期"].unique())
    values = []
    for d in dates:
        day = period[period["交易日期"] == d]
        amounts = day["成交额(亿)"].sort_values(ascending=False)
        if amounts.empty:
            continue
        top_n = max(1, int(len(amounts) * 0.05))
        ratio = float(amounts.head(top_n).sum() / amounts.sum() * 100)
        values.append({"date": str(d)[:10], "ratio": round(ratio, 2)})
    current = values[-1]["ratio"] if values else 0.0
    ratios = [v["ratio"] for v in values]
    ma20 = round(float(np.mean(ratios[-20:])) if len(ratios) >= 20 else float(np.mean(ratios)), 2)
    return {"current": current, "ma20": ma20, "trend": values[-5:]}


def _style_analysis(latest: pd.DataFrame) -> dict:
    """风格研判：周期/价值/成长强势度。"""
    if "MA10" not in latest.columns or "收盘价" not in latest.columns or "行业" not in latest.columns:
        return {"mode": "未知", "sectors": []}

    # 申万一级风格分组
    cycle_sectors = ["银行", "钢铁", "有色金属", "煤炭", "石油石化", "基础化工", "机械设备", "建筑材料"]
    value_sectors = ["公用事业", "交通运输", "非银金融", "食品饮料", "家用电器", "建筑装饰", "纺织服饰"]
    growth_sectors = ["电子", "计算机", "通信", "电力设备", "国防军工", "医药生物", "传媒"]

    def _strength(sec: str) -> float:
        g = latest[latest["行业"] == sec]
        if g.empty:
            return 0.0
        return float((g["收盘价"] > g["MA10"]).mean() * 100)

    sectors = []
    for sec in sorted(latest["行业"].dropna().unique()):
        s = _strength(sec)
        if s > 0:
            sectors.append({"sector": sec, "strength": round(s, 2),
                            "style": "周期" if sec in cycle_sectors else "价值" if sec in value_sectors else "成长" if sec in growth_sectors else ""})

    sectors.sort(key=lambda x: -x["strength"])

    cycle_avg = float(np.mean([_strength(s) for s in cycle_sectors]))
    value_avg = float(np.mean([_strength(s) for s in value_sectors]))
    growth_avg = float(np.mean([_strength(s) for s in growth_sectors]))

    if growth_avg >= max(cycle_avg, value_avg):
        mode = "成长模式"
    elif value_avg >= cycle_avg:
        mode = "价值模式"
    else:
        mode = "周期模式"

    return {"mode": mode, "cycle_avg": round(cycle_avg, 2), "value_avg": round(value_avg, 2),
            "growth_avg": round(growth_avg, 2), "sectors": sectors}


def _sentiment_score(latest: pd.DataFrame) -> dict:
    """6维度情绪评分。"""
    dims = {}
    if "涨跌幅" in latest.columns:
        dims["上涨比例"] = round(float((latest["涨跌幅"] > 0).mean() * 100), 1)
    dims["涨停数量"] = int(latest["涨停"].sum()) if "涨停" in latest.columns else 0
    dims["跌停数量"] = int(latest["跌停"].sum()) if "跌停" in latest.columns else 0
    if "量比" in latest.columns:
        dims["成交活跃度"] = round(float(latest["量比"].median()), 2)
    if "主力净流入(亿)" in latest.columns:
        dims["主力态度"] = round(float(latest["主力净流入(亿)"].sum()), 2)
    if "行业" in latest.columns and "涨跌幅" in latest.columns:
        dims["行业广度"] = int(latest.groupby("行业")["涨跌幅"].mean().gt(0).sum())

    # 综合评分（简单加权）
    score = 50
    if dims.get("上涨比例", 0) > 50:
        score += 10
    elif dims.get("上涨比例", 0) < 20:
        score -= 10
    if dims.get("涨停数量", 0) > 100:
        score += 15
    elif dims.get("涨停数量", 0) < 20:
        score -= 15
    if dims.get("跌停数量", 0) > 50:
        score -= 15
    score = max(0, min(100, score))

    return {"score": score, "dimensions": dims}


# ─── 主编排 ──────────────────────────────────────────────────


def compute_daily_analysis(target_date: str | None = None) -> dict | None:
    """
    计算指定日期的板块效应分析。target_date 为 YYYY-MM-DD 格式，None 取最新。
    返回完整 JSON dict。
    """
    trade_date = target_date or _latest_trade_date()
    logger.info("[review] 开始计算 %s 的板块分析", trade_date)

    full = _load_all_stocks(trade_date)
    latest = _get_latest_df(full)
    period = _get_period_df(full, 5)
    actual_date = str(latest["交易日期"].iloc[0])[:10] if not latest.empty else trade_date

    result = {
        "trade_date": actual_date,
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overview": _market_overview(latest),
        "sector_flow": _sector_flow(latest),
        "limit_analysis": _limit_analysis(latest, period),
        "market_cap_groups": _market_cap_groups(latest),
        "price_groups": _price_groups(latest),
        "turnover_groups": _turnover_groups(latest),
        "flow_trend": _flow_trend(period),
        "huddle": _huddle_ratio(full),
        "style": _style_analysis(latest),
        "sentiment": _sentiment_score(latest),
    }
    logger.info("[review] 计算完成: %s, 概览=%s", actual_date, json.dumps(result["overview"], ensure_ascii=False))
    return result


# ─── 测试入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    data = compute_daily_analysis()
    if data:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
