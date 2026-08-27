"""
总览 AI 分析 (Master View): 汇总系统内全部数据源 → 一份全景 digest →
claude 做顶层交叉分析 → 落 agent_summary 表 (run_type=master_view)。

与 review_ai 的区别:
  review_ai  = 当日复盘视角 (复盘 9 维 + DM-kun 6 tab)
  master_view = 全局决策视角, 在复盘基础上再叠加:
    1. 行业趋势截面 (industry_trend_daily, 31 个申万一级行业的九态分类/
       位置/资金流/广度/形态, 含跨日跃迁对比)
    2. 既有 AI 结论 (最近一次盘后 chief 裁决 / info_brief / strategist),
       用来做"AI 历史判断 vs 当下数据"的对照
    3. 板块资金流最新截面 (净流入/流出前 8)
    4. 智堡(海外投研)AI 分析存档 (综合日报 + 单篇深度分析)
    5. 龙虎榜三层快照 (全市场聚合情绪/净买卖前5/背离样本/反复上榜主战场)
    6. 概念资金流最新截面 (题材粒度净流入前8+净流出前5, 进攻端)
    7. 解禁日历 (未来14天高占流通比解禁, 防守端)

用法:
  from agent.master_view import run as run_master_view
  run_master_view("2026-08-25", extra_context={"dm_kun": {...}})
"""
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

# 与 review_ai / orchestrator 同一条 claude CLI
CLAUDE_BIN = "/Users/kun/.nvm/versions/node/v24.15.0/bin/claude"

MAX_DM_CHARS = 1600      # 单个 DM-kun tab markdown 截断 (同 review_ai)
MAX_SNAP_CHARS = 1200    # 单条既有 AI 结论快照截断
CLAUDE_TIMEOUT = 480     # 秒 (6 大数据源 ~20K 输入, 360s 曾超时, 2026-08-26 提至 480)

# 行业趋势表喂给 AI 的精简列 (完整表 64 列, 大量是 MA 原值/对比基准, 对结论无用)
_TREND_KEEP_COLS = [
    "行业", "分类", "阶段", "趋势得分", "综合分", "形态", "广度%",
    "250位置%", "3年位置%", "5日涨幅", "20日涨幅", "当日涨幅%",
    "主力20日(亿)", "当日净占比%", "资金流Δ", "涨家数", "跌家数",
    "大肉%", "大面%", "领涨市值",
]


def _trim(obj, limit: int) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "...(截断)"


def _compact_trend_table(rows: list[dict]) -> list[dict]:
    """31 行业截面压成精简行: 只保留决策相关列, 按综合分降序."""
    out = []
    for r in rows:
        item = {k: r.get(k) for k in _TREND_KEEP_COLS if r.get(k) is not None}
        # 领涨股太长 (多只股票+涨幅), 只留前 40 字
        if r.get("领涨股"):
            item["领涨股"] = str(r["领涨股"])[:40]
        out.append(item)
    out.sort(key=lambda x: -(x.get("综合分") or 0))
    return out


def _latest_wisburg_views() -> dict | None:
    """智堡(海外投研)视角: 最新 1 条综合 AI 日报 + 最近 3 条单篇 AI 分析。

    智堡内容覆盖全球宏观/利率/地缘/AI 供应链, 与 A 股本土数据互补。
    直接读 agent_summary 存档 (run_type=wisburg_*), 不重复调智堡 API 和 AI,
    无存档时返回 None (总览分析照跑, 只是缺这一维度)。
    """
    from db.storage import get_agent_summary_by_id, get_agent_summary_history
    views: dict = {}
    try:
        briefs = get_agent_summary_history(limit=1, run_type="wisburg_briefing")
        if briefs:
            snap = get_agent_summary_by_id(briefs[0]["id"]) or {}
            raw = snap.get("data_snapshot_json")
            if raw:
                try:
                    s = json.loads(raw)
                    md = str(s.get("analysis_md") or "")
                    if md:
                        views["briefing"] = {
                            "run_time": s.get("run_time"),
                            "count": s.get("count"),
                            "text": md[:MAX_SNAP_CHARS],
                        }
                except Exception:
                    pass
        items = []
        for row in get_agent_summary_history(limit=3, run_type="wisburg_analyze"):
            snap = get_agent_summary_by_id(row["id"]) or {}
            raw = snap.get("data_snapshot_json")
            if not raw:
                continue
            try:
                s = json.loads(raw)
                md = str(s.get("analysis_md") or "")
                if md:
                    items.append({
                        "title": s.get("title") or row.get("content"),
                        "run_time": s.get("run_time"),
                        "text": md[:700],
                    })
            except Exception:
                continue
        if items:
            views["analyses"] = items
    except Exception as exc:
        logger.warning("[master_view] 智堡存档读取失败: %s", exc)
    return views or None


