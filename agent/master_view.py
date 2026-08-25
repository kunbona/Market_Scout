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
CLAUDE_TIMEOUT = 360     # 秒 (输入比 review_ai 大, 略放宽)

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

    return digest


PROMPT_TEMPLATE = """你是 A 股全市场首席策略分析师。下面是一套量化系统汇总的全部数据
(生成时间 {generated_at}, 数据截至 {trade_date}), 包含:
- industry_trend: 31 个申万一级行业趋势截面 (九态分类/阶段/位置百分位/广度/
  主力资金/当日动能/跨日跃迁变化), 这是系统的行业骨架视图
- review: 当日复盘量化数据 (资金流/涨跌停/情绪/风格/结构)
- dm_kun_analysis: 6 个专题分析 (市场状态/情绪周期/行业拥挤度/行业增强/题材梯队/选股)
- prior_ai_views: 此前 AI 分析的历史判断 (盘后裁决/信息简报/战略推理), 供对照
- sector_flow_latest: 板块资金流最新截面

数据 (JSON):
{digest_json}

写一份 markdown 总览分析报告, 要求:
1. `## 全局定位` — 一句话总判断开头; 用「行业趋势结构 × 情绪周期 × 资金面」
   三个维度交叉定位当前市场处于什么状态
2. `## 行业格局` — 基于 industry_trend: 强势主升行业 (综合分/位置/广度佐证)、
   底部反转候选、恶化需回避的行业; 引用跃迁变化 (upgrades/downgrades) 说明结构在改善还是恶化
3. `## 多源交叉验证` — review/dm_kun/资金流/行业趋势之间哪些信号共振、哪些背离
   (例如行业趋势多头但当日资金流出), 背离时给出你更相信哪边及理由;
   对照 prior_ai_views, 指出此前判断被证实还是证伪
4. `## 主线与机会` — 综合给出 2-3 条主线 (行业+逻辑+持续性判断),
   并结合 dm_kun 的选股/题材梯队给出具体方向
5. `## 策略与风险` — 仓位建议 (激进/中性/防守三档)、操作节奏、
   2 个情景推演 (概率+应对)、明确的风险清单

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
                                     "prior_ai_views", "sector_flow_latest")],
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
