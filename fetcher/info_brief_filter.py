"""
信息情报简报 (info_brief) 专用 filter helper。

- 公告: 流程性砍掉, 实质公告保留 (C 版本用)
- 政策: 标题清洗 + 类型粗分类 (B 版本预处理)
- 财新: 清洗【数据通专享】前缀
- 多源印证: 同主题聚合 (代码预筛, 不用 LLM)
"""
import re
from typing import Tuple, Optional, List, Dict, Any

# ── 公告: 流程性 vs 实质 ───────────────────────────────────────────
NOTICE_NOISE_KEYWORDS = [
    "董事会决议", "独立董事专门会议", "股东大会决议", "监事会决议",
    "会议通知", "章程修订", "工商变更", "会计师事务所变更",
    "提示性公告", "进展公告", "停牌核查", "停牌公告",
    "关于召开", "关于举行", "关于选举",
]

NOTICE_STRONG_KEYWORDS = [
    "回购", "增持", "减持", "质押", "解除质押", "解除限售",
    "重组", "吸并", "吸收合并", "并购", "要约收购", "控制权变更",
    "中标", "重大合同", "签署协议", "签署战略合作",
    "业绩预增", "业绩预减", "扭亏", "首亏", "续亏",
    "立案", "监管函", "警示函", "ST", "退市", "摘牌", "复牌", "停牌",
    "海外大单", "产业链", "技术突破", "扩产", "涨价", "降价",
    "首次覆盖", "评级调整", "目标价",
]

# 公告类型 → posterior 权重 (C 版本用)
NOTICE_TYPE_WEIGHT = {
    "重组": 0.9, "并购": 0.85, "退市": 0.95, "ST": 0.9, "立案": 0.85,
    "回购": 0.7, "业绩预增": 0.7, "业绩预减": 0.7, "扭亏": 0.7,
    "减持": 0.65, "增持": 0.65, "质押": 0.5, "解除质押": 0.4,
    "中标": 0.6, "重大合同": 0.7, "海外大单": 0.75, "技术突破": 0.75,
    "扩产": 0.6, "涨价": 0.55, "降价": 0.55,
}

# 中小盘阈值 (元) — < 100亿 砍掉
SMALL_CAP_THRESHOLD = 100_0000_0000


def is_market_moving_notice(title: str, market_cap: Optional[int] = None) -> Tuple[bool, str]:
    """
    返回 (是否保留, 原因)。
    - 强关键词命中 → True
    - 流程性关键词命中 → False
    - 中小盘 + 无强关键词 → False
    """
    if not title:
        return False, "空标题"

    for kw in NOTICE_STRONG_KEYWORDS:
        if kw in title:
            return True, f"强关键词: {kw}"

    for kw in NOTICE_NOISE_KEYWORDS:
        if kw in title:
            return False, f"流程性: {kw}"

    if market_cap is not None and market_cap < SMALL_CAP_THRESHOLD:
        return False, f"中小盘: {market_cap/1e8:.0f}亿"

    return False, "无明确市场级信号"


def get_notice_type(title: str) -> str:
    """从标题抽 notice_type, 找不到返回 '其他'。"""
    # 优先级: 重组 > 业绩 > 减持 > 增持 > 质押 > 中标 ...
    PRIORITY = [
        ("重组", ["重组", "吸并", "吸收合并", "并购", "要约收购", "控制权变更"]),
        ("退市", ["退市", "摘牌"]),
        ("ST", ["ST", "*ST"]),
        ("立案", ["立案", "监管函", "警示函"]),
        ("业绩预增", ["业绩预增", "扭亏"]),
        ("业绩预减", ["业绩预减", "首亏", "续亏"]),
        ("回购", ["回购"]),
        ("减持", ["减持"]),
        ("增持", ["增持"]),
        ("质押", ["质押", "解除质押"]),
        ("中标", ["中标", "重大合同", "签署协议"]),
        ("海外大单", ["海外大单"]),
        ("技术突破", ["技术突破"]),
        ("扩产", ["扩产"]),
        ("涨价", ["涨价", "降价"]),
    ]
    for ttype, kws in PRIORITY:
        for kw in kws:
            if kw in title:
                return ttype
    return "其他"