def _inst_from_interpret(interpret: str) -> str:
    """从 interpret 文本抽机构席位: '4家机构买入，成功率36%' → '4家机构买入'."""
    import re
    m = re.search(r"(\d+)\s*家机构(买入|卖出)", interpret or "")
    return f"{m.group(1)}家机构{m.group(2)}" if m else ""


def _lhb_snapshot(trade_date: str | None = None) -> dict | None:
    """龙虎榜三层快照 (游资/机构行为, 资金流数据替代不了):
    ① market 全市场聚合: 净买总额/净买净卖家数/机构席位覆盖 — 情绪温度计
    ② top_buy/top_sell: 净买/净卖前 5, 带净买占成交比(筹码集中度)与机构席位信号
    ③ divergence: 价涨但榜上净卖 / 价跌但榜上净买 — 背离预警样本
    ④ battles: 近 20 交易日上榜≥3 次的反复博弈股 (看累计净买方向判断延续性)
    无数据返回 None (龙虎榜盘后 17:30 才更新, 盘中跑总览时自动回退前一交易日).
    """
    from db.storage import get_lhb_data, get_lhb_recent
    try:
        rows = get_lhb_data(trade_date) or get_lhb_data(None)
    except Exception as exc:
        logger.warning("[master_view] 龙虎榜读取失败: %s", exc)
        return None
    if not rows:
        return None

    snap: dict = {"trade_date": rows[0].get("trade_date")}

    # ① 全市场聚合
    net_total = sum((r.get("net_buy") or 0) for r in rows)
    buy_n = sum(1 for r in rows if (r.get("net_buy") or 0) > 0)
    inst_n = sum(1 for r in rows if _inst_from_interpret(r.get("interpret") or ""))
    snap["market"] = {
        "上榜家数": len(rows),
        "净买总额(亿)": round(net_total / 1e8, 2),
        "净买家数": buy_n,
        "净卖家数": len(rows) - buy_n,
        "含机构席位家数": inst_n,
    }

    def _fmt(r):
        item = {
            "股票": r.get("stock_name"),
            "净(亿)": round((r.get("net_buy") or 0) / 1e8, 2),
            "占成交%": round(r.get("net_buy_ratio") or 0, 1),
            "涨跌%": round(r.get("change_pct") or 0, 1),
        }
        inst = _inst_from_interpret(r.get("interpret") or "")
        if inst:
            item["席位"] = inst
        return item

    # ② 净买/净卖前 5
    ordered = sorted(rows, key=lambda r: r.get("net_buy") or 0, reverse=True)
    snap["top_buy"] = [_fmt(r) for r in ordered[:5]]
    sell_side = [_fmt(r) for r in ordered if (r.get("net_buy") or 0) < 0]
    snap["top_sell"] = sell_side[-5:][::-1] if sell_side else []

    # ③ 背离样本: 价涨+榜上净卖 (出货预警) / 价跌+榜上净买 (低吸信号)
    diverge = []
    for r in rows:
        net, chg = r.get("net_buy") or 0, r.get("change_pct") or 0
        if (chg > 2 and net < -5e7) or (chg < -2 and net > 5e7):
            diverge.append({"股票": r.get("stock_name"),
                            "净(亿)": round(net / 1e8, 2), "涨跌%": round(chg, 1)})
    if diverge:
        snap["divergence"] = diverge[:4]

    # ④ 主战场: 近 20 交易日上榜≥3 次, 按次数降序
    try:
        agg: dict[str, dict] = {}
        for r in get_lhb_recent(20):
            a = agg.setdefault(r.get("stock_code"),
                               {"name": r.get("stock_name"), "n": 0, "net": 0.0})
            a["n"] += 1
            a["net"] += r.get("net_buy") or 0
        battle = sorted((a for a in agg.values() if a["n"] >= 3),
                        key=lambda a: -a["n"])
        if battle:
            snap["battles"] = [{"股票": a["name"], "上榜次数": a["n"],
                                "累计净(亿)": round(a["net"] / 1e8, 2)}
                               for a in battle[:6]]
    except Exception as exc:
        logger.warning("[master_view] 龙虎榜主战场聚合失败: %s", exc)

    return snap


