"""
项目配置文件

集中管理所有配置项，包括数据路径、API密钥、分析参数等。
"""

import os
from pathlib import Path

# ==================== 你只需要改这里的配置 ====================
# XBX数据路径配置
# 用于存储XBX本地股票交易数据的根目录
# 数据文件位于 {XBX_data_path}/stock-trading-data-pro/ 目录下
# 可用环境变量 XBX_DATA_PATH 覆盖；默认指向本机 CommonData 目录
XBX_data_path = os.environ.get("XBX_DATA_PATH", "/Users/kun/Desktop/AGdata")

# 【最简单用法】把下面这行的 key 直接写死，跑 auto_runner 就会自动推送：
#   WECOM_ROBOT_KEY = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
# key = 企业微信群机器人 webhook 地址里 ?key= 后面那串。
# 写死后无需任何命令行参数，NOTIFY_CHANNEL 会自动变成 'robot' 直接推。
WECOM_ROBOT_KEY = "2e9d429d-2e21-40e3-8a84-b098c9b30f8e"  # ← 直接把 key 填这里即可

# 触发时间（小时），工作日此时间后开始执行
AUTO_RUNNER_TRIGGER_HOUR = 18

# 数据就绪截止点（小时，24制）。
# 默认要求"目标交易日"的所有必需数据源全部到齐才计算指标；
# 若到此时间点仍未到齐（如 XBX 离线数据当天结构性晚到，或遇节假日），
# 则回退到指标基准日（各源最新共同日期）跑一版尽力而为的信号，避免永久卡死。
AUTO_RUNNER_DATA_CUTOFF_HOUR = 23

# ==================== 你只需要改这里的配置 ====================







# ==================== 项目路径配置 ====================
# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 数据目录配置
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw_data"    # 原始数据目录（HTTP请求获取的数据）
PROCESSED_DATA_DIR = DATA_DIR / "processed"  # 处理后数据目录
RESULTS_DIR = DATA_DIR / "results"      # 分析结果目录
PIC_DIR = DATA_DIR / "pic"              # 图片存储目录

# 程序目录
PROGRAM_DIR = PROJECT_ROOT / "program"

# XBX数据加载并行任务数
# -1表示使用所有CPU核心
# 设置较小的数字（如4或8）可以减少内存占用，避免内存溢出
XBX_DATA_N_JOBS = -1

