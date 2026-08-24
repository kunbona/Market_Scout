"""
quant/industry_trend_daily.py — 行业趋势每日截面存档 + 多日对比

数据流(2026-08-21 设计, 用户批准):
    industry_ma_trend.py (50列截面, 原脚本不改动)
        → 本模块读取 CSV → 打 trade_date 标签
        → 与上一存档日对比生成"变化清单"(5类规则, 全部透明可解释)
        → 写入 SQLite industry_trend_daily 表 (照抄 review_daily 模式)
        → server.py /api/industry-trend/* 提供前端页面

变化标注规则:
    分类跃迁  分类 order 变动 (多头方向=↑ / 空头方向=↓)
    资金流Δ变  (本期Δ-上期Δ) >= +0.5pp 改善 / <= -0.5pp 恶化(⚠隐藏抛售)
    量能拐点  量能比5 穿越 1.2/0.8 或穿越慢档(量能比20日)
    警报增减  当日净占比 |v|>=3% 新增/解除
    四维共振⭐ 分类(前4档) + 资金流Δ>0 + 量能比5>量能比 + 广度20日Δ>0 四路同向

调用:
    run_industry_trend(trade_date=None, force_recompute=False)  → dict|None
        force_recompute=True 时以子进程重跑 industry_ma_trend.py --date
    (供 daily_compute 每日任务与 /api/industry-trend/run 重算按钮调用)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

QUANT = Path(__file__).parent
TREND_CSV = QUANT / "industry_ma_trend.csv"          # 默认输出(最新交易日)
SW_CACHE = QUANT / "data" / "sw_industry_index.parquet"

# 与 industry_ma_trend.py 排序 order 完全一致(勿单独改)
CAT_ORDER = {"强势多头": 0, "多头": 1, "多头回踩": 2, "筑底反转": 3, "筑底回踩": 4,
             "震荡粘合": 5, "震荡": 6, "空头反弹": 7, "空头": 8,
             "强势空头": 9, "数据不足": 10}
BULL_CATS = {"强势多头", "多头", "多头回踩", "筑底反转"}

FF_DELTA_CHG_TH = 0.5    # 资金流Δ 变化阈值(pp)
DAY_ALERT_TH = 3.0       # 当日净占比警报阈值(%)


# ── 交易日历: 以申万指数缓存的日期为准 ────────────────────────────────────────

def _sw_dates() -> list[str]:
    df = pd.read_parquet(SW_CACHE, columns=["date"])
    return sorted(d[:10] for d in pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").unique())


def resolve_trade_date(trade_date: str | None = None) -> str | None:
    """把请求日期归一到 <= 该日的最近交易日; None → 数据最大交易日。"""
    dates = _sw_dates()
    if not dates:
        return None
    if not trade_date:
        return dates[-1]
    d = trade_date.replace("-", "").strip()
    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else trade_date
    prior = [x for x in dates if x <= d]
    return prior[-1] if prior else None


# ── 缓存刷新: 复盘级联前先增量拉申万官方指数/资金流 ─────────────────────────

def refresh_sw_caches(timeout: int = 900, force: bool = False) -> bool:
    """增量刷新 sw_industry_index.parquet + sw_industry_fundflow.parquet。

    force=True: 跳过增量判断, 全量重拉(用于重算时缓存明显落后于目标日期)。
    为什么需要: 这两个缓存是 industry_ma_trend.py 的数据源, 也是 resolve_trade_date
    的交易日历。若靠手动拉取, 新交易日复盘级联时缓存还停在昨天, 日期会被归一到
    旧的一天 → 行业趋势"联动"静默失效。两个 fetch 脚本均为增量模式(已最新时
    仅 1 次 probe 调用即跳过, 秒级), 失败只告警不抛出(降级用旧缓存)。
    """
    kun_py = "/opt/anaconda3/envs/kun/bin/python"
    py = kun_py if Path(kun_py).exists() else sys.executable
    env = dict(os.environ)
    env.setdefault("QUANT_DATA_ROOT", "/Volumes/Vesta/AGdata_exodia")
    ok = True
    for script in ("fetch_sw_industry_index.py", "fetch_sw_industry_fundflow.py"):
        t0 = time.time()
        try:
            cmd = [py, str(QUANT / script)]
            if force:
                cmd.append("--refresh")
            res = subprocess.run(cmd, capture_output=True,
                                 text=True, timeout=timeout, cwd=str(QUANT), env=env)
            if res.returncode != 0:
                ok = False
                logger.warning("[industry-trend] %s 刷新失败: %s",
                               script, res.stderr[-400:] or res.stdout[-400:])
            else:
                logger.info("[industry-trend] %s 刷新完成 %.0fs: %s",
                            script, time.time() - t0,
                            (res.stdout or "").strip().splitlines()[-1] if res.stdout else "")
        except Exception as exc:
            ok = False
            logger.warning("[industry-trend] %s 刷新异常: %s", script, exc)
    return ok


# ── 重算: 子进程跑 industry_ma_trend.py (原脚本不动) ──────────────────────────

def _recompute_csv(trade_date: str, timeout: int = 900) -> Path:
    kun_py = "/opt/anaconda3/envs/kun/bin/python"
    py = kun_py if Path(kun_py).exists() else sys.executable
    out = str(QUANT / "tmp" / f"industry_trend_{trade_date}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("QUANT_DATA_ROOT", "/Volumes/Vesta/AGdata_exodia")
    env.setdefault("QUANT_WORKERS", "16")
    cmd = [py, str(QUANT / "industry_ma_trend.py"), "--date", trade_date, "--out", out]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                         cwd=str(QUANT), env=env)
    csv_path = Path(out + ".csv")
    if res.returncode != 0 or not csv_path.exists():
        raise RuntimeError(f"industry_ma_trend.py 重算失败({trade_date}): "
                           f"{res.stderr[-800:] or res.stdout[-800:]}")
    logger.info("[industry-trend] 重算完成 %s 耗时 %.0fs", trade_date, time.time() - t0)
    return csv_path


def _csv_for(trade_date: str, force_recompute: bool) -> Path:
    """取该交易日的 50 列截面 CSV: 默认输出命中则直接用, 否则子进程重算。"""
    if not force_recompute and TREND_CSV.exists():
        # 默认输出就是最新交易日时直接复用, 免重跑
        if resolve_trade_date(None) == trade_date:
            return TREND_CSV
    return _recompute_csv(trade_date)


# ── 变化清单 (对比上一存档日) ────────────────────────────────────────────────

def _num(v):
    return float(v) if v is not None and pd.notna(v) else None


def _batch_history(dates: list[str], cols: tuple[str, ...]) -> dict[str, dict[str, list[float]]]:
    """批量取多个存档日的指定列, 返回 {行业: {列: [值列表(按日期升序)]}}。

    用于 _diff_rows 算 5/20 日均值偏离(当日 vs 短期/中期均值)。
    """
    from db.storage import get_industry_trend_daily
    out: dict[str, dict[str, list[float]]] = {}
    for d in sorted(dates):
        row = get_industry_trend_daily(d)
        if not row:
            continue
        for r in json.loads(row["payload"]).get("table", []):
            ind = r.get("行业")
            if not ind:
                continue
            rec = out.setdefault(ind, {c: [] for c in cols})
            for c in cols:
                v = _num(r.get(c))
                if v is not None:
                    rec[c].append(v)
    return out


def _diff_rows(cur: list[dict], prev: list[dict],
               hist: dict[str, dict[str, list[float]]] | None = None) -> tuple[list[dict], dict]:
    prev_map = {r["行业"]: r for r in prev}
    changes = []
    for r in cur:
        ind = r["行业"]
        p = prev_map.get(ind)
        if not p:
            continue
        # 1) 分类跃迁
        c_now, c_prev = r.get("分类"), p.get("分类")
        if c_now != c_prev and "数据不足" not in (c_now, c_prev):
            up = CAT_ORDER.get(c_now, 10) < CAT_ORDER.get(c_prev, 10)
            changes.append({"行业": ind, "类型": "分类跃迁", "方向": "up" if up else "down",
                            "上期": c_prev, "本期": c_now,
                            "说明": "升档: 底部结构修复" if up else "降档: 趋势走弱"})
        # 2) 资金流Δ 变化
        d_now, d_prev = _num(r.get("资金流Δ")), _num(p.get("资金流Δ"))
        if d_now is not None and d_prev is not None:
            chg = round(d_now - d_prev, 2)
            if chg >= FF_DELTA_CHG_TH:
                changes.append({"行业": ind, "类型": "资金流改善", "方向": "up",
                                "上期": d_prev, "本期": d_now, "变化": chg,
                                "说明": f"资金流Δ改善 {chg:+.2f}pp"})
            elif chg <= -FF_DELTA_CHG_TH:
                changes.append({"行业": ind, "类型": "资金流恶化", "方向": "down",
                                "上期": d_prev, "本期": d_now, "变化": chg,
                                "说明": f"资金流Δ恶化 {chg:+.2f}pp(隐藏抛售)"})
        # 3) 量能拐点
        f_now, f_prev = _num(r.get("量能比5")), _num(p.get("量能比5"))
        s_now, s_prev = _num(r.get("量能比")), _num(p.get("量能比"))
        if f_now is not None and f_prev is not None:
            if f_prev < 1.2 <= f_now:
                changes.append({"行业": ind, "类型": "量能拐点", "方向": "up",
                                "上期": f_prev, "本期": f_now, "说明": "量能比5 上穿1.2(放量启动)"})
            elif f_prev >= 0.8 > f_now:
                changes.append({"行业": ind, "类型": "量能拐点", "方向": "down",
                                "上期": f_prev, "本期": f_now, "说明": "量能比5 跌破0.8(缩量退潮)"})
            elif (f_prev is not None and s_prev is not None and s_now is not None
                  and f_prev <= s_prev and f_now > s_now):
                changes.append({"行业": ind, "类型": "量能拐点", "方向": "up",
                                "上期": f_prev, "本期": f_now, "说明": "量能快档上穿慢档"})
            elif (f_prev is not None and s_prev is not None and s_now is not None
                  and f_prev > s_prev and f_now <= s_now):
                changes.append({"行业": ind, "类型": "量能拐点", "方向": "down",
                                "上期": f_prev, "本期": f_now, "说明": "量能快档下穿慢档"})
        # 4) 当日警报增减
        a_now, a_prev = _num(r.get("当日净占比%")), _num(p.get("当日净占比%"))
        if a_now is not None and abs(a_now) >= DAY_ALERT_TH:
            if a_prev is None or abs(a_prev) < DAY_ALERT_TH:
                changes.append({"行业": ind, "类型": "新增警报", "方向": "up" if a_now > 0 else "down",
                                "上期": a_prev, "本期": a_now,
                                "说明": f"当日净占比 {a_now:+.1f}% 触发警报(连续2日同向才算拐点)"})
            elif a_prev * a_now < 0:
                # 方向反转: 上期有警报且符号相反(如 +4.7% → -4.5%), 不是"新增"也不是"解除"
                changes.append({"行业": ind, "类型": "警报反转", "方向": "down" if a_now < 0 else "up",
                                "上期": a_prev, "本期": a_now,
                                "说明": f"当日净占比方向反转 {a_prev:+.1f}% → {a_now:+.1f}%(主力态度逆转)"})
        elif a_prev is not None and abs(a_prev) >= DAY_ALERT_TH:
            changes.append({"行业": ind, "类型": "警报解除", "方向": "up",
                            "上期": a_prev, "本期": a_now, "说明": "上一期警报已解除"})

        # 5) 当日偏离 5/20 日均值 (时效性: 当日是否异常偏离短期/中期趋势)
        #    偏离超 2pp 才报, 避免噪声。hist 无数据时跳过。
        if hist and ind in hist:
            h = hist[ind]
            def _dev(col_now, col_hist, label, unit="pp"):
                v_now = _num(r.get(col_now))
                if v_now is None:
                    return
                h5 = h.get(col_hist, [])[-5:]
                h20 = h.get(col_hist, [])[-20:]
                if len(h5) >= 3:
                    m5 = sum(h5) / len(h5)
                    dev = round(v_now - m5, 2)
                    if abs(dev) >= 2:
                        changes.append({"行业": ind, "类型": f"{label}偏离5日", "方向": "up" if dev > 0 else "down",
                                        "上期": round(m5, 2), "本期": v_now, "变化": dev,
                                        "说明": f"{label} {v_now:+.1f}{unit} 偏离5日均值 {m5:+.1f}{unit} ({dev:+.1f}{unit})"})
                if len(h20) >= 10:
                    m20 = sum(h20) / len(h20)
                    dev = round(v_now - m20, 2)
                    if abs(dev) >= 2:
                        changes.append({"行业": ind, "类型": f"{label}偏离20日", "方向": "up" if dev > 0 else "down",
                                        "上期": round(m20, 2), "本期": v_now, "变化": dev,
                                        "说明": f"{label} {v_now:+.1f}{unit} 偏离20日均值 {m20:+.1f}{unit} ({dev:+.1f}{unit})"})
            _dev("当日净占比%", "当日净占比%", "净占比")
            _dev("资金流Δ", "资金流Δ", "资金流Δ")
            _dev("当日涨幅%", "当日涨幅%", "当日涨幅", "%")

    # 变化排序: 跃迁/反转优先, 然后资金流/量能, 最后偏离均值
    prio = {"分类跃迁": 0, "警报反转": 1, "资金流恶化": 2, "资金流改善": 3, "量能拐点": 4,
            "新增警报": 5, "警报解除": 6,
            "净占比偏离5日": 7, "净占比偏离20日": 8,
            "资金流Δ偏离5日": 9, "资金流Δ偏离20日": 10,
            "当日涨幅偏离5日": 11, "当日涨幅偏离20日": 12}
    changes.sort(key=lambda c: (prio.get(c["类型"], 9), c["方向"] == "up"))
    return changes, prev_map


def _build_summary(rows: list[dict], changes: list[dict], prev_map: dict) -> dict:
    cats = pd.Series([r["分类"] for r in rows]).value_counts()
    prev_cats = pd.Series([p["分类"] for p in prev_map.values()]).value_counts()
    up_cnt = sum(1 for c in changes if c["类型"] == "分类跃迁" and c["方向"] == "up")
    dn_cnt = sum(1 for c in changes if c["类型"] == "分类跃迁" and c["方向"] == "down")

    # 四维共振(当日截面状态, 非变化)
    resonance = []
    for r in rows:
        if (r.get("分类") in BULL_CATS
                and (_num(r.get("资金流Δ")) or 0) > 0
                and (_num(r.get("量能比5")) or 0) > (_num(r.get("量能比")) or 1)
                and (_num(r.get("广度20日Δ")) or 0) > 0):
            resonance.append(r["行业"])

    deltas = sorted((r for r in rows if _num(r.get("资金流Δ")) is not None),
                    key=lambda r: _num(r.get("资金流Δ")))
    worsen = [(r["行业"], _num(r["资金流Δ"])) for r in deltas[:3]]
    improve = [(r["行业"], _num(r.get("资金流Δ"))) for r in reversed(deltas[-3:])]

    alerts = sorted((r for r in rows
                     if _num(r.get("当日净占比%")) is not None
                     and abs(_num(r.get("当日净占比%"))) >= DAY_ALERT_TH),
                    key=lambda r: -abs(_num(r.get("当日净占比%"))))

    headline = (f"多头类{cats.get('强势多头', 0) + cats.get('多头', 0) + cats.get('多头回踩', 0)}"
                f"·筑底类{cats.get('筑底反转', 0) + cats.get('筑底回踩', 0)}"
                f"·空头类{cats.get('空头', 0) + cats.get('强势空头', 0)}"
                f" | 跃迁↑{up_cnt}↓{dn_cnt}"
                + (f" | 共振:{'+'.join(resonance)}" if resonance else "")
                + (f" | 恶化之最:{worsen[0][0]}{worsen[0][1]:+.1f}pp" if worsen else ""))
    return {"cat_counts": cats.to_dict(),
            "prev_cat_counts": prev_cats.to_dict(),
            "upgrades": up_cnt, "downgrades": dn_cnt,
            "resonance": resonance,
            "ff_worsen_top3": worsen, "ff_improve_top3": improve,
            "day_alerts": [[r["行业"], _num(r.get("当日净占比%"))] for r in alerts],
            "headline": headline}


# ── 主入口 ──────────────────────────────────────────────────────────────────

def run_industry_trend(trade_date: str | None = None,
                       force_recompute: bool = False) -> dict | None:
    """计算一个交易日的行业趋势截面并存档, 返回完整 payload(含变化清单)。

    指定日期 + force_recompute(重算按钮路径) 时做**严格日期校验**:
    缓存里若没有该日期(数据源还没更新到这天), 直接报错——
    绝不静默归一到旧日期算一份"看起来成功"但日期错误的结果。
    """
    from db.storage import get_industry_trend_daily, insert_industry_trend_daily

    td = resolve_trade_date(trade_date)
    if not td:
        logger.warning("[industry-trend] 无法解析交易日: %s", trade_date)
        return None

    # 硬校验: 指定了日期且强制重算时, 归一结果必须 == 请求日期。
    # 否则说明缓存(交易日历)还没更新到该日 → 先强制刷新缓存再试一次, 仍不行才报错。
    if trade_date and force_recompute:
        req = trade_date.replace("-", "")
        req = f"{req[:4]}-{req[4:6]}-{req[6:8]}" if len(req) == 8 else trade_date
        if td != req:
            # 缓存落后 → 强制全量刷新(跳过增量判断, 直接拉最新)
            refresh_sw_caches(force=True)
            td = resolve_trade_date(trade_date)  # 刷新后重新解析
            if td != req:
                latest = resolve_trade_date(None)
                raise RuntimeError(
                    f"数据源尚未更新到 {req}（强制刷新后申万指数缓存最新仍仅 {latest}）。"
                    f"接口数据可能尚未同步, 请稍后再试；本次未按 {latest} 冒充生成。")

    csv_path = _csv_for(td, force_recompute)
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    rows = json.loads(df.to_json(orient="records", force_ascii=False))

    # 上一存档日(严格早于本日)的存档做对比
    # 注意 get_industry_trend_dates 返回 DESC(最新在前), 取 max() 而不是 dates[-1]
    from db.storage import get_industry_trend_dates
    dates = [d for d in get_industry_trend_dates() if d < td]
    prev_date = max(dates) if dates else None
    changes, prev_map = [], {}
    if prev_date:
        prev_payload = get_industry_trend_daily(prev_date)
        if prev_payload:
            prev_rows = json.loads(prev_payload["payload"]).get("table", [])
            # 拉历史存档算 5/20 日均值偏离(当日净占比/资金流Δ/当日涨幅)
            hist_dates = [d for d in get_industry_trend_dates() if d < td][-20:]
            hist = _batch_history(hist_dates, ("当日净占比%", "资金流Δ", "当日涨幅%")) if hist_dates else None
            changes, prev_map = _diff_rows(rows, prev_rows, hist=hist)

    payload = {"trade_date": td, "prev_date": prev_date,
               "cols": list(df.columns),
               "summary": _build_summary(rows, changes, prev_map),
               "changes": changes,
               "table": rows}
    insert_industry_trend_daily(td, json.dumps(payload, ensure_ascii=False))
    logger.info("[industry-trend] 存档 %s (变化%d条, 上期=%s)", td, len(changes), prev_date)
    return payload


def get_industry_series(industry: str, days: int = 30) -> list[dict]:
    """从存档聚合单行业关键指标时序(供前端折线图)。"""
    from db.storage import get_industry_trend_dates, get_industry_trend_daily
    keep = ("资金流Δ", "量能比", "量能比5", "广度%", "广度20日Δ",
            "20日涨幅", "当日净占比%", "分类", "趋势强度")
    out = []
    for d in get_industry_trend_dates()[-days:]:
        row = get_industry_trend_daily(d)
        if not row:
            continue
        rec = next((r for r in json.loads(row["payload"]).get("table", [])
                    if r.get("行业") == industry), None)
        if rec:
            out.append({"date": d, **{k: rec.get(k) for k in keep}})
    return out


def get_rps_heatmap(days: int = 40) -> dict:
    """行业 RPS(相对强度)热力图: 日期 × 行业。

    RPS = 行业 N 日累计等权涨幅在全市场 31 个行业中的排名百分位 (0-100)。
    三档: 当日 / 5日 / 20日。返回 {dates, industries, rps: {当日: [...], 5日: [...], 20日: [...]}, latest}
    """
    from db.storage import get_industry_trend_dates, get_industry_trend_daily
    dates = sorted(get_industry_trend_dates()[:days])
    if not dates:
        return {"dates": [], "industries": [], "rps": {}, "latest": {}}

    # 收集每日各行业等权涨幅 + 5日/20日累计涨幅
    day_ret: dict[str, dict[str, float]] = {}   # {日期: {行业: 当日等权涨幅%}}
    day_ret5: dict[str, dict[str, float]] = {}  # {日期: {行业: 5日涨幅%}}
    day_ret20: dict[str, dict[str, float]] = {} # {日期: {行业: 20日涨幅%}}
    for d in dates:
        row = get_industry_trend_daily(d)
        if not row:
            continue
        ret_map, ret5_map, ret20_map = {}, {}, {}
        for r in json.loads(row["payload"]).get("table", []):
            ind = r.get("行业")
            if not ind:
                continue
            v = r.get("当日等权涨幅%")
            v5 = r.get("5日涨幅")
            v20 = r.get("20日涨幅")
            if v is not None:
                ret_map[ind] = float(v)
            if v5 is not None:
                ret5_map[ind] = float(v5)
            if v20 is not None:
                ret20_map[ind] = float(v20)
        if ret_map:
            day_ret[d] = ret_map
        if ret5_map:
            day_ret5[d] = ret5_map
        if ret20_map:
            day_ret20[d] = ret20_map

    # 每日 RPS: 该日 31 个行业涨幅排名百分位
    def _rps(day_map):
        out = {}
        for d, ret_map in day_map.items():
            s = pd.Series(ret_map)
            rps = (s.rank(pct=True) * 100).round(1)
            for ind, v in rps.items():
                out.setdefault(ind, {})[d] = float(v)
        return out

    rps_1d = _rps(day_ret)
    rps_5d = _rps(day_ret5)
    rps_20d = _rps(day_ret20)

    # 量比数据: 每日各行业量能比5(快档), 用于异动箭头判断(量比>1.5加粗)
    day_vol: dict[str, dict[str, float]] = {}
    for d in dates:
        row = get_industry_trend_daily(d)
        if not row:
            continue
        vol_map = {}
        for r in json.loads(row["payload"]).get("table", []):
            ind = r.get("行业")
            v = r.get("量能比5")
            if ind and v is not None:
                vol_map[ind] = float(v)
        if vol_map:
            day_vol[d] = vol_map

    # 行业顺序: 按最新日 5日RPS 降序(兼顾短期+平滑)
    latest_date = dates[-1]
    latest = {ind: rps_5d.get(ind, {}).get(latest_date) for ind in rps_5d}
    latest = dict(sorted(latest.items(), key=lambda x: x[1] or 0, reverse=True))
    industries = list(latest.keys())

    matrix_1d = [[rps_1d.get(ind, {}).get(d) for d in dates] for ind in industries]
    matrix_5d = [[rps_5d.get(ind, {}).get(d) for d in dates] for ind in industries]
    matrix_20d = [[rps_20d.get(ind, {}).get(d) for d in dates] for ind in industries]
    matrix_vol = [[day_vol.get(d, {}).get(ind) for d in dates] for ind in industries]

    return {"dates": dates, "industries": industries,
            "rps": {"当日": matrix_1d, "5日": matrix_5d, "20日": matrix_20d},
            "vol": matrix_vol,
            "latest": {k: v for k, v in latest.items() if v is not None}}


def get_score_heatmap(days: int = 20) -> dict:
    """综合分热力图数据: 日期 × 行业 矩阵。

    返回 {dates: [...], industries: [...], scores: [[...], ...], latest: {行业: 综合分}}
    scores[i][j] = 第 i 个行业在第 j 个日期的综合分 (None=该日无数据)。
    行业顺序按最新日综合分降序。
    """
    from db.storage import get_industry_trend_dates, get_industry_trend_daily
    dates = get_industry_trend_dates()[:days]
    dates = sorted(dates)
    if not dates:
        return {"dates": [], "industries": [], "scores": [], "latest": {}}

    # 收集所有行业 + 每日综合分
    ind_scores: dict[str, dict[str, float]] = {}   # {行业: {日期: 综合分}}
    for d in dates:
        row = get_industry_trend_daily(d)
        if not row:
            continue
        for r in json.loads(row["payload"]).get("table", []):
            ind = r.get("行业")
            score = r.get("综合分")
            if ind and score is not None:
                ind_scores.setdefault(ind, {})[d] = float(score)

    # 行业顺序: 按最新日综合分降序
    latest_date = dates[-1]
    latest = {ind: scores.get(latest_date) for ind, scores in ind_scores.items()}
    # latest 按综合分降序排列(dict 保序), 供前端直接取 top
    latest = dict(sorted(latest.items(), key=lambda x: x[1] or 0, reverse=True))
    industries = sorted(ind_scores.keys(),
                        key=lambda x: latest.get(x) or 0, reverse=True)

    # 矩阵: scores[i][j] = 第 i 行业在第 j 日期
    matrix = []
    for ind in industries:
        row_scores = [ind_scores[ind].get(d) for d in dates]
        matrix.append(row_scores)

    return {"dates": dates, "industries": industries, "scores": matrix,
            "latest": {k: v for k, v in latest.items() if v is not None}}


# ─── 板块类别映射: 申万一级 31 行业 → 6 大投资类别 (与前端 INDUSTRY_CATEGORY 同步) ───
INDUSTRY_CATEGORY = {
    '电子': '科技TMT', '计算机': '科技TMT', '通信': '科技TMT', '传媒': '科技TMT',
    '电力设备': '先进制造', '国防军工': '先进制造', '汽车': '先进制造', '机械设备': '先进制造',
    '食品饮料': '大消费', '家用电器': '大消费', '农林牧渔': '大消费', '纺织服饰': '大消费',
    '轻工制造': '大消费', '商贸零售': '大消费', '社会服务': '大消费', '美容护理': '大消费',
    '医药生物': '大消费',
    '煤炭': '周期资源', '石油石化': '周期资源', '有色金属': '周期资源', '钢铁': '周期资源',
    '基础化工': '周期资源', '建筑材料': '周期资源', '建筑装饰': '周期资源',
    '银行': '金融地产', '非银金融': '金融地产', '房地产': '金融地产',
    '公用事业': '稳定公用', '交通运输': '稳定公用', '环保': '稳定公用', '综合': '稳定公用',
}
CATEGORY_ORDER = ['科技TMT', '先进制造', '大消费', '周期资源', '金融地产', '稳定公用']


def get_category_rotation(days: int = 60) -> dict:
    """6 大板块类别轮动时序: 每日期 × 每类别 4 个指标均值。

    指标:
      综合分    类别内行业综合分均值 (0-100, 越高越强)
      当日涨幅% 类别内行业当日等权涨幅均值
      5日RPS    类别内行业 5日涨幅排名百分位(0-100) 均值
      20日RPS   类别内行业 20日涨幅排名百分位(0-100) 均值
    返回 {dates, categories, metrics: {指标: {类别: [按dates对齐的值]}}, cat_counts}。
    单次循环读每日存档, 避免重复 IO。
    """
    from db.storage import get_industry_trend_dates, get_industry_trend_daily
    dates = sorted(get_industry_trend_dates()[:days])
    if not dates:
        return {"dates": [], "categories": CATEGORY_ORDER, "metrics": {}, "cat_counts": {}}

    m_score: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    m_day: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    m_rps5: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    m_rps20: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    cat_counts = {c: 0 for c in CATEGORY_ORDER}
    for c in CATEGORY_ORDER:
        cat_counts[c] = sum(1 for v in INDUSTRY_CATEGORY.values() if v == c)

    def _rank_pct(vals: dict[str, float]) -> dict[str, float]:
        s = pd.Series(vals)
        r = s.rank(pct=True) * 100
        return r.to_dict()

    for d in dates:
        row = get_industry_trend_daily(d)
        score_acc: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        day_acc: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        ret5: dict[str, float] = {}
        ret20: dict[str, float] = {}
        if row:
            for r in json.loads(row["payload"]).get("table", []):
                ind = r.get("行业")
                cat = INDUSTRY_CATEGORY.get(ind)
                if not cat:
                    continue
                sc = r.get("综合分")
                if sc is not None:
                    score_acc[cat].append(float(sc))
                dr = r.get("当日等权涨幅%")
                if dr is not None:
                    day_acc[cat].append(float(dr))
                v5 = r.get("5日涨幅")
                if v5 is not None:
                    ret5[ind] = float(v5)
                v20 = r.get("20日涨幅")
                if v20 is not None:
                    ret20[ind] = float(v20)
        rps5 = _rank_pct(ret5) if len(ret5) >= 2 else {}
        rps20 = _rank_pct(ret20) if len(ret20) >= 2 else {}
        r5_acc: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        r20_acc: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        for ind, cat in INDUSTRY_CATEGORY.items():
            if ind in rps5:
                r5_acc[cat].append(rps5[ind])
            if ind in rps20:
                r20_acc[cat].append(rps20[ind])
        for c in CATEGORY_ORDER:
            m_score[c].append(round(sum(score_acc[c]) / len(score_acc[c]), 1) if score_acc[c] else None)
            m_day[c].append(round(sum(day_acc[c]) / len(day_acc[c]), 2) if day_acc[c] else None)
            m_rps5[c].append(round(sum(r5_acc[c]) / len(r5_acc[c]), 1) if r5_acc[c] else None)
            m_rps20[c].append(round(sum(r20_acc[c]) / len(r20_acc[c]), 1) if r20_acc[c] else None)

    return {
        "dates": dates,
        "categories": CATEGORY_ORDER,
        "metrics": {"综合分": m_score, "当日涨幅%": m_day, "5日RPS": m_rps5, "20日RPS": m_rps20},
        "cat_counts": cat_counts,
    }


def get_industry_kline(industry: str, days: int = 1600,
                       end_date: str | None = None) -> dict | None:
    """单行业申万官方指数日K + 均线 + 1/3/5年位置高低点 (前端 K 线图数据源)。

    口径与 industry_ma_trend.py 完全一致 (用户红线: 勿单独改):
    - 均线: MA5/10/20/60/120, 官方指数原始收盘价 rolling mean
    - 位置: 末值收盘 vs 最近 250/750/1320 交易日窗口**收盘价**高低点 (0~1)
    - end_date 传入存档日时截断到该日, 与当日截面表的位置%严格一致
      (看历史存档时 K 线/位置不会"偷看未来")。
    """
    df = pd.read_parquet(SW_CACHE)
    df = df[df["name"] == industry]
    if df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
        if df.empty:
            return None
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)

    ma = {f"ma{n}": close.rolling(n).mean().round(2) for n in (5, 10, 20, 60, 120)}

    pos, levels = {}, {}
    for key, win in (("y1", 250), ("y3", 750), ("y5", 1320)):
        w = close.tail(win)
        if len(w) < int(win * 0.8):   # 数据不足该窗口(与脚本 min_periods 口径一致)
            continue
        hi, lo = float(w.max()), float(w.min())
        hi_d = df.loc[w.idxmax(), "date"].strftime("%Y-%m-%d")
        lo_d = df.loc[w.idxmin(), "date"].strftime("%Y-%m-%d")
        last = float(close.iloc[-1])
        rng = hi - lo
        pos[key] = round((last - lo) / rng * 100, 1) if rng > 0 else None
        levels[key] = {"hi": round(hi, 1), "hi_date": hi_d,
                       "lo": round(lo, 1), "lo_date": lo_d,
                       "dd": round((last / hi - 1) * 100, 1),   # 距高点回撤%
                       "rb": round((last / lo - 1) * 100, 1)}   # 距低点反弹%

    n = min(days, len(df))
    tail = df.tail(n)
    # MA 需要全量序列预热后再截尾, 否则前 119 根 MA120 全空。
    # NaN 必须转 None: json.dumps 默认把 NaN 写成非法 JSON 的裸 NaN, 前端 JSON.parse 直接炸
    ma_tail = {k: [None if (x is None or pd.isna(x)) else float(x)
                   for x in v.tail(n).tolist()]
               for k, v in ma.items()}
    return {
        "industry": industry,
        "end_date": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "dates": [d.strftime("%Y-%m-%d") for d in tail["date"]],
        "kline": tail[["open", "close", "low", "high"]].round(2).values.tolist(),
        "amount": tail["amount"].round(0).tolist(),   # 成交额(元), 前端画柱
        "ma": ma_tail,
        "pos": pos,           # {"y1": 62.0, "y3": 48.0, "y5": 40.0} 0~100
        "levels": levels,     # 各周期高点/低点(收盘口径)+日期+距高距低
    }