# 概念流里的"伪题材": 股票池/指数成分类标签, 不是真正的题材, 会污染排名
_CONCEPT_NOISE = ("融资融券", "深股通", "沪股通", "中报预增", "中报预减",
                  "证金持股", "高股息", "漂亮100", "MSCI", "富时", "标普", "AH股")


def _concept_flow_snapshot() -> dict | None:
    """概念资金流最新截面 (题材粒度的毛细血管, 比行业资金流细一层):
    净流入前 8 + 净流出前 5, 带领涨股 — 帮 AI 把主线钉到具体题材。
    net_amount 单位已是亿元 (勿再除 1e8)。过滤股票池类伪题材标签。
    """
    from db.storage import get_concept_flow_latest
    try:
        rows = get_concept_flow_latest(top_n=400)  # 全截面 ~340 行, 拿全才能取两头
    except Exception as exc:
        logger.warning("[master_view] 概念资金流读取失败: %s", exc)
        return None
    if not rows:
        return None
    clean = [r for r in rows
             if r.get("concept") and not any(n in r["concept"] for n in _CONCEPT_NOISE)]
    if not clean:
        return None
    clean.sort(key=lambda r: r.get("net_amount") or 0, reverse=True)

    def _fmt(r):
        item = {"概念": r.get("concept"),
                "净(亿)": round(r.get("net_amount") or 0),
                "涨跌%": round(r.get("change_pct") or 0, 1)}
        if r.get("lead_stock"):
            item["领涨"] = f"{r.get('lead_stock')} {round(r.get('lead_pct') or 0, 1)}%"
        return item

    return {"fetch_time": str(rows[0].get("fetch_time") or "")[:19],
            "top_in": [_fmt(r) for r in clean[:8]],
            "top_out": [_fmt(r) for r in clean[-5:][::-1]]}


def _lockup_calendar(days: int = 14) -> dict | None:
    """解禁日历 (防守信号): 未来 N 天解禁家数/总额 + 高压力个股。
    单位注意 (2026-08-28 实测定案): lift_shares=万股、
    lift_market_cap=万元(除以 1e4 得亿)、lift_ratio=已是百分数(2.77 即 2.77%)。
    解禁是日期已知的确定性供给冲击。
    """
    from db.storage import get_lockup_expiry
    try:
        rows = get_lockup_expiry(days=days)
    except Exception as exc:
        logger.warning("[master_view] 解禁日历读取失败: %s", exc)
        return None
    if not rows:
        return None
    total_cap_yi = sum((r.get("lift_market_cap") or 0) for r in rows) / 1e4
    hot = sorted(rows, key=lambda r: r.get("lift_ratio") or 0, reverse=True)
    return {"window_days": days,
            "total": len(rows),
            "total_cap_yi": round(total_cap_yi),
            "high_pressure": [
                {"日期": r.get("free_date"), "股票": r.get("stock_name"),
                 "解禁(万股)": round(r.get("lift_shares") or 0),
                 "解禁市值(亿)": round((r.get("lift_market_cap") or 0) / 1e4, 1),
                 "占流通%": round(r.get("lift_ratio") or 0, 2)}
                for r in hot[:8]]}


def _latest_ai_snapshots() -> dict:
    """最近一次各 run_type 的 AI 结论快照 (让新分析能对照此前判断)."""
    from db.storage import get_agent_summary_latest_snapshot
    snaps = {}
    # evening: 结构化盘后报告 → 只抽关键判断字段
    ev = get_agent_summary_latest_snapshot("evening")
    if ev:
        snaps["evening"] = {
            "run_time": ev.get("run_time"),
            "market_status": ev.get("market_status"),
            "verdict_summary": ev.get("verdict_summary"),
            "core_narrative": ev.get("core_narrative"),
            "summary_text": ev.get("summary_text"),
        }
    # info_brief / strategist: markdown 正文, 截断后给
    for rt in ("info_brief", "strategist"):
        snap = get_agent_summary_latest_snapshot(rt)
        if snap:
            md = snap.get("analysis_md") or snap.get("markdown") or ""
            snaps[rt] = {
                "run_time": snap.get("run_time"),
                "text": str(md)[:MAX_SNAP_CHARS],
            }
    return snaps