# ── 政策: 标题清洗 + 粗分类 ──────────────────────────────────────
POLICY_TYPE_KEYWORDS = {
    "产业规划": ["规划", "意见", "指导", "方案", "行动计划"],
    "价格调整": ["价格调整", "成品油", "电价", "气价"],
    "市场监管": ["监管", "立案", "处罚", "调查"],
    "资本市场制度": ["期权", "期货", "上市规则", "交易规则", "减持", "分红", "回购"],
    "工作会议": ["会议", "座谈", "调研", "讲话", "致辞", "会见", "会见"],
    "信用体系": ["信用", "示范"],
    "民营经济": ["民营", "民营企业"],
    "其他": [],
}


def classify_policy_type(title: str) -> str:
    """政策类型粗分类 (LLM 可覆盖, 这是兜底)。"""
    for ptype, kws in POLICY_TYPE_KEYWORDS.items():
        if any(kw in title for kw in kws):
            return ptype
    return "其他"


def classify_policy_phase(title: str) -> str:
    """政策阶段粗判断: 吹风/征求意见/落地/执行/NA。"""
    if any(kw in title for kw in ["征求意见", "公开征求", "意见稿"]):
        return "征求意见"
    if any(kw in title for kw in ["印发", "发布", "实施", "施行", "正式"]):
        return "落地"
    if any(kw in title for kw in ["试点", "首批", "案例"]):
        return "执行"
    if any(kw in title for kw in ["研究", "拟", "将"]):
        return "吹风"
    return "NA"


# ── 财新: 清洗【数据通专享】等前缀 ──────────────────────────────
CAIXIN_PREFIX_RE = re.compile(r"^【[^】]*】\s*")


def clean_caixin_title(title: str) -> str:
    """去掉财新标题里的【...】前缀。"""
    if not title:
        return ""
    return CAIXIN_PREFIX_RE.sub("", title).strip()


# ── 财经快讯: 提取 ticker ──────────────────────────────────────
TICKER_RE = re.compile(r"\b(60[0-9]{4}|00[0-9]{4}|30[0-9]{4}|68[0-9]{4}|51[0-9]{4}|15[0-9]{4}|18[0-9]{4})\b")


def extract_ticker(title: str) -> str:
    """从标题抽股票代码。"""
    if not title:
        return ""
    m = TICKER_RE.search(title)
    return m.group(1) if m else ""


# ── 多源印证: 同事件聚合 (代码预筛, 不用 LLM) ──────────────
def aggregate_multi_source(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按 ticker + 时间窗聚合, 输出每个事件的 source_count。
    输入每条: {pub_time, source, title, ticker, topic?}
    输出每条加 _source_count, _is_first, _is_repeat_24h 字段。
    """
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        ticker = it.get("ticker") or ""
        # 无 ticker 的放"无主"组, 不聚合
        if not ticker:
            it["_source_count"] = 1
            it["_is_first"] = True
            it["_is_repeat_24h"] = False
            continue
        by_ticker.setdefault(ticker, []).append(it)

    for ticker, group in by_ticker.items():
        sources = set(g["source"] for g in group if g.get("source"))
        count = len(sources)
        # 找最早的
        group_sorted = sorted(group, key=lambda g: g.get("pub_time", ""))
        earliest = group_sorted[0]
        for g in group:
            g["_source_count"] = count
            g["_is_first"] = (g is earliest)
        # 24h 内是否 ≥3 次
        # 简化: 如果组内条目数 ≥3, 标 repeat
        is_repeat = len(group) >= 3
        for g in group:
            g["_is_repeat_24h"] = is_repeat

    return items
