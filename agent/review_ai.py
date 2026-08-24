"""
复盘 AI 总结: 把复盘页面自己算的数据 (L4 legacy: 资金流/涨跌停/情绪/风格/结构)
+ DM-kun 6 tab 分析喂给 claude, 让 AI 做交叉总结分析, 结果落 agent_summary 表
(run_type=review_ai)。

数据链路 (2026-08-19 解耦: 不再用市场数据管线的 15 个 compute / _v2_dimensions):
  review_daily legacy payload (overview/limit_analysis/sentiment/style/...)
  + DM-kun 6 tab markdown (由调用方从 server 内存 cache 传入 extra_context)
  → 摘要 digest → claude -p → markdown 总结 → insert_agent_summary

用法:
  from agent.review_ai import run as run_review_ai
  run_review_ai("2026-08-17", extra_context={"dm_kun": {...}})
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

# claude CLI 路径 (orchestrator 已发现的稳定路径; 若移除会走 fallback 提示)
CLAUDE_BIN = "/Users/kun/.nvm/versions/node/v24.15.0/bin/claude"

MAX_DIM_CHARS = 900    # 单个维度摘要截断
MAX_DM_CHARS = 1600    # 单个 DM-kun tab markdown 截断
CLAUDE_TIMEOUT = 300   # 秒


def _trim(obj, limit: int) -> str:
    """obj 转紧凑 JSON 字符串并截断"""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "...(截断)"


def _build_kpis(payload: dict) -> dict:
    """从 review_daily payload 提取可视化 KPI (落库给前端画图用, 不喂 claude)."""
    ov = payload.get("overview") or {}
    la = payload.get("limit_analysis") or {}
    return {
        "limit_up": la.get("limit_up_count"),
        "limit_down": la.get("limit_down_count"),
        "limit_up_trend": (la.get("limit_up_trend") or [])[-6:],
        "limit_up_sectors": (la.get("limit_up_sectors") or [])[:8],
        "total_amount_yi": ov.get("total_amount_yi"),
        "amount_ratio": ov.get("amount_ratio"),
        "main_net_yi": ov.get("main_net_yi"),
        "retail_net_yi": ov.get("retail_net_yi"),
        "up_count": ov.get("up_count"),
        "down_count": ov.get("down_count"),
    }


def build_digest(trade_date: str | None = None,
                 extra_context: dict | None = None) -> dict | None:
    """从 review_daily 取复盘页自己的数据 (L4 legacy), 拼 AI 输入 digest.

    2026-08-19 解耦: 不再依赖 _v2_dimensions (市场数据管线的 15 个 compute 产出),
    AI 只用复盘页显示的数据: 资金流/涨跌停/情绪/风格/结构 + DM-kun 6 tab。
    没有复盘数据返回 None.
    """
    from db.storage import get_review_daily

    if trade_date is None:
        # 默认最新一条
        rows = get_review_dates_all()
        if not rows:
            return None
        trade_date = rows[0]
    row = get_review_daily(trade_date)
    if not row or not row.get("payload"):
        return None
    try:
        payload = json.loads(row["payload"])
    except Exception:
        return None

    # 复盘页可见的 legacy 数据 (L4 compute_daily_analysis 产出, 即复盘 5 个 tab 的数据源)
    digest = {
        "trade_date": trade_date,
        "created_at": row.get("created_at"),
        "review_summary": payload.get("_v2_summary") or "",
        "overview": _trim(payload.get("overview") or {}, 1600),              # 资金流/成交额/涨跌家数
        "limit_analysis": _trim(payload.get("limit_analysis") or {}, 1600),  # 涨跌停分析
        "sentiment": _trim(payload.get("sentiment") or {}, 800),             # 情绪
        "style": _trim(payload.get("style") or {}, 800),                     # 风格
        "market_cap_groups": _trim(payload.get("market_cap_groups") or {}, 800),   # 市值结构
        "price_groups": _trim(payload.get("price_groups") or {}, 800),
        "turnover_groups": _trim(payload.get("turnover_groups") or {}, 800),
        "sector_flow": _trim(payload.get("sector_flow") or {}, 1600),        # 板块资金流
        "flow_trend": _trim(payload.get("flow_trend") or {}, 800),
    }

    # KPI 给前端可视化用 (call_claude 前 pop 掉, 不浪费 token)
    digest["_kpis"] = _build_kpis(payload)

    # DM-kun 6 tab markdown (server 内存 cache, 调用方传入)
    dm = (extra_context or {}).get("dm_kun") or {}
    if dm:
        digest["dm_kun_analysis"] = {}
        for name, md in dm.items():
            if md and str(md).strip():
                digest["dm_kun_analysis"][name] = str(md)[:MAX_DM_CHARS]
    return digest


def get_review_dates_all() -> list[str]:
    from db.storage import get_review_dates
    return get_review_dates()


PROMPT_TEMPLATE = """你是资深 A 股复盘分析师。下面是 {date} 的当日复盘数据 (复盘页量化数据 + 6 个专题分析),
来自一套量化系统, 数据已经过计算验证, 请基于数据做交叉总结分析。
数据说明: overview=资金流/成交额/涨跌家数, limit_analysis=涨跌停, sentiment=情绪,
style=风格, market_cap/price/turnover_groups=市值/价位/换手结构, sector_flow=板块资金流,
dm_kun_analysis=6 个专题分析 (市场状态/情绪周期/行业拥挤/行业增强/主题阶梯/选股推荐)。