def build_master_digest(trade_date: str | None = None,
                        extra_context: dict | None = None) -> dict | None:
    """汇总全数据源拼 digest。核心数据(行业趋势/复盘)都缺失时返回 None."""
    from db.storage import (get_industry_trend_daily, get_review_daily,
                            get_sector_flow_latest)

    digest: dict = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    has_core = False

    # 1. 行业趋势截面 (最近一条存档; trade_date 指定时优先该日)
    trend_row = get_industry_trend_daily(trade_date) if trade_date else None
    if not trend_row:
        trend_row = get_industry_trend_daily(None)
    if trend_row and trend_row.get("payload"):
        try:
            tp = json.loads(trend_row["payload"])
            digest["industry_trend"] = {
                "trade_date": tp.get("trade_date"),
                "headline": (tp.get("summary") or {}).get("headline"),
                "summary": tp.get("summary"),
                "changes": (tp.get("changes") or [])[:20],
                "table": _compact_trend_table(tp.get("table") or []),
            }
            digest["trade_date"] = tp.get("trade_date")
            has_core = True
        except Exception as exc:
            logger.warning("[master_view] 行业趋势 payload 解析失败: %s", exc)

    # 2. 复盘数据 (优先与行业趋势同日, 否则取最新)
    review = get_review_daily(digest.get("trade_date")) or get_review_daily(None)
    if review and review.get("payload"):
        try:
            payload = json.loads(review["payload"])
            digest["review"] = {
                "trade_date": review.get("trade_date"),
                "overview": _trim(payload.get("overview") or {}, 1600),
                "limit_analysis": _trim(payload.get("limit_analysis") or {}, 1600),
                "sentiment": _trim(payload.get("sentiment") or {}, 800),
                "style": _trim(payload.get("style") or {}, 800),
                "sector_flow": _trim(payload.get("sector_flow") or {}, 1200),
            }
            # KPI 给前端画图, 不喂 claude (call_claude 前 pop)
            from agent.review_ai import _build_kpis
            digest["_kpis"] = _build_kpis(payload)
            has_core = True
        except Exception as exc:
            logger.warning("[master_view] 复盘 payload 解析失败: %s", exc)

    if not has_core:
        return None

    # 3. DM-kun 6 tab markdown (server 内存 cache, 调用方传入; 截断同 review_ai)
    # cache 空的条目 (如服务刚重启预热未完成) 回退到最新一条 DB 存档
    dm = dict((extra_context or {}).get("dm_kun") or {})
    try:
        from db.storage import get_dm_kun_latest_by_name
        for ep_name in ("market-regime", "sentiment-cycle", "industry-crowding",
                        "industry-enhanced", "theme-ladder", "stock-recommender"):
            cache_key = ep_name.replace("-", "_")
            if not (dm.get(cache_key) or "").strip():
                row = get_dm_kun_latest_by_name(ep_name)
                if row and row.get("markdown"):
                    dm[cache_key] = row["markdown"]
    except Exception as exc:
        logger.warning("[master_view] DM-kun 存档回退读取失败: %s", exc)
    if dm:
        digest["dm_kun_analysis"] = {
            name: str(md)[:MAX_DM_CHARS]
            for name, md in dm.items() if md and str(md).strip()
        }

    # 4. 既有 AI 结论 (对照用)
    snaps = _latest_ai_snapshots()
    if snaps:
        digest["prior_ai_views"] = snaps

    # 5. 板块资金流最新截面 (净流入/流出各前 8)
    try:
        flows = get_sector_flow_latest("industry")
        if flows:
            keep = flows[:8] + flows[-8:] if len(flows) > 16 else flows
            digest["sector_flow_latest"] = [
                {"板块": f.get("sector_name"), "涨跌%": f.get("change_pct"),
                 "主力净流入(亿)": f.get("main_inflow")}
                for f in keep
            ]
    except Exception as exc:
        logger.warning("[master_view] 板块资金流读取失败: %s", exc)

    # 6. 智堡(海外投研)视角: 最新综合日报 + 近期单篇分析存档
    wb = _latest_wisburg_views()
    if wb:
        digest["wisburg_views"] = wb

    # 7. 龙虎榜三层快照 (游资/机构个股级行为)
    lhb = _lhb_snapshot(trade_date)
    if lhb:
        digest["lhb"] = lhb

    # 8. 概念资金流最新截面 (题材粒度, 进攻端: 把主线钉到具体题材)
    cf = _concept_flow_snapshot()
    if cf:
        digest["concept_flow"] = cf

    # 9. 解禁日历 (防守端: 未来 14 天确定性供给冲击)
    lk = _lockup_calendar(days=14)
    if lk:
        digest["lockup_calendar"] = lk

    return digest