# 确保目录存在
for directory in [RAW_DATA_DIR, PROCESSED_DATA_DIR, RESULTS_DIR, PIC_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ==================== 指标计算配置 ====================
# 数据加载的开始日期（格式：YYYY-MM-DD）
# 这是从数据源加载原始数据的起始时间
# 说明：
#   - 用于指定从API或本地文件加载数据时的开始日期
#   - 某些指标需要Rolling窗口计算（如大盘拥挤度需要10年历史数据），因此需要更早的数据
#   - 此日期应该早于INDICATOR_START_DATE，以确保有足够的历史数据用于计算
#   - 例如：如果指标开始日期是2013-06-01，且需要10年Rolling窗口，则数据开始日期应至少是2003年
DATA_START_DATE = "2012-01-01"

# 指标计算的统一开始日期（格式：YYYY-MM-DD）
# 这是指标计算结果的输出起始时间
# 说明：
#   - 所有指标的计算结果（如分位数、风险百分比）都会从此日期开始输出
#   - 所有指标必须使用此日期作为结果过滤的默认日期，确保数据一致性
#   - 此日期之前的数据只用于计算（如作为Rolling窗口的历史数据），不会出现在最终结果中
#   - 修改此日期会影响所有指标的输出范围，但不会影响数据加载范围
INDICATOR_START_DATE = "2014-01-01"

# MACD背离指标配置
# 是否只显示放量背离（真的背离），将普通背离视为噪音
# True: 只显示放量背离，过滤掉普通背离（推荐，减少噪音）
# False: 显示所有背离（包括普通背离和放量背离）
MACD_DIVERGENCE_ONLY_VOLUME = True

# PE估值指标配置
# PE估值类型，可选值：
# - "等权滚动市盈率"：等权滚动市盈率（默认）
# - "滚动市盈率"：滚动市盈率
# - "等权静态市盈率"：等权静态市盈率
# - "静态市盈率"：静态市盈率
# - "滚动市盈率中位数"：滚动市盈率中位数
# - "静态市盈率中位数"：静态市盈率中位数
PE_VALUATION_TYPE = "滚动市盈率"

# PE估值计算方式，可选值：
# - "rolling"：滚动方案（默认），每行只使用到当前为止的历史数据计算分位数（expanding窗口）
# - "full_history"：全历史方案，所有行都使用完整的历史数据集计算分位数
# 说明：
#   - rolling方案：分位数是动态的，随着时间推移，历史数据逐渐增加，分位数会变化
#   - full_history方案：分位数基于全部历史数据，所有时间点的分位数都使用相同的历史数据集
PE_VALUATION_METHOD = "rolling"

# PE估值异常值处理方式，用于解决历史极端值（如牛市高PE）对分位数计算的影响
# 可选值：
# - "none"：不处理异常值（默认），使用全部历史数据计算分位数
# - "winsorize"：Winsorization截断法，去除最高和最低的极端值（如各5%），只使用中间90%的数据
# - "rolling_window"：滚动窗口法，使用固定长度的滚动窗口（如5年、10年）计算分位数，避免早期极端值影响
# - "iqr"：IQR方法，使用四分位距（IQR）识别并去除异常值，只使用正常范围内的数据
# 说明：
#   - none：适合数据质量好、极端值较少的情况
#   - winsorize：业界常用方法，简单有效，去除极端值后计算分位数
#   - rolling_window：适合希望使用近期数据、避免早期极端值影响的情况
#   - iqr：统计上更严谨，自动识别异常值
PE_VALUATION_OUTLIER_METHOD = "iqr"

# PE估值异常值处理参数
# 当PE_VALUATION_OUTLIER_METHOD为"winsorize"时：
#   - 去除最高和最低各winsorize_percentile%的数据（默认5%，即各去除5%，保留中间90%）
PE_VALUATION_WINSORIZE_PERCENTILE = 10.0  # 百分比，如5.0表示5%

# 当PE_VALUATION_OUTLIER_METHOD为"rolling_window"时：
#   - 使用最近rolling_window_years年的数据计算分位数（默认10年）
PE_VALUATION_ROLLING_WINDOW_YEARS = 15  # 年数，如10表示10年

# 当PE_VALUATION_OUTLIER_METHOD为"iqr"时：
#   - 使用IQR倍数识别异常值，超过Q3 + iqr_multiplier * IQR 或 低于Q1 - iqr_multiplier * IQR 的值被视为异常值
#   - 默认1.5（标准IQR方法），可以调整为2.0或3.0以更严格或更宽松
PE_VALUATION_IQR_MULTIPLIER = 3  # IQR倍数，如1.5表示1.5倍IQR

# 抱团率指标配置
# 滚动Z-score标准化窗口年数
# 说明：
#   - 抱团率（前5%个股成交额占比）存在结构性上升趋势（A股从2600只扩容到5400只）
#   - 使用滚动窗口Z-score标准化消除趋势漂移
#   - 默认使用过去3年（约750个交易日）的数据计算Z-score和分位数
HERDING_RATE_ROLLING_WINDOW_YEARS = 3

# 大盘拥挤度指标配置
# 分位数计算滚动窗口年数
# 说明：
#   - 拥挤度指标在中长期有中枢抬升趋势（2019年后），导致全历史分位数长期偏高
#   - 使用滚动窗口分位数（Rolling Percentile）可以适应市场结构变化
#   - 默认使用过去10年（约2500个交易日）的数据来计算当前拥挤度的历史排位
MARKET_CROWDEDNESS_ROLLING_WINDOW_YEARS = 10

# ==================== 启用指标配置 ====================
# 生产链路（run_indicators 注册 + 周期信号 B）真正消费的开关：
# 只有列在这里的指标才会被注册、计算并写出 processed CSV，
# 而这些 CSV 正是等权周期分数（cycle_signal / cycle_position）的输入。
#
# 注意：周期分数是“等权平均”，不存在指标权重的概念，
# 控制生产行为的是“指标名是否在本列表中”。
ENABLED_INDICATORS = [
    "hs300_equity_premium",
    "hs300_pe_valuation",
    "sz50_pe_valuation",
    "zz500_pe_valuation",
    "zz1000_pe_valuation",
    "price_percentile",
    "market_turnover_percentile",
    "ma250_bias",
    "market_crowdedness",
    "below_net_asset",
    "herding_rate",
]

# ==================== 综合评分阈值配置 ====================
# 综合评分的高风险和低风险阈值
# 范围：0.0 - 1.0
# 说明：
#   - 高风险阈值：当综合得分高于此值时，视为高风险区域（如0.85表示前15%）
#   - 中度风险阈值：当综合得分高于此值时，视为中度风险区域（如0.60表示前40%）
#   - 低风险阈值：当综合得分低于此值时，视为低风险区域（如0.15表示后15%）
#   - 修改记录：从默认的0.9/0.1 (+10/-10) 修改为 0.85/0.15 (+15/-15)
#   - 新增记录：新增中度风险阈值 0.60
WEIGHTED_SCORE_HIGH_THRESHOLD = 0.8
WEIGHTED_SCORE_MEDIUM_THRESHOLD = 0.60
WEIGHTED_SCORE_LOW_THRESHOLD = 0.20

# 风险阶段最小持续天数（用于图表显示过滤）
# 持续天数少于此值的风险阶段将在图表右侧信息区域中被过滤掉（当前阶段除外）
# 默认：10天
# 说明：
#   - 过滤掉持续时间过短的风险阶段，减少噪音，突出重要阶段
#   - 当前阶段（最新阶段）无论持续多少天都会显示
#   - 设置为0表示显示所有阶段
RISK_PERIOD_MIN_DAYS = 5

# ==================== 多指数回测配置 ====================
# 目标交易指数：信号系统产生的信号将应用到这些指数上进行回测
TARGET_INDICES = {
    "上证指数": {
        "index_code": "sh000001",
        "display_name": "上证指数",
        "color": "#e74c3c",
    },
    "沪深300": {
        "index_code": "sh000300",
        "display_name": "沪深300",
        "color": "#3498db",
    },
}

# 各信号对应的目标仓位（占可用资金的比例）
# 信号形成有序的仓位生命周期：
#   空仓(0%) → 轻仓抄底(30%) → 重仓抄底(100%) → 持有
#   持有(100%) → 轻仓逃顶(50%) → 重仓逃顶(0%) → 空仓
POSITION_LEVELS = {
    "light_buy_bottom": 0.30,
    "heavy_buy_bottom": 1.00,
    "light_escape_top": 0.50,
    "heavy_escape_top": 0.00,
}

# ==================== 自动调度器配置 ====================
# 完成标记文件目录
COMPLETION_FLAGS_DIR = DATA_DIR / "completion_flags"

# 轮询间隔（秒），数据未就绪时多久重试一次
AUTO_RUNNER_POLL_INTERVAL = 3600


# 自动调度器日志目录
AUTO_RUNNER_LOG_DIR = DATA_DIR / "auto_runner_logs"

# 邮件功能开关（预留，暂不实现）
EMAIL_ENABLED = False

# ==================== 消息推送配置（企业微信机器人）====================
# key 解析：环境变量优先，其次用上面写死的值
_WECOM_KEY = os.environ.get("WECOM_ROBOT_KEY", "").strip() or WECOM_ROBOT_KEY.strip()
# info：常规消息推送   warn：异常告警推送（可分别用 *_INFO / *_WARN 环境变量区分）
WECOM_ROBOT_KEYS = {
    "info": os.environ.get("WECOM_ROBOT_KEY_INFO", "").strip() or _WECOM_KEY,
    "warn": os.environ.get("WECOM_ROBOT_KEY_WARN", "").strip() or _WECOM_KEY,
}

# 完成后如何推送结果：
#   'none'  不推送
#   'email' 走邮件（预留接口）
#   'robot' 推送到企业微信机器人（文本摘要 + 周期定位图）
#   'both'  邮件 + 机器人都推
# 默认：只要配了 key（写死或环境变量）就自动走 'robot' 直接推；否则 'none'。
# 可用环境变量 NOTIFY_CHANNEL 或命令行 --notify 覆盖。
NOTIFY_CHANNEL = os.environ.get(
    "NOTIFY_CHANNEL", "robot" if _WECOM_KEY else "none"
).strip().lower()

# 推送请求代理（如需翻墙/内网代理时填，形如 {"https": "http://127.0.0.1:7890"}）。
# 默认不走代理。可用环境变量 NOTIFY_HTTPS_PROXY 注入。
_NOTIFY_HTTPS_PROXY = os.environ.get("NOTIFY_HTTPS_PROXY", "").strip()
NOTIFY_PROXIES = {"https": _NOTIFY_HTTPS_PROXY} if _NOTIFY_HTTPS_PROXY else None