数据 (JSON):
{digest_json}

写一份 markdown 复盘总结, 要求:
1. `## 当日核心结论` — 3-5 句话讲清今天市场的本质 (情绪位置/资金方向/风格)
2. `## 多维交叉验证` — 哪些维度的信号共振 (如同向确认), 哪些背离 (如涨但资金流出),
   用数据说话, 引用具体数字
3. `## 情绪周期定位` — 结合涨跌停/成交额/情绪评分和 dm_kun_analysis 里的情绪周期分析,
   判断当前处于情绪周期哪个阶段
4. `## 板块主线` — 资金流+涨停分布+dm_kun_analysis 的行业拥挤/主题阶梯交叉看,
   今天的主线是什么, 持续性如何
5. `## 明日推演与风险` — 基于以上给出 2-3 个情景推演 (概率+应对), 明确风险点

约束:
- 只用数据里有的信息, 不要编造数字
- 结论先行, 每节开头一句话给判断; 总长度 600-1000 字
- 直接输出 markdown 正文, 不要代码块包裹"""


def call_claude(digest: dict) -> str:
    """调 claude CLI 生成总结, 返回 markdown 文本 (失败返回 ⚠️ 开头的提示)."""
    if not Path(CLAUDE_BIN).exists():
        return f"⚠️ claude CLI 不存在 ({CLAUDE_BIN}), 跳过复盘 AI 总结"
    prompt_data = {k: v for k, v in digest.items() if k != "_kpis"}  # KPI 只落库, 不进 prompt
    prompt = PROMPT_TEMPLATE.format(
        date=digest.get("trade_date"),
        digest_json=json.dumps(prompt_data, ensure_ascii=False, indent=1),
    )
    # prompt 走文件, 避免 argv 超长
    tmp = Path("/tmp") / f"mra-review-ai-{os.getpid()}.txt"
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
        return f"⚠️ 复盘 AI 总结为空 (exit {proc.returncode}): {(proc.stderr or '')[:300]}"
    except subprocess.TimeoutExpired:
        return f"⚠️ 复盘 AI 总结超时 ({CLAUDE_TIMEOUT}s)"
    except Exception as e:
        return f"⚠️ 复盘 AI 总结异常: {e}"
    finally:
        tmp.unlink(missing_ok=True)


def run(trade_date: str | None = None, extra_context: dict | None = None) -> dict:
    """主入口: 构建 digest → 调 AI → 落 agent_summary. 返回结果 dict."""
    digest = build_digest(trade_date, extra_context)
    if digest is None:
        return {"status": "skip", "reason": f"{trade_date or '最新'} 无复盘数据 (或 legacy 半成品)"}

    md = call_claude(digest)
    ok = not md.startswith("⚠️")
    summary_line = md.splitlines()[0].lstrip("# ").strip() if ok else md

    from db.storage import insert_agent_summary
    insert_agent_summary(
        content=summary_line[:500],
        data_snapshot_json=json.dumps({
            "trade_date": digest["trade_date"],
            "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "analysis_md": md,
            "kpis": digest.get("_kpis") or {},
            "digest_used": {k: digest.get(k) for k in ("trade_date", "review_summary")},
            "ok": ok,
        }, ensure_ascii=False, default=str),
        run_type="review_ai",
        report_html="",
    )
    logger.info("[review_ai] %s 总结%s落库 (run_type=review_ai)",
                digest["trade_date"], "" if ok else "(带⚠️) ")
    return {"status": "ok" if ok else "warn", "trade_date": digest["trade_date"],
            "summary": summary_line[:200], "md_len": len(md)}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    td = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(run(td), ensure_ascii=False, indent=2))