PROMPT_TEMPLATE = """你是 A 股全市场首席策略分析师。下面是一套量化系统汇总的全部数据
(生成时间 {generated_at}, 数据截至 {trade_date}), 包含:
- industry_trend: 31 个申万一级行业趋势截面 (九态分类/阶段/位置百分位/广度/
  主力资金/当日动能/跨日跃迁变化), 这是系统的行业骨架视图
- review: 当日复盘量化数据 (资金流/涨跌停/情绪/风格/结构)
- dm_kun_analysis: 6 个专题分析 (市场状态/情绪周期/行业拥挤度/行业增强/题材梯队/选股)
- prior_ai_views: 此前 AI 分析的历史判断 (盘后裁决/信息简报/战略推理), 供对照
- sector_flow_latest: 板块资金流最新截面
- wisburg_views: 智堡投研(海外视角)的 AI 分析存档 — briefing=综合 10 类日报
  (全球宏观/利率/地缘/大宗商品/AI 供应链), analyses=近期单篇深度分析;
  这是外部世界视角, 用于与 A 股本土数据做内外交叉验证 (可能缺失)
- lhb: 龙虎榜三层快照 (个股级游资/机构行为, 资金流数据替代不了) —
  market=全市场聚合(上榜家数/净买总额/净买净卖家数/机构席位覆盖, 即短线情绪温度计),
  top_buy/top_sell=净买/净卖前 5 (含占成交%=筹码集中度、席位=机构动向),
  divergence=价涨但榜上净卖/价跌但榜上净买的背离样本 (主力出货/低吸预警),
  battles=近 20 日上榜≥3 次的反复博弈股 (累计净买方向判断延续性) (可能缺失)
- concept_flow: 概念资金流最新截面 (题材粒度, 比行业资金流细一层) —
  top_in=净流入前 8 题材 (含领涨股), top_out=净流出前 5 (可能缺失)
- lockup_calendar: 未来 14 天解禁日历 (日期已知的确定性供给冲击) —
  high_pressure 按占流通比排序, 比例越高抛压越集中 (可能缺失)

数据 (JSON):
{digest_json}

写一份 markdown 总览分析报告, 要求:
1. `## 全局定位` — 一句话总判断开头; 用「行业趋势结构 × 情绪周期 × 资金面」
   三个维度交叉定位当前市场处于什么状态
2. `## 行业格局` — 基于 industry_trend: 强势主升行业 (综合分/位置/广度佐证)、
   底部反转候选、恶化需回避的行业; 引用跃迁变化 (upgrades/downgrades) 说明结构在改善还是恶化
3. `## 多源交叉验证` — review/dm_kun/资金流/行业趋势之间哪些信号共振、哪些背离
   (例如行业趋势多头但当日资金流出), 背离时给出你更相信哪边及理由;
   对照 prior_ai_views, 指出此前判断被证实还是证伪;
   再与 wisburg_views 的海外宏观视角 (利率/地缘/全球资金/大宗商品) 交叉:
   海外因素对国内主线是顺风、逆风还是无关, 传导路径是什么
   (数据里没有 wisburg_views 时跳过此点, 不要编造);
   最后用 lhb 龙虎榜验证短线资金真实动向: 全市场净买总额与净买卖家数比
   反映游资进攻意愿, 机构席位覆盖多说明专业资金参与度高;
   点名 divergence 里的背离样本 (价涨榜净卖=出货预警) 并给出应对;
   battles 里"反复上榜且累计净买为正"的题材延续性最强, 反之警惕断板
   (数据里没有 lhb 时跳过此点, 不要编造)
4. `## 主线与机会` — 综合给出 2-3 条主线 (行业+逻辑+持续性判断),
   并结合 dm_kun 的选股/题材梯队给出具体方向;
   用 concept_flow 把主线钉到具体题材: 行业趋势多头的行业, 若概念资金流
   里对应的具体题材(如芯片/存储/机器人)同时净流入居前, 说明资金正从行业
   级下沉到题材级, 主线更扎实; 反之行业热但题材资金涣散, 则主线偏虚
   (数据里没有 concept_flow 时跳过此点, 不要编造)
5. `## 策略与风险` — 仓位建议 (激进/中性/防守三档)、操作节奏、
   2 个情景推演 (概率+应对)、明确的风险清单;
   风险清单必须纳入 lockup_calendar: 未来 14 天高占流通比的解禁股是日期
   已知的确定性抛压, 若主线行业/个股恰逢大额解禁, 明确提示回避时点
   (数据里没有 lockup_calendar 时跳过此点, 不要编造)

约束:
- 只用数据里有的信息, 不要编造数字; 引用具体数值时与数据一致
- 结论先行, 每节第一句给判断; 总长度 900-1500 字
- 直接输出 markdown 正文, 不要代码块包裹, 不要任何开场白/过程描述
  (不要写"数据已通读"之类的话), 第一个字符必须是 `## 全局定位`"""


