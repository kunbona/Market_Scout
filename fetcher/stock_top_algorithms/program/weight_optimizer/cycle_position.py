"""
牛熊周期定位器

核心思路：将所有指标的 risk_percentage (0-100) 等权平均，自适应EMA平滑。
指标已由数据管线统一为 0=低风险 100=高风险 的尺度。

抗钝化 + 延迟信号设计：
1. 长窗口自适应EMA：正常时EMA(60)延迟信号（避免提前数月触发），
   当多数指标达到极端时缩短至EMA(10)，让峰/谷更尖锐
2. 尾部放大：对最终分数做幂变换，拉开极端区与中性区的距离
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


# ==================== 参数 ====================
#
# 参数分类（降低过拟合风险）：
# - 固定参数：有明确金融逻辑或经敏感性分析证明稳健，不应调优
# - 可调参数：影响信号质量，但数量已精简以降低过拟合风险
#
# 过拟合分析结论（2026-05）：
# - 原18个参数中7/9敏感，已精简合并为10个可调+若干固定
# - 逃顶/抄底共享 reversal_points 和 confirm_days（两者原本就相同）
# - 底部冷却期 = 2 × 顶部冷却期（A股底部磨底更久，比例固定）
# - 新增逃顶共识过滤器，降低对单一指标(herding_rate)的依赖

# ---- 固定参数（EMA平滑，经多轮测试稳定）----
EMA_SPAN_MAX = 60   # 正常EMA窗口（~3个月，延迟信号避免过早触发）
EMA_SPAN_MIN = 10   # 极端共识时最短EMA窗口（比5更平滑）
EXTREME_THRESHOLD = 75  # 指标>75或<25视为"极端"
CONSENSUS_RATIO = 0.5   # 50%指标达到极端时开始缩短EMA
TAIL_POWER = 0.7     # 尾部放大幂次 (<1 = 放大极端, 1 = 无变换)

# ---- 固定参数（敏感性分析证明稳健，或有明确金融逻辑）----
SIGNAL_SLOPE_WINDOW = 5    # 斜率计算窗口（天）
SIGNAL_CONFIRM_DAYS = 3    # 连续确认天数（共享，避免假突破的最小确认期）
SIGNAL_BOTTOM_CLEAR_THRESHOLD = 35  # 抄底撤离阈值（敏感性扫描28-45全不变，极稳健）
SIGNAL_BOTTOM_MA_WINDOW = 20   # 抄底价格确认均线窗口（标准值）

# ---- 可调参数（核心信号逻辑）----
SIGNAL_ARM_HIGH = 75       # 逃顶进入阈值
SIGNAL_ARM_LOW = 18        # 抄底进入阈值
# 顶快底慢的不对称设计：
#   逃顶单级——牛市情绪快，半仓预警只会踏空，进入临界后直接等强力清仓，不再发半仓减仓信号。
#   抄底双级——熊市底部难测，保留轻仓试探，先上车再强力加仓确认。
EMIT_LIGHT_ESCAPE_TOP = False   # 是否发出 light_escape_top 半仓逃顶预警（False=砍掉，逃顶单级）
SIGNAL_REVERSAL_POINTS = 8       # 逃顶从峰回撤触发点数（也是抄底未显式覆盖时的 fallback）
SIGNAL_BOTTOM_REVERSAL_POINTS = 3  # 抄底从谷回升触发点数（独立于逃顶）：对已进入临界的底确认提速，2019/2024更早更低确认
SIGNAL_TOP_COOLDOWN_DAYS = 60    # 逃顶冷却期（底部冷却 = 2×此值）
SIGNAL_TOP_CLEAR_THRESHOLD = 50 # 逃顶撤离阈值
SIGNAL_TOP_SLOPE_THRESHOLD = 0.01      # 逃顶斜率进入阈值（拐头确认）
SIGNAL_BOTTOM_SLOPE_THRESHOLD = -0.25  # 抄底斜率进入阈值（允许V型底）
SIGNAL_BOTTOM_MAX_NEAR_DAYS = 60      # 抄底最大临界天数（安全网）

# ---- 两档信号参数（轻仓/重仓，左右侧结合）----
# 涨停占比阈值：near_bottom状态下，涨停占比超过此值视为breadth thrust → 重仓抄底
# 历史验证：7%阈值捕获5/9个历史底部（其余由分数回撤路径兜底）
# 选7%而非5%：过滤2012-01等数据稀疏期的假突破（6.5%限涨日）
SIGNAL_LIMIT_UP_THRUST = 7.0   # 百分比（7% = 全市场7%个股涨停）
# 重仓信号的最大涨/跌幅约束（防止追涨追跌）
# 用户要求"涨了10-20%了才说重仓抄底"不可接受，取15%作为折中
# 重仓抄底：指数不能从临界期最低价涨超15%
# 重仓逃顶：指数不能从临界期最高价跌超15%
SIGNAL_MAX_RISE_FROM_TROUGH = 0.15  # 15%
SIGNAL_MAX_DROP_FROM_PEAK = 0.15    # 15%
# 预防式逃顶的"价格确认下限"（分层逃顶第二条件，2026-06 落地为生产默认 0.10）：
# 语义——分数确认(条件1:从峰回撤reversal_points)成立后，指数还需从临界期最高价跌幅
# ≥ 此值(条件2)，才升级为清仓 escape_top；否则停在 near_top 继续等价格兑现。
# 配合 SIGNAL_ESCAPE_HALF_ON_CONFIRM=True 构成"分层逃顶"：分数确认先减半仓预警
# (light_escape_top→0.5)，价格再跌够此值才清仓(→0.0)。
# 真顶价格续跌→终会跌够→清仓(吃前~10%，仍远好于满仓硬扛)；
# 假顶/慢牛价格小跌即反弹→永远到不了→只停在半仓，不全踏空。
# hard_stop/trail_stop 反应式止损不受此下限约束(它们本就是为大跌兜底，仍直接清仓)。
# 全历史验证见 layered_escape_probe.py（终值 4.92→4.41，2025假顶保住半仓、灾难顶保护不丢）。
SIGNAL_ESCAPE_MIN_PRICE_DROP = 0.10
# 分层逃顶开关：True=分数确认但价格未跌够时先发 light_escape_top 减半仓(生产默认)；
# False=退化为纯延迟(AND门，仅压制逃顶不减仓，见 and_gate_escape_probe.py)。
# 仅在 SIGNAL_ESCAPE_MIN_PRICE_DROP 启用时有意义。
SIGNAL_ESCAPE_HALF_ON_CONFIRM = True
# 热区硬性止损（闪崩兜底）：在 near_top（分数已≥75 的过热区）内，若指数从临界期最高价
# 跌幅 ≥ 此阈值，则不等分数确认、立即强力清仓。这是逃顶第二道闸门的反应式触发器，
# 与预防式"分数确认"逃顶并存（双触发）。止损只在热区生效，避免误砍牛市健康回调
# （牛市健康回调动辄 -10~15%，故阈值取 15% 而非更紧，避免打断"逃在顶上"的记录）。
# 实测 2014-2025：历史最深热区实时回撤仅 9.2%，从未触及 15%，故对历史业绩零影响——
# 它是为"历史外尾部风险（如单日跳空闪崩）"上的免费保险。None=关闭（沿用旧语义）。
SIGNAL_HARD_STOP_FROM_PEAK = 0.15
# 指标无关的"最后保险"——移动止损（always-on，不依赖状态机是否进入临界）：
# 收盘价从过去 TRAIL_STOP_WINDOW 日滚动最高价回撤 ≥ TRAIL_STOP_PCT 即强制清仓。
# 动机：硬止损只在 near_top 热区生效；若一个顶分数从未到 75（如2019温和顶、
# 2011/2008 指标未覆盖的深熊），状态机永不进入临界，热区止损形同虚设——这正是
# "逃顶最后保险"要填的缺口。-20% = 技术性熊市的经典定义（非过拟合阈值），
# 250日 = 标准年度高点窗口。实测2010-2025：在状态机已逃的2015/2018/2021顶，
# 移动止损均"晚于"状态机触发（纯兜底不抢功，更不打断"逃在顶上"）；唯独在
# 状态机完全漏掉的2011深熊(-36%)上独立触发，把损失腰斩到-17%，且全程净值
# 不降反升（whipsaw仅8次/16年）。重入完全交给状态机 buy_bottom/re_entry，
# 移动止损只负责"被动清仓"这一道右侧最后防线。None=关闭。详见 compare_insurance.py。
SIGNAL_TRAIL_STOP_PCT = 0.20
SIGNAL_TRAIL_STOP_WINDOW = 250
# 轻仓抄底最小跌幅要求：指数须从120日高点下跌超过此比例才发出轻仓信号
# 原理：市场"修正"的经典定义是跌10%，未达到修正幅度不应入场
# 效果：过滤4个浅跌假信号（2012-03/06, 2013-11, 2023-12），消除>-10%回撤
SIGNAL_LIGHT_MIN_DECLINE = 0.10     # 10%
SIGNAL_LIGHT_DECLINE_WINDOW = 120   # 用120个交易日的滚动最高价作为参考
# 最少指标数量：少于此数量的有效指标时，不发出任何信号
# 原理：指标太少时分数不可靠（如2014年前仅1-2个指标），容易产生假信号
SIGNAL_MIN_INDICATOR_COUNT = 3

# ---- 逃顶共识过滤器（降低herding_rate单因子依赖）----
# 进入逃顶临界时，要求至少N个"顶部敏感指标"处于极端高位(>75)
# 避免仅因单一指标极端就触发逃顶
SIGNAL_TOP_CONSENSUS_MIN = 2  # 至少2个指标极端才进入逃顶临界
SIGNAL_TOP_CONSENSUS_INDICATORS = [
    'herding_rate',                # 羊群效应（去掉后丢3/4逃顶）
    'market_turnover_percentile',  # 换手率（去掉后抄底噪音+3）
    'pe_composite',                # 估值（去掉后丢1逃顶）
    'market_crowdedness',          # 拥挤度（与换手率相关但独立维度）
    # 'new_high_ratio' 已移除：原始占比非百分位,>75只占0.06%形同废票。详见 bug-review-decisions.md S2
]

# ---- 情绪子系统并联通道（抓2018/2021结构性抱团顶）----
# 问题根因：8指标等权平均假设"顶=所有指标共同过热"，对2015全面顶成立，但
# 2018/2021是结构性抱团顶——只有情绪/资金类指标爆表(80-100)、广度类指标冷淡，
# 等权平均被稀释到64-66，永远摸不到75进入线，从未逃顶（2018顶后大盘跌-25%）。
# 解法：把情绪/资金类指标拆成独立子系统并联，任一通道达标即可进入逃顶。
# 实测：情绪子系统>=75历史上零假信号，每段后都跟16-43%真实大跌，2019/2023假顶不触发。
SIGNAL_EMOTION_INDICATORS = [
    'pe_composite',          # 综合估值
    'hs300_equity_premium',  # 股权溢价
    'market_crowdedness',    # 拥挤度
    'herding_rate',          # 抱团率
]
SIGNAL_EMOTION_ARM_HIGH = 75  # 情绪子系统进入阈值（与主通道一致）

# ---- 广度背离分类器（区分"健康普涨"与"背离结构顶"）----
# 广度绝对值在结构顶反而低（少数股拉指数），故不进等权打分，只做分类器。
# 用站上MA60的历史分位区分两种高情绪状态：
#   广度健康(高分位) + 情绪不极端 → 普涨牛市中继(如2020-07情绪70/广度94)，降级不进入
#   广度背离(低分位) → 结构顶(如2018广度32/2021广度24)，正常进入逃顶
# 对历史业绩零增益（情绪>=75本身已零假信号），是为"未来情绪高但广度健康普涨行情"上的前瞻保险。
SIGNAL_BREADTH_HEALTHY_PCT = 70   # 广度分位>=此值视为"健康"
SIGNAL_BREADTH_DIVERGENT_PCT = 35  # 广度分位<此值视为"背离顶"
SIGNAL_BREADTH_BYPASS_EMOTION = 85  # 情绪>=此值时无视广度健康，强制进入（极端情绪优先）

# ---- 认错回补参数（逃顶后突破前高则重新入场）----
# 逃顶后监控窗口：在此期间如果指数突破逃顶期间最高价，则认错回补。
# 90个交易日≈4个半月（2026-06 由 120 回退）：分层逃顶(SIGNAL_ESCAPE_MIN_PRICE_DROP=0.10)
# 落地后，慢牛假顶只减半仓、不全踏空，原 D 突破旁路(为救2025全踏空而设)已被取代并关闭，
# 故配套的延长窗口一并回退到分层方案前的 90。
SIGNAL_REENTRY_WINDOW = 90
# 突破确认天数：连续N天收盘价高于前高才触发回补（防假突破）
SIGNAL_REENTRY_CONFIRM_DAYS = 1
# 回补后止损确认天数：连续N天收盘价低于参考价才触发止损
SIGNAL_REENTRY_STOP_DAYS = 3
# 回补强制空仓期：逃顶后 N 个交易日内不允许认错回补（防止顶部假突破立即回补）。
# 0 = 关闭（当前生产行为）。逃顶判断常在剧烈震荡顶失效，给市场一段冷静期再判突破真伪。
SIGNAL_REENTRY_COOLDOWN_DAYS = 0

# ---- 回补"退烧门控"（区分"慢牛趋势延续"与"顶部假突破"，根治回补太随意）----
# 根因：原回补只看"价格突破逃顶前高"，完全无视"我们当初逃跑的过热理由消化了没有"。
# 历史3次 re_entry(2015-06/2017-11/2026-01)触发当天顶通道分数都是72~82(仍在/接近顶区)，
# 即全部是"过热未消化时的价格反弹"——接刀。慢牛与假突破的本质区别就是：
#   真慢牛 = 情绪/拥挤先降温(分数退烧出顶区) → 价格再创新高（健康趋势）
#   假突破 = 价格还在原顶位附近反弹，分数从没退烧（过热未消化）
# 门控做两道与价格无关、且由系统已有线（不引入新魔数）派生的硬条件：
#   ① 退烧 latch：post_escape 窗口内顶通道分数曾跌破退烧线(=撤离线50与顶区75的中点62.5)，
#      证明这波过热"已消化"——既成事实，不因创新高当天分数小幅回升而失效（避免误挡慢牛）。
#      退烧线取中点而非撤离线本身：历史唯一靠 latch 挡的(2017)窗口只退烧到71.1，安全平台为[50,70]；
#      撤离线50在平台最底=要求最深退烧，会误挡"只部分退烧(到55~65)就创新高"的温和慢牛。中点62.5
#      居平台正中、距唯一泄漏点71.1有~8.6分余量，把可接纳退烧深度放宽约一倍，不踏空慢牛。
#   ② 当天非顶区：回补当天顶通道分数 < arm_high(75)，堵住"latch 后二次过热又冲回顶区"接刀漏洞。
# 两条由不同的线把守 → 历史3次接刀被「①或②」分别挡掉(latch挡2017，顶区线挡2015/2026)，
# 不是单一阈值拟合单次灾难。中间带(62.5<分数<75)=慢牛温和升温区，才放行回补，不踏空真趋势。
# require_cooling=False 完全回到旧行为（仅价格突破），供对照与回退。
SIGNAL_REENTRY_REQUIRE_COOLING = True
# 退烧线：None=取撤离线与顶区线中点((50+75)/2=62.5)，避免新魔数。也可显式覆盖做敏感性扫描。
SIGNAL_REENTRY_COOLING_THRESHOLD = None
# ---- 决定性突破旁路（根治"强势慢牛分数从不退烧 → 回补被退烧门焊死 → 踏空"）----
# 根因（2025-12 逃顶实证）：某些慢牛里估值/拥挤结构性偏高，顶通道分数全程不退烧(>62.5)，
# 退烧门控两道(latch + 非顶区)都过不了，价格已决定性创新高(+5%)仍永不回补，整波踏空。
# 旁路：post_escape 内价格站上逃顶前高 *(1+bypass_pct) 时，直接放行回补——
# "决定性创新高"本身就是"逃错了"的独立铁证，不再要求分数退烧。
# 安全边际由 bypass_pct 把守，与退烧门控相互独立、互为补充：
#   - 真接刀(顶部小幅假突破后崩)：历史 2015 escape 后价格仅 +0.9%、2018/2021 逃后即崩，
#     都够不到 +3% → 旁路不放行，退烧门控继续挡刀。
#   - 真慢牛(决定性创新高)：2025 逃后 +3.3%→+5.3% → 旁路放行，救回踏空。
# None=关闭（仅退烧门控）。2026-05 曾落地 0.05 为生产默认(D 方案，救慢牛全踏空)；
# 2026-06 改用"分层逃顶"(SIGNAL_ESCAPE_MIN_PRICE_DROP=0.10 + 半仓档)后，慢牛假顶
# 在源头只减半仓、不再全踏空，D 的"逃了再回补"已无必要，故回退为 None 关闭。
# 参数与逻辑保留，供对照/回退（slowbull_2025_probe.py / apply_d_probe.py 仍可复跑）。
SIGNAL_REENTRY_BREAKOUT_BYPASS_PCT = None

# ---- 抄底"深跌共识"并联进入通道（对称于逃顶共识过滤器，默认关闭）----
# 根因：抄底只有单一等权分数 score<=18 一条进入路径。后泡沫慢熊底(如2016)会出现
# "局部深跌"——技术/拥挤类指标进单位数(ma250_bias 2.1/herding 5.4/crowdedness 7.8)，
# 但估值/价格类仍在半山腰(PE 43.6/价格 67.1)，等权平均被稀释到24.6，永远摸不到18，从未进入。
# 这与逃顶侧2018/2021"结构稀释顶"完全对称：逃顶已用"情绪子分数max通道+共识计数"解决，
# 抄底侧却无对称机制。本通道做"计数进入"：深跌类指标中≥K个 risk_pct<T 即额外进入 near_bottom。
# 关键设计：进入层放开，确认层完全不变（仍需站上MA20+分数回升+连续确认+15%追高护栏+score>撤离线）。
# 假设：宽进入+严确认能抓2016，而严确认拦得住2015股灾/2018快跌的下跌中继假反弹。
# 此假设必须经留出检验实证，未验证前默认 None=关闭（完全不改生产行为）。
SIGNAL_DEEP_OVERSOLD_INDICATORS = [
    'ma250_bias',                  # 技术超跌（底部偏离极强，+31）
    'herding_rate',                # 抱团瓦解（情绪冰点）
    'market_crowdedness',          # 资金撤离（情绪冰点）
    'below_net_asset',            # 破净（极强抄底信号）
    'market_turnover_percentile',  # 地量（技术超跌）
]
SIGNAL_DEEP_OVERSOLD_THRESHOLD = 10    # risk_pct<此值视为该维度深跌；None=关闭整条通道。落地=10：抓2016后泡沫慢熊底
SIGNAL_DEEP_OVERSOLD_MIN_COUNT = 3     # 至少K个维度深跌才并联进入。K=3是结构性分界线：分开2016真深跌(3个个位数维度)与2015崩盘反弹(2维度)

# ---- 向后兼容的旧常量名（deprecated，指向新值）----
SIGNAL_TOP_REVERSAL_POINTS = SIGNAL_REVERSAL_POINTS
# SIGNAL_BOTTOM_REVERSAL_POINTS 已在上方独立定义（=3，落地确认提速），不再指向逃顶共享值
SIGNAL_TOP_CONFIRM_DAYS = SIGNAL_CONFIRM_DAYS
SIGNAL_BOTTOM_CONFIRM_DAYS = SIGNAL_CONFIRM_DAYS
SIGNAL_BOTTOM_COOLDOWN_DAYS = SIGNAL_TOP_COOLDOWN_DAYS * 2

# 市场阶段划分
MARKET_PHASES = {
    "极度低估": {"range": (0, 20), "emoji": "🟢", "advice": "底部区域，积极建仓"},
    "偏多":     {"range": (20, 40), "emoji": "🟢", "advice": "安全区域，持有为主"},
    "中性":     {"range": (40, 60), "emoji": "⚪", "advice": "观望，按计划执行"},
    "偏空":     {"range": (60, 80), "emoji": "🟠", "advice": "开始减仓，提高警惕"},
    "极度高估": {"range": (80, 100), "emoji": "🔴", "advice": "顶部区域，果断减仓"},
}


# ==================== 核心函数 ====================


def _amplify_extremes(score: np.ndarray, power: float = TAIL_POWER) -> np.ndarray:
    """对分数做幂变换，放大远离50的部分

    power < 1 时放大极端：50附近不变，远端被推更远
    例如 power=0.7: 83→87, 73→78, 60→65, 50→50, 40→35, 21→13

    Args:
        score: 0-100 分数数组
        power: 幂次，<1放大极端, 1不变

    Returns:
        变换后的分数 (0-100)
    """
    t = (score - 50) / 50  # -1 to 1
    amplified = 50 + 50 * np.sign(t) * np.abs(t) ** power
    return np.clip(amplified, 0, 100)


def compute_cycle_score(
    indicators: Dict[str, pd.Series],
    ema_span_max: int = EMA_SPAN_MAX,
    ema_span_min: int = EMA_SPAN_MIN,
    tail_power: float = TAIL_POWER,
) -> pd.Series:
    """计算周期分数 (0-100)

    等权平均 → 自适应EMA平滑 → 尾部放大
    高分 = 近顶, 低分 = 近底。

    自适应EMA：当多数指标达到极端值(>75或<25)时，
    缩短EMA窗口以加快响应，让峰谷更尖锐。

    Args:
        indicators: {name: risk_percentage_series} 所有指标，值域0-100
        ema_span_max: 正常EMA窗口
        ema_span_min: 极端共识时最短窗口
        tail_power: 尾部放大幂次

    Returns:
        pd.Series: 周期分数 (0-100)
    """
    # 去重并对齐
    deduped = {}
    for name, series in indicators.items():
        s = series[~series.index.duplicated(keep='last')]
        deduped[name] = s

    df = pd.DataFrame(deduped)

    # 等权平均（跳过当日缺失的指标）
    raw_score = df.mean(axis=1, skipna=True)
    all_nan = df.isna().all(axis=1)
    raw_score[all_nan] = np.nan

    # 计算每日"极端共识度"：多少指标处于极端区
    extreme_count = ((df > EXTREME_THRESHOLD) | (df < (100 - EXTREME_THRESHOLD))).sum(axis=1)
    indicator_count = df.notna().sum(axis=1).clip(lower=1)
    extreme_ratio = extreme_count / indicator_count

    # 自适应EMA：共识越强，窗口越短
    consensus_scale = (extreme_ratio / CONSENSUS_RATIO).clip(0, 1)
    effective_span = ema_span_max - (ema_span_max - ema_span_min) * consensus_scale

    # 逐日EMA递推（支持可变alpha）
    raw_vals = raw_score.values.copy()
    smoothed = np.empty_like(raw_vals)
    spans = effective_span.values

    # 找到第一个非NaN值作为初始值
    first_valid = 0
    for i in range(len(raw_vals)):
        if not np.isnan(raw_vals[i]):
            first_valid = i
            smoothed[i] = raw_vals[i]
            break
        else:
            smoothed[i] = np.nan

    for i in range(first_valid + 1, len(raw_vals)):
        alpha = 2.0 / (spans[i] + 1)
        if np.isnan(raw_vals[i]):
            smoothed[i] = smoothed[i - 1]
        else:
            smoothed[i] = alpha * raw_vals[i] + (1 - alpha) * smoothed[i - 1]

    # 尾部放大：拉开极端区与中性区的距离
    if tail_power < 1.0:
        smoothed = _amplify_extremes(smoothed, power=tail_power)

    result = pd.Series(smoothed, index=raw_score.index)
    return result.clip(0, 100).rename("cycle_score")


def classify_phase(score: float) -> Dict:
    """将单个分数映射到市场阶段

    Args:
        score: 周期分数 (0-100)

    Returns:
        Dict with: phase, emoji, advice
    """
    for phase, info in MARKET_PHASES.items():
        lo, hi = info["range"]
        if lo <= score < hi or (phase == "极度高估" and score >= hi - 1):
            return {"phase": phase, "emoji": info["emoji"], "advice": info["advice"]}
    return {"phase": "中性", "emoji": "⚪", "advice": "观望"}


def classify_market_phases(score_series: pd.Series) -> pd.DataFrame:
    """为每日分数分配市场阶段

    Args:
        score_series: compute_cycle_score 输出

    Returns:
        DataFrame: score, phase, emoji
    """
    score = score_series.dropna()
    phases = pd.Series("中性", index=score.index)
    phases[score < 20] = "极度低估"
    phases[(score >= 20) & (score < 40)] = "偏多"
    phases[(score >= 40) & (score < 60)] = "中性"
    phases[(score >= 60) & (score < 80)] = "偏空"
    phases[score >= 80] = "极度高估"

    return pd.DataFrame({"score": score, "phase": phases})


# ==================== 逃顶/抄底信号 ====================


# 系统可发出的全部权威事件（derive_event_column 的值域）。三张映射表必须覆盖它，
# 否则下游遇到未登记事件会"静默保持仓位"掩盖错误 —— 用模块加载期断言把漂移变成响亮失败。
CYCLE_EVENTS = frozenset({
    'none', 'light_buy_bottom', 'light_escape_top',
    'buy_bottom_confirm', 're_entry', 're_entry_stop',
    'escape_top_confirm', 'escape_top_trailstop', 'near_bottom_cleared',
})

# 权威事件 → 目标仓位（单一事实源）。下游不再反推 signal/state，只读 event 查这张表。
# 方向语义内嵌在 _events_to_positions 的 fold 里（买只增、轻仓逃顶只减），这里给目标值。
EVENT_TO_POSITION = {
    'light_buy_bottom': 0.30,
    'buy_bottom_confirm': 1.00,
    'near_bottom_cleared': 0.00,
    'escape_top_confirm': 0.00,
    'escape_top_trailstop': 0.00,
    're_entry': 1.00,
    're_entry_stop': 0.00,
    'light_escape_top': 0.50,
    'none': None,
}

# event → 旧版 signal_event 词表（回测输出列保持向后兼容，5 个下游消费者不受影响）
_EVENT_TO_LEGACY = {
    'light_buy_bottom': 'light_buy_bottom',
    'light_escape_top': 'light_escape_top',
    'buy_bottom_confirm': 'buy_bottom',
    'escape_top_confirm': 'escape_top',
    'escape_top_trailstop': 'escape_top',
    're_entry': 're_entry',
    're_entry_stop': 're_entry_stop',
    'near_bottom_cleared': 'false_bottom_exit',
    'none': '',
}

# 任何人新增事件却忘了补映射，在 import 时即报错，而非生产里悄悄冻结仓位。
assert set(EVENT_TO_POSITION) == CYCLE_EVENTS, (
    f"EVENT_TO_POSITION 与 CYCLE_EVENTS 不一致: {set(EVENT_TO_POSITION) ^ CYCLE_EVENTS}")
assert set(_EVENT_TO_LEGACY) == CYCLE_EVENTS, (
    f"_EVENT_TO_LEGACY 与 CYCLE_EVENTS 不一致: {set(_EVENT_TO_LEGACY) ^ CYCLE_EVENTS}")


def derive_event_column(
    signal: pd.Series,
    signal_light: pd.Series,
    state: pd.Series,
) -> pd.Series:
    """从已定稿的 signal/signal_light/state 合成权威 event 列（单一事实源）。

    event 是三列的纯函数：generate_signals 在产出端调用它写入 event 列；回测端在收到
    没有 event 的旧式 signals_df（如单测构造的合成帧）时，用同一函数补出 event，保证
    产出端与消费端语义完全一致（DRY）。改任何映射只改这一处。

    事件类型：
      - escape_top_trailstop：移动止损兜底逃顶（state=='trail_stop'，原"借壳"消失）
      - escape_top_confirm  ：状态机确认/硬止损逃顶
      - buy_bottom_confirm  ：重仓抄底确认
      - near_bottom_cleared       ：near_bottom 未兑现即撤离（前一日 near_bottom→今非，且
                              今日无 buy_bottom/re_entry）。轻仓假底破灭的清仓依据。
      - re_entry / re_entry_stop / light_buy_bottom / light_escape_top / none
    """
    prev_state = state.shift(1)
    event = pd.Series('none', index=signal.index, dtype=object)
    # 轻仓档（会被重仓档覆盖）
    event[signal_light == 'light_buy_bottom'] = 'light_buy_bottom'
    event[signal_light == 'light_escape_top'] = 'light_escape_top'
    # 重仓档
    event[signal == 'buy_bottom'] = 'buy_bottom_confirm'
    event[signal == 're_entry'] = 're_entry'
    event[signal == 're_entry_stop'] = 're_entry_stop'
    event[signal == 'escape_top'] = 'escape_top_confirm'
    event[(signal == 'escape_top') & (state == 'trail_stop')] = 'escape_top_trailstop'
    # 撤离：前一日 near_bottom，今日不再 near_bottom，且今日没有重仓多头确认
    cleared = (
        (prev_state == 'near_bottom')
        & (state != 'near_bottom')
        & (~signal.isin(['buy_bottom', 're_entry']))
    )
    event[cleared & (event == 'none')] = 'near_bottom_cleared'
    return event


def _events_to_positions(
    event: pd.Series,
    event_to_level: Optional[Dict[str, float]] = None,
    light_buy_level: Optional[float] = None,
) -> pd.Series:
    """事件序列 → 逐日目标仓位（含方向守卫）。回测与 live 报告共用的唯一 fold。

    方向语义（硬逻辑，不依赖历史恰好不出现某情形）：
      - light_buy_bottom ：看涨，加仓只增不减   current = max(current, 目标)
      - light_escape_top ：看跌，减仓只减不增   current = min(current, 目标)
      - near_bottom_cleared    ：仅清"试探性轻仓底"(≤light_buy_level)，不动已确认的重仓多头
      - 其余确认/止损事件：直接设到目标仓位

    event_to_level 缺省用 EVENT_TO_POSITION（live 报告）；回测端传入按 position_levels
    覆盖各档比例的映射，保证两条链路仓位语义同源、不漂移。
    """
    levels = event_to_level or EVENT_TO_POSITION
    if light_buy_level is None:
        light_buy_level = levels.get('light_buy_bottom', 0.30)
    out = np.zeros(len(event))
    current = 0.0
    ev_values = event.values
    for i in range(len(ev_values)):
        ev = ev_values[i]
        target = levels.get(ev)
        if target is not None:
            if ev == 'light_buy_bottom':
                current = max(current, target)
            elif ev == 'light_escape_top':
                current = min(current, target)
            elif ev == 'near_bottom_cleared':
                if current <= light_buy_level:
                    current = 0.0
            else:
                current = target
        out[i] = current
    return pd.Series(out, index=event.index)


def generate_signals(
    score_series: pd.Series,
    index_close: Optional[pd.Series] = None,
    indicators: Optional[Dict[str, pd.Series]] = None,
    limit_up_ratio: Optional[pd.Series] = None,
    emotion_score: Optional[pd.Series] = None,
    breadth_pct: Optional[pd.Series] = None,
    # 核心可调参数
    arm_high: float = SIGNAL_ARM_HIGH,
    arm_low: float = SIGNAL_ARM_LOW,
    reversal_points: float = SIGNAL_REVERSAL_POINTS,
    confirm_days: int = SIGNAL_CONFIRM_DAYS,
    top_cooldown_days: int = SIGNAL_TOP_COOLDOWN_DAYS,
    top_clear_threshold: float = SIGNAL_TOP_CLEAR_THRESHOLD,
    top_slope_threshold: float = SIGNAL_TOP_SLOPE_THRESHOLD,
    bottom_slope_threshold: float = SIGNAL_BOTTOM_SLOPE_THRESHOLD,
    bottom_max_near_days: int = SIGNAL_BOTTOM_MAX_NEAR_DAYS,
    # 两档信号参数
    limit_up_thrust: float = SIGNAL_LIMIT_UP_THRUST,
    max_rise_from_trough: float = SIGNAL_MAX_RISE_FROM_TROUGH,
    max_drop_from_peak: float = SIGNAL_MAX_DROP_FROM_PEAK,
    escape_min_price_drop: Optional[float] = SIGNAL_ESCAPE_MIN_PRICE_DROP,
    escape_half_on_confirm: bool = SIGNAL_ESCAPE_HALF_ON_CONFIRM,
    hard_stop_from_peak: Optional[float] = SIGNAL_HARD_STOP_FROM_PEAK,
    trail_stop_pct: Optional[float] = SIGNAL_TRAIL_STOP_PCT,
    trail_stop_window: int = SIGNAL_TRAIL_STOP_WINDOW,
    light_min_decline: float = SIGNAL_LIGHT_MIN_DECLINE,
    light_decline_window: int = SIGNAL_LIGHT_DECLINE_WINDOW,
    min_indicator_count: int = SIGNAL_MIN_INDICATOR_COUNT,
    # 认错回补参数
    reentry_window: int = SIGNAL_REENTRY_WINDOW,
    reentry_confirm_days: int = SIGNAL_REENTRY_CONFIRM_DAYS,
    reentry_stop_days: int = SIGNAL_REENTRY_STOP_DAYS,
    reentry_cooldown_days: int = SIGNAL_REENTRY_COOLDOWN_DAYS,
    reentry_require_cooling: bool = SIGNAL_REENTRY_REQUIRE_COOLING,
    reentry_cooling_threshold: Optional[float] = SIGNAL_REENTRY_COOLING_THRESHOLD,
    reentry_breakout_bypass_pct: Optional[float] = SIGNAL_REENTRY_BREAKOUT_BYPASS_PCT,
    enable_reentry: bool = True,
    # 固定参数（一般不调）
    slope_window: int = SIGNAL_SLOPE_WINDOW,
    bottom_clear_threshold: float = SIGNAL_BOTTOM_CLEAR_THRESHOLD,
    bottom_ma_window: int = SIGNAL_BOTTOM_MA_WINDOW,
    # 共识过滤器
    top_consensus_min: int = SIGNAL_TOP_CONSENSUS_MIN,
    top_consensus_indicators: Optional[List[str]] = None,  # None=用全局常量(生产默认)；显式传入供 A/B 实验
    # 抄底深跌共识并联进入（默认关闭：deep_oversold_threshold=None）
    deep_oversold_threshold: Optional[float] = SIGNAL_DEEP_OVERSOLD_THRESHOLD,
    deep_oversold_min_count: int = SIGNAL_DEEP_OVERSOLD_MIN_COUNT,
    # 情绪子系统并联 + 广度背离分类器
    emotion_arm_high: float = SIGNAL_EMOTION_ARM_HIGH,
    breadth_healthy_pct: float = SIGNAL_BREADTH_HEALTHY_PCT,
    breadth_bypass_emotion: float = SIGNAL_BREADTH_BYPASS_EMOTION,
    # 顶快底慢：是否发出半仓逃顶预警（默认 False = 逃顶单级，详见 EMIT_LIGHT_ESCAPE_TOP）
    emit_light_escape_top: bool = EMIT_LIGHT_ESCAPE_TOP,
    # 向后兼容（deprecated，传入时覆盖对应参数）
    top_reversal_points: Optional[float] = None,
    bottom_reversal_points: Optional[float] = SIGNAL_BOTTOM_REVERSAL_POINTS,
    top_confirm_days: Optional[int] = None,
    bottom_confirm_days: Optional[int] = None,
    bottom_cooldown_days: Optional[int] = None,
    cooldown_days: Optional[int] = None,
) -> pd.DataFrame:
    """生成两档逃顶和抄底信号（轻仓 + 重仓）

    两档信号框架（左右侧结合）：
    - 轻仓信号（左侧）：分数进入极端区 → 提前预警，建议轻仓操作
    - 重仓信号（右侧确认）：市场微观结构或分数回撤确认 → 高确信度

    轻仓信号：
    - light_escape_top：进入near_top状态时发出（一次性）
    - light_buy_bottom：进入near_bottom状态时发出（一次性）

    重仓信号（抄底两条路径）：
    - 路径A（breadth thrust）：near_bottom + 涨停占比 > 阈值 → 快速捕获V型底
    - 路径B（分数回撤）：near_bottom + 分数从谷回撤 + 确认 → 捕获渐进底
    - 两条路径均需：指数未从谷底涨超max_rise_from_trough

    重仓信号（逃顶）：
    - 分数从峰值回撤 + 确认 + 指数未从峰值跌超max_drop_from_peak

    认错回补信号：
    - 逃顶后进入 post_escape 监控状态（最长 reentry_window 天）
    - 指数收盘价连续 reentry_confirm_days 天高于逃顶期间最高价 → 发出 re_entry 信号
    - 逻辑：突破前高说明逃顶判断错误，牛市仍在继续

    Args:
        score_series: 周期分数 (0-100)
        index_close: 可选，指数收盘价（认错回补功能必须提供）
        indicators: 可选，原始指标字典用于共识过滤器
        limit_up_ratio: 可选，每日涨停占比(0-1)用于breadth thrust确认
        limit_up_thrust: 涨停占比阈值(%)，超过视为breadth thrust
        max_rise_from_trough: 重仓抄底最大允许涨幅(0.10=10%)
        max_drop_from_peak: 重仓逃顶最大允许跌幅(0.10=10%)
        reentry_window: 逃顶后监控窗口（交易日）
        reentry_confirm_days: 突破前高连续确认天数

    Returns:
        DataFrame columns:
          signal: 'none'/'escape_top'/'buy_bottom'/'re_entry' (重仓信号)
          signal_light: 'none'/'light_escape_top'/'light_buy_bottom' (轻仓信号)
          state: 状态
          ref_value: 峰/谷跟踪值
    """
    # 参数解析：共享参数 + 向后兼容覆盖
    _top_reversal = top_reversal_points if top_reversal_points is not None else reversal_points
    _bottom_reversal = bottom_reversal_points if bottom_reversal_points is not None else reversal_points
    _top_confirm = top_confirm_days if top_confirm_days is not None else confirm_days
    _bottom_confirm = bottom_confirm_days if bottom_confirm_days is not None else confirm_days
    _top_cooldown = top_cooldown_days
    _bottom_cooldown = bottom_cooldown_days if bottom_cooldown_days is not None else (top_cooldown_days * 2)
    if cooldown_days is not None:
        _top_cooldown = cooldown_days
        _bottom_cooldown = cooldown_days

    # 退烧门控：退烧线缺省=两条已有线(撤离50/顶区75)的中点(不引入新魔数)。低于此值视为"过热已消化"。
    # 取中点而非撤离线本身：历史接刀里唯一靠 latch 挡的(2017)窗口只退烧到 71.1，安全平台为[撤离线,70]；
    # 撤离线50落在平台最底(要求最深退烧)，会误挡"只部分退烧(到55~65)就创新高的慢牛"。中点62.5
    # 居平台正中、距唯一泄漏点71.1有~8.6分余量，把可接纳的退烧深度放宽约一倍，不踏空温和慢牛。
    _reentry_cooling_thr = (
        reentry_cooling_threshold if reentry_cooling_threshold is not None
        else (top_clear_threshold + arm_high) / 2.0
    )

    valid = score_series.dropna()
    if len(valid) < slope_window + 1:
        return pd.DataFrame({
            'signal': 'none',
            'signal_light': 'none',
            'state': 'normal',
            'ref_value': np.nan,
        }, index=score_series.index)

    # 计算分数的斜率
    slope = valid.rolling(slope_window, min_periods=slope_window).apply(
        lambda x: np.polyfit(range(len(x)), x.values, 1)[0], raw=False
    )

    # 预计算价格均线（在原始日历上计算，再对齐到分数日期）
    close_aligned = None
    ma_aligned = None
    rolling_high_aligned = None
    trail_high_aligned = None
    if index_close is not None:
        close_sorted = index_close.sort_index()
        ma = close_sorted.rolling(bottom_ma_window, min_periods=bottom_ma_window).mean()
        # ffill 而非 nearest：只用最近的过去值，杜绝前视偏差（nearest 会取到未来日的值）
        close_aligned = close_sorted.reindex(valid.index, method='ffill')
        ma_aligned = ma.reindex(valid.index, method='ffill')
        # 滚动最高价（轻仓信号最小跌幅检查）
        rh = close_sorted.rolling(light_decline_window, min_periods=max(60, light_decline_window // 2)).max()
        rolling_high_aligned = rh.reindex(valid.index, method='ffill')
        # 移动止损"最后保险"用的长周期滚动最高价（默认250日，独立于轻仓窗口）
        if trail_stop_pct is not None:
            trh = close_sorted.rolling(trail_stop_window, min_periods=1).max()
            trail_high_aligned = trh.reindex(valid.index, method='ffill')

    # 预计算逃顶共识过滤器（对齐指标到分数日期）
    consensus_pool = (top_consensus_indicators
                      if top_consensus_indicators is not None
                      else SIGNAL_TOP_CONSENSUS_INDICATORS)
    consensus_aligned = {}
    if indicators is not None:
        for ind_name in consensus_pool:
            if ind_name in indicators:
                s = indicators[ind_name]
                s = s[~s.index.duplicated(keep='last')]
                consensus_aligned[ind_name] = s.reindex(valid.index, method='ffill')

    # 预计算抄底深跌计数（每日 risk_pct<阈值 的深跌类指标个数）。默认关闭。
    deep_oversold_count_aligned = None
    if indicators is not None and deep_oversold_threshold is not None:
        cap_cols = {}
        for ind_name in SIGNAL_DEEP_OVERSOLD_INDICATORS:
            if ind_name in indicators:
                s = indicators[ind_name]
                s = s[~s.index.duplicated(keep='last')]
                cap_cols[ind_name] = s.reindex(valid.index, method='ffill')
        if cap_cols:
            cap_df = pd.DataFrame(cap_cols)
            deep_oversold_count_aligned = (cap_df < deep_oversold_threshold).sum(axis=1)

    # 预对齐涨停占比（用于breadth thrust确认）
    limit_up_aligned = None
    if limit_up_ratio is not None:
        lu = limit_up_ratio[~limit_up_ratio.index.duplicated(keep='last')]
        limit_up_aligned = lu.reindex(valid.index, method='ffill')

    # 预对齐情绪子系统分数（并联进入通道）
    emotion_aligned = None
    if emotion_score is not None:
        es = emotion_score[~emotion_score.index.duplicated(keep='last')]
        emotion_aligned = es.reindex(valid.index, method='ffill')

    # 预对齐广度分位（背离分类器）
    breadth_aligned = None
    if breadth_pct is not None:
        bp = breadth_pct[~breadth_pct.index.duplicated(keep='last')]
        breadth_aligned = bp.reindex(valid.index, method='ffill')

    # 预计算每日有效指标数量（用于最少指标过滤）
    indicator_count_aligned = None
    if indicators is not None and min_indicator_count > 0:
        deduped_inds = {}
        for ind_name, s in indicators.items():
            deduped_inds[ind_name] = s[~s.index.duplicated(keep='last')]
        ind_df = pd.DataFrame(deduped_inds)
        ind_count = ind_df.notna().sum(axis=1)
        indicator_count_aligned = ind_count.reindex(valid.index, method='ffill')

    signals = []
    light_signals = []
    states = []
    ref_values = []

    state = 'normal'
    peak = 0.0
    trough = 100.0
    confirm_count = 0
    top_cooldown_remaining = 0
    bottom_cooldown_remaining = 0
    near_days = 0
    # 两档信号追踪
    peak_close = 0.0       # near_top期间指数最高价
    trough_close = 1e9     # near_bottom期间指数最低价
    half_escape_armed = False  # 分层逃顶 latch：本段 near_top 是否已发过半仓预警（防每日重发刷屏）
    # 认错回补追踪
    escape_ref_price = 0.0     # 逃顶期间最高价（突破目标）
    post_escape_days = 0       # post_escape已过天数
    reentry_confirm_count = 0  # 突破确认计数
    reentry_cooled = False     # 退烧 latch：本段 post_escape 内顶通道分数是否曾跌破退烧线（过热已消化）
    reentry_ref_price = 0.0    # 回补参考价（止损线）
    post_reentry_days = 0      # post_reentry已过天数
    reentry_stop_count = 0     # 跌破确认计数

    valid_values = valid.values
    valid_dates = valid.index

    # 逃顶通道分数 = max(主分数, 情绪子系统分数)，逐日取大。
    # 这样2018/2021被等权稀释的主分数(64-66)会被情绪分数(80-86)顶替，
    # 自然越过进入线；而抄底通道仍只用主分数（情绪不参与抄底）。
    # 顶通道斜率也基于这条合成分数计算，保证"峰值回撤+斜率拐头"逻辑一致。
    top_values = valid_values.copy()
    if emotion_aligned is not None:
        emo_vals = emotion_aligned.values
        top_values = np.where(
            np.isnan(emo_vals), valid_values,
            np.maximum(valid_values, emo_vals),
        )
    top_score_series = pd.Series(top_values, index=valid.index)
    top_slope = top_score_series.rolling(slope_window, min_periods=slope_window).apply(
        lambda x: np.polyfit(range(len(x)), x.values, 1)[0], raw=False
    )

    for i, date in enumerate(valid_dates):
        score = float(valid_values[i])
        top_sc = float(top_values[i])
        top_s = float(top_slope.loc[date]) if date in top_slope.index and not np.isnan(top_slope.loc[date]) else 0.0
        s = float(slope.loc[date]) if date in slope.index and not np.isnan(slope.loc[date]) else 0.0
        signal = 'none'
        light_signal = 'none'

        # 获取当日指数收盘价
        cur_close = None
        if close_aligned is not None:
            cur_close = close_aligned.get(date)
            if cur_close is not None and np.isnan(cur_close):
                cur_close = None

        # 指标数量不足时跳过（分数不可靠，不发信号）
        if indicator_count_aligned is not None:
            ic = indicator_count_aligned.get(date)
            if ic is not None and ic < min_indicator_count:
                signals.append(signal)
                light_signals.append(light_signal)
                states.append(state)
                ref_values.append(np.nan)
                continue

        # 冷却期：逃顶和抄底独立冷却
        if top_cooldown_remaining > 0:
            top_cooldown_remaining -= 1
        if bottom_cooldown_remaining > 0:
            bottom_cooldown_remaining -= 1

        # 如果两个都在冷却中，跳过
        if top_cooldown_remaining > 0 and bottom_cooldown_remaining > 0:
            signals.append(signal)
            light_signals.append(light_signal)
            states.append('cooldown')
            ref_values.append(np.nan)
            continue

        if state == 'normal':
            # 逃顶进入条件：顶通道分数(max(主,情绪))>arm_high + 斜率拐头 + 共识过滤 + 广度分类
            if top_sc >= arm_high and top_s <= top_slope_threshold and top_cooldown_remaining == 0:
                # 共识过滤器：要求至少N个顶部敏感指标处于极端高位
                consensus_ok = True
                if consensus_aligned and top_consensus_min > 0:
                    extreme_count = 0
                    for ind_name, ind_series in consensus_aligned.items():
                        val = ind_series.get(date)
                        if val is not None and not np.isnan(val) and val > EXTREME_THRESHOLD:
                            extreme_count += 1
                    consensus_ok = extreme_count >= top_consensus_min

                # 广度背离分类器：广度健康(高分位)且情绪未极端 → 普涨牛市中继，降级不进入。
                # 这是为"情绪高但广度健康普涨行情"(如2020-07型)上的前瞻保险。
                breadth_ok = True
                if breadth_aligned is not None:
                    bp = breadth_aligned.get(date)
                    emo_now = emotion_aligned.get(date) if emotion_aligned is not None else None
                    emo_extreme = emo_now is not None and not np.isnan(emo_now) and emo_now >= breadth_bypass_emotion
                    if bp is not None and not np.isnan(bp) and bp >= breadth_healthy_pct and not emo_extreme:
                        breadth_ok = False

                if consensus_ok and breadth_ok:
                    state = 'near_top'
                    lookback_start = max(0, i - slope_window * 4)
                    peak = float(np.nanmax(top_values[lookback_start:i + 1]))
                    confirm_count = 0
                    half_escape_armed = False  # 新一段临界：半仓预警 latch 归零
                    # 轻仓逃顶：进入near_top时发出（逃顶单级时关闭）
                    if emit_light_escape_top:
                        light_signal = 'light_escape_top'
                    # 初始化peak_close
                    peak_close = float(cur_close) if cur_close is not None else 0.0
            elif (
                (score <= arm_low
                 or (deep_oversold_count_aligned is not None
                     and deep_oversold_count_aligned.get(date, 0) >= deep_oversold_min_count))
                and s >= bottom_slope_threshold and bottom_cooldown_remaining == 0
            ):
                # 抄底进入：主路径(等权分<=arm_low) 或 深跌并联通道(≥K个维度risk_pct<阈值)。
                # 并联通道默认关闭(deep_oversold_threshold=None)。进入后确认层完全不变。
                # 抄底斜率放宽（允许V型底），噪音由其他过滤器控制
                state = 'near_bottom'
                # 回看找到近期最低点作为初始谷值
                lookback_start = max(0, i - slope_window * 4)
                trough = float(np.nanmin(valid_values[lookback_start:i + 1]))
                confirm_count = 0
                near_days = 0
                # 轻仓抄底：进入near_bottom时发出（需通过最小跌幅检查）
                if cur_close is not None and rolling_high_aligned is not None:
                    rh_val = rolling_high_aligned.get(date)
                    if rh_val is not None and not np.isnan(rh_val) and rh_val > 0:
                        decline = (rh_val - cur_close) / rh_val
                        if decline >= light_min_decline:
                            light_signal = 'light_buy_bottom'
                    else:
                        light_signal = 'light_buy_bottom'
                else:
                    light_signal = 'light_buy_bottom'
                # 初始化trough_close
                trough_close = float(cur_close) if cur_close is not None else 1e9

        elif state == 'near_top':
            # 更新peak_close（追踪临界期指数最高价）
            if cur_close is not None and cur_close > peak_close:
                peak_close = float(cur_close)

            if top_cooldown_remaining > 0:
                state = 'normal'
                confirm_count = 0
            else:
                # 计算当前从临界期高点的跌幅（供两个触发器共用）
                drop_pct = 0.0
                if cur_close is not None and peak_close > 0:
                    drop_pct = (peak_close - cur_close) / peak_close

                # —— 触发器①：反应式硬止损（闪崩兜底）——
                # 热区内指数从临界高点跌≥hard_stop_from_peak，不等分数确认立即清仓。
                # 仅在 hard_stop_from_peak 启用时生效。
                hard_stop_fire = (
                    hard_stop_from_peak is not None
                    and drop_pct >= hard_stop_from_peak
                )

                # —— 触发器②：预防式确认逃顶（顶通道分数从峰值回落+连续确认）——
                if top_sc > peak:
                    peak = top_sc
                    confirm_count = 0
                confirm_fire = False
                if peak - top_sc >= _top_reversal:
                    confirm_count += 1
                    if confirm_count >= _top_confirm:
                        confirm_fire = True
                else:
                    confirm_count = 0

                # 分层逃顶：分数确认(条件1)成立但指数尚未从临界高点跌够
                # escape_min_price_drop(条件2)时，不直接清仓——
                #   escape_half_on_confirm=True：发一次 light_escape_top 半仓预警(→0.5)；
                #   escape_half_on_confirm=False：纯延迟(AND门，仅压制不减仓)。
                # 半仓预警用 half_escape_armed latch 保证每段只发一次——仓位由 fold 的
                # reduce-only(min)+前向填充维持在 0.5，无需逐日重发(否则信号被刷屏)。
                # 两种模式都不清零 confirm_count，留在 near_top 等价格兑现；价格一旦跌够
                # 即升级为 escape_top 清仓。hard_stop 闪崩兜底不受此约束。
                if (
                    confirm_fire
                    and escape_min_price_drop is not None
                    and drop_pct < escape_min_price_drop
                ):
                    if escape_half_on_confirm and not half_escape_armed:
                        light_signal = 'light_escape_top'
                        half_escape_armed = True
                    confirm_fire = False

                if hard_stop_fire or confirm_fire:
                    # 预防式逃顶仍受"别追跌"上限约束（跌太多则该轮交给硬止损/认错机制）；
                    # 硬止损本身就是为大跌设计的，不受该上限限制。
                    price_not_too_late = drop_pct <= max_drop_from_peak
                    if hard_stop_fire or price_not_too_late:
                        signal = 'escape_top'
                    # 逃顶后进入 post_escape 监控突破（需要价格数据）
                    if signal == 'escape_top' and close_aligned is not None and peak_close > 0:
                        state = 'post_escape'
                        # 参考价 = near_top期间最高价（也考虑近期lookback）
                        lookback_start = max(0, i - 60)
                        recent_high = float(close_aligned.iloc[lookback_start:i + 1].max()) if i > lookback_start else peak_close
                        escape_ref_price = max(peak_close, recent_high)
                        post_escape_days = 0
                        reentry_confirm_count = 0
                        reentry_cooled = False  # 新一段监控：退烧 latch 归零，重新等待过热消化
                    else:
                        state = 'normal'
                    top_cooldown_remaining = _top_cooldown
                    peak = 0.0
                    peak_close = 0.0
                    confirm_count = 0
                # 顶通道分数跌破撤离阈值且不在确认中 → 撤离临界
                if state == 'near_top' and top_sc < top_clear_threshold and confirm_count == 0:
                    state = 'normal'
                    confirm_count = 0

        elif state == 'near_bottom':
            near_days += 1
            # 更新trough_close（追踪临界期指数最低价）
            if cur_close is not None and cur_close < trough_close:
                trough_close = float(cur_close)

            if bottom_cooldown_remaining > 0:
                state = 'normal'
                confirm_count = 0
            else:
                if score < trough:
                    trough = score
                    confirm_count = 0

                triggered = False

                # === 路径A: Breadth thrust（涨停占比突破阈值）===
                if not triggered and limit_up_aligned is not None:
                    lu_val = limit_up_aligned.get(date)
                    if lu_val is not None and not np.isnan(lu_val):
                        # 涨停占比 > 阈值（limit_up_ratio是0-1，thrust是百分比）
                        if lu_val * 100 >= limit_up_thrust:
                            # 检查最大涨幅约束
                            rise_ok = True
                            if cur_close is not None and trough_close < 1e8:
                                rise_pct = (cur_close - trough_close) / trough_close
                                rise_ok = rise_pct <= max_rise_from_trough
                            if rise_ok:
                                signal = 'buy_bottom'
                                state = 'normal'
                                bottom_cooldown_remaining = _bottom_cooldown
                                trough = 100.0
                                trough_close = 1e9
                                confirm_count = 0
                                triggered = True

                # === 路径B: 分数回撤确认（现有逻辑）===
                if not triggered and score - trough >= _bottom_reversal:
                    # 价格确认：要求价格站上MA均线
                    price_ok = True
                    if close_aligned is not None and ma_aligned is not None:
                        c = close_aligned.get(date)
                        m = ma_aligned.get(date)
                        if c is not None and m is not None and not np.isnan(c) and not np.isnan(m):
                            price_ok = c >= m

                    if price_ok:
                        confirm_count += 1
                        if confirm_count >= _bottom_confirm:
                            # 检查最大涨幅约束
                            rise_ok = True
                            if cur_close is not None and trough_close < 1e8:
                                rise_pct = (cur_close - trough_close) / trough_close
                                rise_ok = rise_pct <= max_rise_from_trough
                            if rise_ok:
                                signal = 'buy_bottom'
                            # 无论是否发出重仓信号，都重置状态
                            state = 'normal'
                            bottom_cooldown_remaining = _bottom_cooldown
                            trough = 100.0
                            trough_close = 1e9
                            confirm_count = 0
                            triggered = True
                    else:
                        confirm_count = 0
                elif not triggered:
                    confirm_count = 0

                # 撤离检查1：分数涨过阈值且反弹未达触发值 → 撤离临界
                reversal_reached = (score - trough) >= _bottom_reversal
                if not triggered and state == 'near_bottom' and score > bottom_clear_threshold and confirm_count == 0 and not reversal_reached:
                    state = 'normal'
                    confirm_count = 0

                # 撤离检查2：超过最大临界天数 → 强制撤离（防止stuck状态）
                if not triggered and state == 'near_bottom' and near_days > bottom_max_near_days and confirm_count == 0:
                    state = 'normal'
                    confirm_count = 0

        elif state == 'post_escape':
            # 逃顶后监控：等待"过热消化(退烧) + 价格突破前高"才认错回补
            post_escape_days += 1

            # 退烧 latch：本段内顶通道分数一旦跌破退烧线，即记下"过热已消化"（不可逆）。
            # 用 top_sc(=max(主,情绪))与逃顶进入同源，语义一致。
            if top_sc < _reentry_cooling_thr:
                reentry_cooled = True

            if cur_close is not None and escape_ref_price > 0:
                # 退烧门控（require_cooling=True 时启用，根治"过热未消化时接刀"）：
                #   ① 已退烧：本段曾跌破退烧线（reentry_cooled）——过热消化是既成事实。
                #   ② 当天非顶区：top_sc < arm_high——堵住 latch 后二次过热又冲回顶区的接刀漏洞。
                # 两道都不满足就不累计突破确认。require_cooling=False 时恒放行（回到旧行为）。
                cooling_ok = (
                    not reentry_require_cooling
                    or (reentry_cooled and top_sc < arm_high)
                )
                # 决定性突破旁路：价格站上逃顶前高 *(1+bypass_pct) → 独立放行（绕过退烧门控）。
                # "决定性创新高"本身即"逃错"铁证，根治强势慢牛分数不退烧导致的回补焊死。
                if (
                    reentry_breakout_bypass_pct is not None
                    and escape_ref_price > 0
                    and cur_close is not None
                    and cur_close >= escape_ref_price * (1 + reentry_breakout_bypass_pct)
                ):
                    cooling_ok = True
                # 强制空仓期内不累计突破确认：给市场冷静期，期满后需重新连续突破前高，
                # 防止逃顶后剧烈震荡中的"假突破"立刻把仓位骗回去。cooldown=0 时此分支恒真。
                # enable_reentry=False 时彻底关闭回补（仅 post_escape 监控不触发回补），
                # 用于对照"砍掉认错回补"——逃顶后只待窗口期满回 normal，不会二次入场。
                if not enable_reentry or post_escape_days <= reentry_cooldown_days or not cooling_ok:
                    reentry_confirm_count = 0
                elif cur_close > escape_ref_price:
                    reentry_confirm_count += 1
                else:
                    reentry_confirm_count = 0

                # 连续N天突破前高 → 认错回补
                if reentry_confirm_count >= reentry_confirm_days:
                    signal = 're_entry'
                    state = 'post_reentry'
                    reentry_ref_price = escape_ref_price
                    post_reentry_days = 0
                    reentry_stop_count = 0
                    # 清除逃顶冷却期（逃顶判断错误，不应阻止后续信号）
                    top_cooldown_remaining = 0

            # 监控窗口到期 → 接受逃顶判断，回归正常
            if state == 'post_escape' and post_escape_days >= reentry_window:
                state = 'normal'
                escape_ref_price = 0.0
                reentry_confirm_count = 0

            # post_escape期间仍可响应抄底信号（若市场急跌）
            if state == 'post_escape' and score <= arm_low and bottom_cooldown_remaining == 0:
                state = 'near_bottom'
                lookback_start = max(0, i - slope_window * 4)
                trough = float(np.nanmin(valid_values[lookback_start:i + 1]))
                confirm_count = 0
                near_days = 0
                trough_close = float(cur_close) if cur_close is not None else 1e9
                escape_ref_price = 0.0

        elif state == 'post_reentry':
            # 回补后：止损检查 + 正常逃顶检测（无保护期）
            post_reentry_days += 1

            # 超时出口（与 post_escape 对称）：回补后持有 reentry_window 天既未止损也未再逃顶，
            # 说明回补对了、趋势确实延续，回归正常持有。否则会永久滞留 post_reentry（对后续
            # 抄底信号失明）。详见 bug-review-decisions.md M3。
            if post_reentry_days >= reentry_window:
                state = 'normal'
                reentry_ref_price = 0.0
                reentry_stop_count = 0

            # 止损检查：价格跌破参考价
            if state == 'post_reentry' and cur_close is not None and reentry_ref_price > 0:
                if cur_close < reentry_ref_price:
                    reentry_stop_count += 1
                else:
                    reentry_stop_count = 0

                # 连续N天跌破 → 止损出场
                if reentry_stop_count >= reentry_stop_days:
                    signal = 're_entry_stop'
                    state = 'normal'
                    top_cooldown_remaining = _top_cooldown
                    reentry_ref_price = 0.0
                    reentry_stop_count = 0

            # 正常逃顶检测：如果顶通道分数高且斜率转负 → near_top
            if state == 'post_reentry' and top_sc >= arm_high and top_s <= top_slope_threshold:
                consensus_ok = True
                if consensus_aligned and top_consensus_min > 0:
                    extreme_count = 0
                    for ind_name, ind_series in consensus_aligned.items():
                        val = ind_series.get(date)
                        if val is not None and not np.isnan(val) and val > EXTREME_THRESHOLD:
                            extreme_count += 1
                    consensus_ok = extreme_count >= top_consensus_min
                if consensus_ok:
                    state = 'near_top'
                    lookback_start = max(0, i - slope_window * 4)
                    peak = float(np.nanmax(top_values[lookback_start:i + 1]))
                    confirm_count = 0
                    peak_close = float(cur_close) if cur_close is not None else 0.0
                    if emit_light_escape_top:
                        light_signal = 'light_escape_top'
                    reentry_ref_price = 0.0

        signals.append(signal)
        light_signals.append(light_signal)
        states.append(state)
        # ref_value 只记分数域的峰/谷跟踪值（0-100）；价格域参考线（逃顶最高价、
        # 回补止损价）属于 post_escape/post_reentry，不混进这一列，留 NaN 保持语义一致。
        ref_values.append(peak if 'top' in state else
                          (trough if 'bottom' in state else np.nan))

    result = pd.DataFrame({
        'signal': signals,
        'signal_light': light_signals,
        'state': states,
        'ref_value': ref_values,
    }, index=valid.index)

    # ---- 指标无关的"最后保险"：移动止损叠加（事件式右侧兜底）----
    # 设计原则（详见 SIGNAL_TRAIL_STOP_PCT 注释 + compare_insurance.py 实证）：
    #   - 独立于状态机是否进入临界：状态机漏掉的顶（分数未到75）由它兜底。
    #   - 事件式：仅在"收盘从250日高点回撤首次≥20%"的当天触发一次 escape_top，
    #     且仅当此刻仍持仓（long）。持续低于阈值的日子不重复发信号。
    #   - 先触发者生效：若状态机当天已发 escape_top/re_entry_stop，则不重复。
    #   - 重入完全交给状态机（buy_bottom/re_entry），移动止损只做"被动清仓"。
    # 用独立 state 标签 'trail_stop' 标注，便于审计与可视化区分两类逃顶来源。
    if trail_stop_pct is not None and trail_high_aligned is not None and close_aligned is not None:
        drawdown = (close_aligned - trail_high_aligned) / trail_high_aligned
        breached = drawdown <= -trail_stop_pct  # 是否处于"已回撤≥阈值"态
        long_pos = True  # 跟随者的持仓态：初始满仓
        prev_breached = False
        sig_col = result['signal'].copy()
        state_col = result['state'].copy()
        for date in valid.index:
            s = sig_col.loc[date]
            # 状态机信号先更新持仓态
            if s in ('escape_top', 're_entry_stop'):
                long_pos = False
            elif s in ('buy_bottom', 're_entry'):
                long_pos = True
            # 移动止损事件：回撤首次跌破阈值（False→True）的当天
            b = bool(breached.loc[date]) if date in breached.index and not pd.isna(breached.loc[date]) else False
            if b and not prev_breached and long_pos and s == 'none':
                sig_col.loc[date] = 'escape_top'
                state_col.loc[date] = 'trail_stop'
                long_pos = False
            prev_breached = b
        result['signal'] = sig_col
        result['state'] = state_col

    # Reindex to full original index
    result = result.reindex(score_series.index)
    result['signal'] = result['signal'].fillna('none')
    result['signal_light'] = result['signal_light'].fillna('none')
    result['state'] = result['state'].fillna('normal')

    # ==== 权威事件列（event）：单一事实源 ====
    # 状态机此前把"发生了什么"拆散进 signal/signal_light/state 三列，下游(回测/绘图/
    # live)只能反推。这里在三列定稿后、调用 derive_event_column 一次性合成权威 event：
    # 下游从此只读 event 查表定仓，不再反推。event 是三列的纯函数 → 三列逐日 0-diff 由
    # 构造保证（等价性 harness 验证）。映射定义见 derive_event_column / EVENT_TO_POSITION。
    result['event'] = derive_event_column(
        result['signal'], result['signal_light'], result['state'],
    )
    return result


def generate_cycle_report(
    indicators: Dict[str, pd.Series],
    index_close: pd.Series,
    limit_up_ratio: Optional[pd.Series] = None,
    breadth_pct: Optional[pd.Series] = None,
    ema_span_max: int = EMA_SPAN_MAX,
    ema_span_min: int = EMA_SPAN_MIN,
    tail_power: float = TAIL_POWER,
    hard_stop_from_peak: Optional[float] = SIGNAL_HARD_STOP_FROM_PEAK,
    trail_stop_pct: Optional[float] = SIGNAL_TRAIL_STOP_PCT,
) -> Dict:
    """生成完整的周期定位报告

    Args:
        indicators: 所有指标 {name: risk_percentage series}
        index_close: 上证指数收盘价
        limit_up_ratio: 可选，每日涨停占比(0-1)用于breadth thrust确认
        ema_span_max: 正常EMA窗口
        ema_span_min: 极端共识时最短窗口
        tail_power: 尾部放大幂次

    Returns:
        Dict: 完整报告（最新值 + 时间序列）
    """
    score = compute_cycle_score(
        indicators, ema_span_max=ema_span_max,
        ema_span_min=ema_span_min, tail_power=tail_power,
    )
    phases = classify_market_phases(score)

    # 情绪子系统分数：仅用情绪/资金类指标，同款EMA+尾部放大。
    # 用于逃顶并联通道，抓2018/2021被等权稀释的结构顶。
    emotion_inds = {k: indicators[k] for k in SIGNAL_EMOTION_INDICATORS if k in indicators}
    emotion_score = None
    if len(emotion_inds) >= 2:
        emotion_score = compute_cycle_score(
            emotion_inds, ema_span_max=ema_span_max,
            ema_span_min=ema_span_min, tail_power=tail_power,
        )

    valid = score.dropna()
    if valid.empty:
        raise ValueError("No valid cycle score data")

    latest_date = valid.index[-1]
    latest_score = float(valid.iloc[-1])
    latest_phase = classify_phase(latest_score)

    # 各指标最新值
    indicator_panel = {}
    for name, series in indicators.items():
        s = series[~series.index.duplicated(keep='last')]
        if latest_date in s.index:
            indicator_panel[name] = float(s.loc[latest_date])
        else:
            # 找最近的日期
            idx = s.index.get_indexer([latest_date], method='nearest')
            if idx[0] >= 0:
                indicator_panel[name] = float(s.iloc[idx[0]])

    # 生成逃顶/抄底信号（传入价格、指标、涨停占比、情绪分数、广度分位）
    signals_df = generate_signals(
        score, index_close=index_close, indicators=indicators,
        limit_up_ratio=limit_up_ratio, emotion_score=emotion_score,
        breadth_pct=breadth_pct, hard_stop_from_peak=hard_stop_from_peak,
        trail_stop_pct=trail_stop_pct,
    )

    # 当前信号状态
    latest_signal = signals_df.loc[latest_date, 'signal'] if latest_date in signals_df.index else 'none'
    latest_light = signals_df.loc[latest_date, 'signal_light'] if latest_date in signals_df.index else 'none'
    latest_state = signals_df.loc[latest_date, 'state'] if latest_date in signals_df.index else 'normal'

    # 最近一次重仓信号
    fired = signals_df[signals_df['signal'] != 'none']
    last_signal_info = None
    if not fired.empty:
        last_date = fired.index[-1]
        last_signal_info = {
            "date": last_date.strftime("%Y-%m-%d"),
            "type": fired.loc[last_date, 'signal'],
            "tier": "heavy",
        }

    # 最近一次轻仓信号
    light_fired = signals_df[signals_df['signal_light'] != 'none']
    last_light_info = None
    if not light_fired.empty:
        last_date = light_fired.index[-1]
        last_light_info = {
            "date": last_date.strftime("%Y-%m-%d"),
            "type": light_fired.loc[last_date, 'signal_light'],
            "tier": "light",
        }

    # ==== 权威事件 + 当前仓位（live 链路此前完全缺失"仓位"概念，这里补齐）====
    # event 是单一事实源；仓位 = event 查 EVENT_TO_POSITION 后前向填充。方向守卫（买只增、
    # 轻仓逃顶只减、撤离只清轻仓）与回测端 run_position_backtest 完全同源，避免两条链路漂移。
    event_series = signals_df['event'] if 'event' in signals_df.columns else pd.Series(
        'none', index=signals_df.index)
    position_series = _events_to_positions(event_series)
    latest_event = event_series.loc[latest_date] if latest_date in event_series.index else 'none'
    latest_position = float(position_series.loc[latest_date]) if latest_date in position_series.index else 0.0
    fired_event = event_series[event_series != 'none']
    last_event_info = None
    if not fired_event.empty:
        last_date = fired_event.index[-1]
        last_event_info = {
            "date": last_date.strftime("%Y-%m-%d"),
            "type": fired_event.loc[last_date],
            "target_position": EVENT_TO_POSITION.get(fired_event.loc[last_date]),
        }

    return {
        "date": latest_date.strftime("%Y-%m-%d"),
        "cycle_score": round(latest_score, 1),
        "phase": latest_phase["phase"],
        "emoji": latest_phase["emoji"],
        "advice": latest_phase["advice"],
        "indicator_panel": indicator_panel,
        "indicator_count": len(indicators),
        # 重仓信号
        "current_signal": latest_signal,
        "signal_state": latest_state,
        "last_signal": last_signal_info,
        # 轻仓信号
        "current_light_signal": latest_light,
        "last_light_signal": last_light_info,
        # 权威事件 + 当前仓位（单一事实源）
        "current_event": latest_event,
        "current_position": latest_position,
        "last_event": last_event_info,
        "signals_df": signals_df,
        # 完整时间序列（供可视化）
        "score_series": score,
        "emotion_score_series": emotion_score,
        "phases_df": phases,
    }


def validate_against_history(
    score_series: pd.Series,
    labeled_events: pd.DataFrame,
    tolerance_days: int = 30,
) -> Dict:
    """验证周期分数在历史顶底事件处的表现

    检查：顶部事件附近分数是否 > 60，底部事件附近分数是否 < 40

    Args:
        score_series: compute_cycle_score 输出
        labeled_events: 顶底事件 (含 type, date 列)
        tolerance_days: 查找窗口

    Returns:
        Dict: 每个事件的匹配结果
    """
    valid_score = score_series.dropna()
    results = []

    for _, event in labeled_events.iterrows():
        event_date = pd.to_datetime(event["date"])
        event_type = event["type"]

        # 在容忍窗口内找分数
        mask = (valid_score.index >= event_date - pd.Timedelta(days=tolerance_days)) & \
               (valid_score.index <= event_date + pd.Timedelta(days=tolerance_days))
        window_scores = valid_score[mask]

        if window_scores.empty:
            results.append({
                "date": event_date, "type": event_type,
                "score": None, "matched": False, "reason": "no_data",
            })
            continue

        if event_type == "top":
            score_at_event = window_scores.max()
            matched = score_at_event > 60
        else:
            score_at_event = window_scores.min()
            matched = score_at_event < 40

        results.append({
            "date": event_date, "type": event_type,
            "score": round(float(score_at_event), 1),
            "matched": matched,
        })

    captured = [r for r in results if r["matched"]]
    missed = [r for r in results if not r["matched"]]
    total = len(labeled_events)

    return {
        "results": results,
        "captured": captured,
        "missed": missed,
        "capture_rate": len(captured) / max(total, 1),
        "total_events": total,
    }