def call_claude(digest: dict) -> str:
    """调 claude CLI 生成总览分析, 返回 markdown (失败返回 ⚠️ 开头提示)."""
    if not Path(CLAUDE_BIN).exists():
        return f"⚠️ claude CLI 不存在 ({CLAUDE_BIN}), 跳过总览 AI 分析"
    prompt_data = {k: v for k, v in digest.items() if k != "_kpis"}
    prompt = PROMPT_TEMPLATE.format(
        generated_at=digest.get("generated_at"),
        trade_date=digest.get("trade_date") or "最新",
        digest_json=json.dumps(prompt_data, ensure_ascii=False, indent=1),
    )
    tmp = Path("/tmp") / f"mra-master-view-{os.getpid()}.txt"
    tmp.write_text(prompt, encoding="utf-8")
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", f"按 {tmp} 文件里的完整指示执行",
             "--output-format", "text",
             "--dangerously-skip-permissions"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        out = (proc.stdout or "").strip()
        if out:
            return out
        return f"⚠️ 总览 AI 分析为空 (exit {proc.returncode}): {(proc.stderr or '')[:300]}"
    except subprocess.TimeoutExpired:
        return f"⚠️ 总览 AI 分析超时 ({CLAUDE_TIMEOUT}s)"
    except Exception as e:
        return f"⚠️ 总览 AI 分析异常: {e}"
    finally:
        tmp.unlink(missing_ok=True)


def run(trade_date: str | None = None, extra_context: dict | None = None) -> dict:
    """主入口: 构建 digest → 调 AI → 落 agent_summary(run_type=master_view)."""
    digest = build_master_digest(trade_date, extra_context)
    if digest is None:
        return {"status": "skip",
                "reason": f"{trade_date or '最新'} 无行业趋势与复盘数据, 无法做总览分析"}

    md = call_claude(digest)
    ok = not md.startswith("⚠️")
    # 摘要取第一个标题行 (防模型开场白污染); 没有则退回首个非空行
    summary_line = ""
    for line in md.splitlines():
        if line.strip().startswith("#"):
            summary_line = line.strip().lstrip("# ").strip()
            break
    if not summary_line:
        summary_line = next((l.strip() for l in md.splitlines() if l.strip()), "")
    # 正文若带开场白, 裁到第一个标题处 (历史回看更干净)
    if ok:
        idx = md.find("\n## ")
        if idx > 0 and md[:idx].strip() and not md.strip().startswith("#"):
            md = md[idx:].lstrip()

    trend = digest.get("industry_trend") or {}
    from db.storage import insert_agent_summary
    insert_agent_summary(
        content=summary_line[:500],
        data_snapshot_json=json.dumps({
            "trade_date": digest.get("trade_date"),
            "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "analysis_md": md,
            "kpis": {
                **(digest.get("_kpis") or {}),
                "trend_headline": (trend.get("summary") or {}).get("headline"),
                "trend_cat_counts": (trend.get("summary") or {}).get("cat_counts"),
                "trend_upgrades": (trend.get("summary") or {}).get("upgrades"),
                "trend_downgrades": (trend.get("summary") or {}).get("downgrades"),
                "trend_resonance": (trend.get("summary") or {}).get("resonance"),
            },
            "digest_used": {
                "trade_date": digest.get("trade_date"),
                "sources": [k for k in digest
                            if k in ("industry_trend", "review", "dm_kun_analysis",
                                     "prior_ai_views", "sector_flow_latest",
                                     "wisburg_views", "lhb", "concept_flow",
                                     "lockup_calendar")],
            },
            "ok": ok,
        }, ensure_ascii=False, default=str),
        run_type="master_view",
        report_html="",
    )
    logger.info("[master_view] %s 总览分析%s落库 (run_type=master_view, %d字)",
                digest.get("trade_date"), "" if ok else "(带⚠️)", len(md))
    return {"status": "ok" if ok else "warn",
            "trade_date": digest.get("trade_date"),
            "summary": summary_line[:200], "md_len": len(md)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    td = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(run(td), ensure_ascii=False, indent=2))
