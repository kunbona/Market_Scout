"""
quant/daily_compute.py — 每日批量计算任务

统一入口: run_daily_compute(trade_date: str = None)
trade_date 为 None 时，调用 loader.get_latest_trade_date() 自动获取。

每个子任务用 try/except 包裹，单个任务失败不影响其他任务。
所有写入操作通过 db/storage.py 中的函数完成。
"""

import logging
from pathlib import Path

import pandas as pd

from quant.loader import DATA_ROOT, get_factor_slice

logger = logging.getLogger(__name__)


# ── 辅助：加载申万行业映射 ────────────────────────────────────────────────────

def _load_industry_map(trade_date: str) -> dict:
    """
    返回 {code: industry_l1} 的字典，用于联表补充行业信息。
    优先从 申万行业.parquet 的指定日期截面获取；
    如果该日期没有数据，取最近可用日期。
    """
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / "申万行业.parquet"
    if not parquet_path.exists():
        return {}
    try:
        df = pd.read_parquet(parquet_path, columns=["trade_date", "code", "新版申万一级行业名称"])
        df["trade_date"] = df["trade_date"].astype(str)
        slice_df = df[df["trade_date"] == trade_date]
        if slice_df.empty:
            # 取最近日期
            latest = df["trade_date"].max()
            slice_df = df[df["trade_date"] == latest]
        return dict(zip(slice_df["code"], slice_df["新版申万一级行业名称"]))
    except Exception as e:
        logger.warning("[daily_compute] 加载行业映射失败: %s", e)
        return {}


def _load_name_map(trade_date: str) -> dict:
    """
    返回 {code: stock_name} 的字典。
    从 stock-trading-data-pro 读取会很慢，改为从 涨跌幅相关因子.parquet
    联合 涨停相关因子.parquet 无法取到名称。
    实际从 stock-trading-data-pro 中按代码逐个读取代价高，
    这里改为扫描目录获取名称列——仅读首行，效率尚可。

    由于逐文件读取慢，此函数使用缓存策略：
    只在首次调用时建立一次全量映射（内存占用小，仅 code+name 两列）。
    """
    # 尝试从 申万行业 parquet 获取，该文件只有 code 没有名称
    # 降级：从 stock-trading-data-pro 目录取最后一行（有 stock_name 列）
    # 实际上从 CSV 的第2行（skiprows=1 后的第1行）就能拿到代码和名称
    # 性能权衡：全量加载约 5000+ 文件代价极高，
    # 优先用涨停因子截面匹配后，在写入时 name 留空，使用侧可通过 code 二次查询
    # 这里实现一个轻量版本：只扫描一个已知样本文件，全量映射用懒加载
    try:
        # 尝试从 stock-money-flow-xbx 获取名称映射（如果该 parquet 含 stock_name）
        # 否则从 涨跌幅相关因子 + 申万行业 仅能得到 code
        # 最实用的方案：读取全量 stock-trading-data-pro 下各 CSV 首行
        # 使用 pandas read_csv 只读 2 行，避免全量加载
        trading_dir = DATA_ROOT / "stock-trading-data-pro"
        name_map = {}
        for csv_file in trading_dir.glob("*.csv"):
            try:
                row = pd.read_csv(
                    csv_file, encoding="GBK", skiprows=1,
                    usecols=["股票代码", "股票名称"], nrows=1,
                )
                if not row.empty:
                    code = str(row.iloc[0]["股票代码"]).strip()
                    name = str(row.iloc[0]["股票名称"]).strip()
                    name_map[code] = name
            except Exception:
                pass
        return name_map
    except Exception as e:
        logger.warning("[daily_compute] 加载名称映射失败: %s", e)
        return {}


# ── 任务1：市场情绪日度指标 ───────────────────────────────────────────────────

def compute_market_emotion(trade_date: str) -> None:
    """
    计算市场情绪日度指标并写入 market_emotion 表。

    字段来源：涨停相关因子.parquet
      - 收盘涨停 (int, 1=涨停)
      - 是否炸板 (int, 1=炸板)
      - 连板次数 (int)
    """
    from db.storage import upsert_market_emotion

    df = get_factor_slice("涨停相关因子", trade_date)
    if df.empty:
        logger.warning("[daily_compute] market_emotion: %s 无数据", trade_date)
        return

    zt_total = int(df["收盘涨停"].sum())
    zb_total = int(df["是否炸板"].sum())
    max_lianzban = int(df["连板次数"].max()) if "连板次数" in df.columns else 0

    # 昨日涨停今日溢价：需要跨两日联表计算，字段复杂，暂设 None
    zt_yesterday_premium = None

    # 跌停数：涨停相关因子 parquet 中无跌停字段
    # TODO: 可从 涨跌幅相关因子.parquet 中筛选 pct_chg_1 <= -0.099 (跌停近似)
    dt_total = 0

    zb_rate = zb_total / (zt_total + zb_total) if (zt_total + zb_total) > 0 else 0.0

    upsert_market_emotion(
        trade_date, zt_total, dt_total, zb_total,
        max_lianzban, zt_yesterday_premium, zb_rate,
    )
    logger.info(
        "[daily_compute] market_emotion %s: zt=%d zb=%d max_lb=%d zb_rate=%.3f",
        trade_date, zt_total, zb_total, max_lianzban, zb_rate,
    )


# ── 任务2：板块涨停密度 ───────────────────────────────────────────────────────

def compute_sector_zt_density(trade_date: str) -> None:
    """
    按申万一级行业计算涨停密度并写入 sector_zt_density 表。

    字段来源:
      - 涨停相关因子.parquet: 收盘涨停, 是否炸板, 连板次数
      - 申万行业.parquet: 新版申万一级行业名称（联表）
    zt_density = 涨停股数 / 该行业总股数
    """
    from db.storage import insert_sector_zt_density

    df = get_factor_slice("涨停相关因子", trade_date)
    if df.empty:
        logger.warning("[daily_compute] sector_zt_density: %s 无数据", trade_date)
        return

    industry_map = _load_industry_map(trade_date)
    if not industry_map:
        logger.warning("[daily_compute] sector_zt_density: 行业映射为空，跳过")
        return

    df = df.copy()
    df["industry"] = df["code"].map(industry_map).fillna("未知")

    # 按行业分组统计
    grouped = df.groupby("industry")
    total_per_ind = grouped["code"].count()
    zt_per_ind = grouped["收盘涨停"].sum()
    max_lb_per_ind = grouped["连板次数"].max() if "连板次数" in df.columns else total_per_ind * 0

    for industry in total_per_ind.index:
        total = int(total_per_ind[industry])
        zt_cnt = int(zt_per_ind[industry])
        max_lb = int(max_lb_per_ind.get(industry, 0)) if not max_lb_per_ind.empty else 0
        density = zt_cnt / total if total > 0 else 0.0

        insert_sector_zt_density(trade_date, industry, zt_cnt, density, max_lb)

    logger.info("[daily_compute] sector_zt_density %s: %d 个行业写入", trade_date, len(total_per_ind))


# ── 任务3：板块资金流加速度 ──────────────────────────────────────────────────

def compute_sector_flow_acceleration(trade_date: str) -> None:
    """
    TODO: 按申万行业聚合资金流，计算 3日均值 / 20日均值 (inst_inflow_3d_vs_20d)。

    当前跳过实现，原因:
      - 资金流相关因子.parquet 字段为「机构净买入占比」等截面值，
        非累计额度，需要跨日聚合后才能计算加速度。
      - 跨多日读取 parquet 性能代价较高，需要设计增量缓存方案后再实现。

    依赖: 资金流相关因子.parquet 字段: 机构净买入占比, 大户净买入占比, 等
    写入目标: 暂无独立表，未来可扩展 sector_flow_acceleration 表。
    """
    # TODO: 实现板块资金流加速度计算
    #   1. 读取 trade_date 前 20 个交易日的资金流截面
    #   2. 按申万一级行业聚合「机构净买入占比」均值
    #   3. 计算 mean_3d / mean_20d 作为加速度指标
    pass


# ── 任务4：成交额异动 ─────────────────────────────────────────────────────────

def compute_volume_breakout(trade_date: str) -> None:
    """
    筛选成交额异动股票并写入 volume_breakout 表。

    字段来源: 成交额相关因子.parquet
      - amount_mean_5: 近5日均成交额
      - amount_mean_20: 近20日均成交额
    筛选条件: amount_mean_5 / amount_mean_20 > 2.0
    联表: 申万行业.parquet (行业), stock-trading-data-pro CSV (股票名称，按需)
    """
    from db.storage import insert_volume_breakout

    df = get_factor_slice("成交额相关因子", trade_date)
    if df.empty:
        logger.warning("[daily_compute] volume_breakout: %s 无数据", trade_date)
        return

    # 过滤无效行
    valid = df.dropna(subset=["amount_mean_5", "amount_mean_20"])
    valid = valid[valid["amount_mean_20"] > 0].copy()
    valid["ratio_5_20"] = valid["amount_mean_5"] / valid["amount_mean_20"]
    breakout = valid[valid["ratio_5_20"] > 2.0].copy()

    if breakout.empty:
        logger.info("[daily_compute] volume_breakout %s: 无异动股票", trade_date)
        return

    industry_map = _load_industry_map(trade_date)

    # 股票名称：优先从 stock-trading-data-pro 取，批量读取代价高，
    # 此处仅读取异动股票（数量通常 < 100）对应的 CSV
    trading_dir = DATA_ROOT / "stock-trading-data-pro"

    inserted = 0
    for _, row in breakout.iterrows():
        code = str(row["code"])
        industry = industry_map.get(code, "未知")
        ratio = float(row["ratio_5_20"])
        amount_5d = float(row["amount_mean_5"])

        # 获取股票名称
        stock_name = ""
        csv_path = trading_dir / f"{code}.csv"
        if csv_path.exists():
            try:
                name_row = pd.read_csv(
                    csv_path, encoding="GBK", skiprows=1,
                    usecols=["股票名称"], nrows=1,
                )
                if not name_row.empty:
                    stock_name = str(name_row.iloc[0]["股票名称"]).strip()
            except Exception:
                pass

        insert_volume_breakout(trade_date, code, stock_name, industry, ratio, amount_5d)
        inserted += 1

    logger.info("[daily_compute] volume_breakout %s: %d 只股票写入", trade_date, inserted)


# ── 任务5：筹码状态 ───────────────────────────────────────────────────────────

def compute_chip_status(trade_date: str) -> None:
    """
    计算目标股票筹码状态并写入 chip_status 表。

    目标股票范围: 当日涨停池 + volume_breakout 名单（取自 DB）。
    数据来源: stock-chip-distribution/{code}.csv
      字段: 交易日期, 50分位成本, 95分位成本, 加权平均成本, 胜率
    overhead_ratio: (95分位成本 - 当前价) / 当前价，若当前价 >= 95分位成本 则为 0
    当前价: 从 stock-chip-distribution CSV 中「后复权价格」字段获取（当日收盘价近似）
    """
    from db.storage import get_zt_pool, get_volume_breakout, insert_chip_status

    # 汇集目标股票
    zt_rows = get_zt_pool(trade_date)
    vb_rows = get_volume_breakout(trade_date)
    target_codes = set()
    for r in zt_rows:
        target_codes.add(r["stock_code"])
    for r in vb_rows:
        target_codes.add(r["stock_code"])

    if not target_codes:
        logger.info("[daily_compute] chip_status %s: 无目标股票（涨停池和成交异动均为空）", trade_date)
        return

    chip_dir = DATA_ROOT / "stock-chip-distribution"
    inserted = 0

    for code in target_codes:
        csv_path = chip_dir / f"{code}.csv"
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path, encoding="GBK", skiprows=1)
            # 过滤目标日期行
            df["交易日期"] = df["交易日期"].astype(str)
            day_row = df[df["交易日期"] == trade_date]
            if day_row.empty:
                continue

            r = day_row.iloc[0]
            cost_50 = float(r["50分位成本"]) if "50分位成本" in r.index else None
            cost_95 = float(r["95分位成本"]) if "95分位成本" in r.index else None
            win_rate = float(r["胜率"]) if "胜率" in r.index else None
            # 后复权价格作为当前价格近似
            cur_price = float(r["后复权价格"]) if "后复权价格" in r.index else None

            if cost_95 is not None and cur_price is not None and cur_price > 0:
                if cur_price >= cost_95:
                    overhead_ratio = 0.0
                else:
                    overhead_ratio = (cost_95 - cur_price) / cur_price
            else:
                overhead_ratio = None

            insert_chip_status(
                trade_date, code,
                cost_50=cost_50,
                win_rate=win_rate,
                overhead_ratio=overhead_ratio,
            )
            inserted += 1
        except Exception as e:
            logger.warning("[daily_compute] chip_status %s 读取失败: %s", code, e)

    logger.info("[daily_compute] chip_status %s: %d 只股票写入", trade_date, inserted)


# ── 任务6：连板链条 ───────────────────────────────────────────────────────────

def compute_lianzban_chain(trade_date: str) -> None:
    """
    计算连板链条并写入 lianzban_chain 表。

    筛选条件: 涨停相关因子.parquet 中 连板次数 >= 2
    联表: 申万行业.parquet (行业)
    股票名称: 从 stock-trading-data-pro CSV 中读取（逐个读，数量少）
    """
    from db.storage import insert_lianzban_chain

    df = get_factor_slice("涨停相关因子", trade_date)
    if df.empty:
        logger.warning("[daily_compute] lianzban_chain: %s 无数据", trade_date)
        return

    # 筛选连板股 (连板次数 >= 2)
    lb_df = df[df["连板次数"] >= 2].copy()
    if lb_df.empty:
        logger.info("[daily_compute] lianzban_chain %s: 无连板股", trade_date)
        return

    industry_map = _load_industry_map(trade_date)
    trading_dir = DATA_ROOT / "stock-trading-data-pro"
    inserted = 0

    for _, row in lb_df.iterrows():
        code = str(row["code"])
        lianzban_cnt = int(row["连板次数"])
        is_zb = bool(row["是否炸板"] == 1) if "是否炸板" in row.index else False
        industry = industry_map.get(code, "未知")

        # 获取股票名称
        stock_name = ""
        csv_path = trading_dir / f"{code}.csv"
        if csv_path.exists():
            try:
                name_row = pd.read_csv(
                    csv_path, encoding="GBK", skiprows=1,
                    usecols=["股票名称"], nrows=1,
                )
                if not name_row.empty:
                    stock_name = str(name_row.iloc[0]["股票名称"]).strip()
            except Exception:
                pass

        insert_lianzban_chain(trade_date, code, stock_name, industry, lianzban_cnt, is_zb)
        inserted += 1

    logger.info("[daily_compute] lianzban_chain %s: %d 只连板股写入", trade_date, inserted)


# ── 任务7：机构调研热度 ───────────────────────────────────────────────────────

def compute_research_activity(trade_date: str) -> None:
    """
    统计机构调研热度并写入 research_activity 表。

    数据来源: stock-activation-records/{code}.csv
      字段: 股票代码, 股票名称, 公告日期, 参会机构名称
    统计窗口: trade_date 前5个交易日（含 trade_date）内被调研的机构数
    筛选: org_count_5d >= 3

    注意:
      - 公告日期格式可能为 "YYYY/MM/DD" 或 "YYYY-MM-DD"，需要标准化处理
      - 仅统计有参会机构名称记录的行（排除 NaN）
      - 「机构数」用参会机构名称的去重计数近似（同一机构多次算一次）
    """
    from db.storage import insert_research_activity

    act_dir = DATA_ROOT / "stock-activation-records"
    if not act_dir.exists():
        logger.warning("[daily_compute] research_activity: 调研记录目录不存在")
        return

    # 生成窗口日期范围（前5个交易日：简单取日历5天，非严格交易日）
    try:
        end_dt = pd.Timestamp(trade_date)
    except Exception as e:
        logger.error("[daily_compute] research_activity: 日期解析失败 %s: %s", trade_date, e)
        return

    window_start = (end_dt - pd.Timedelta(days=7)).strftime("%Y-%m-%d")  # 稍宽松，覆盖5个交易日

    inserted = 0
    for csv_file in act_dir.glob("*.csv"):
        try:
            df = pd.read_csv(csv_file, encoding="GBK", skiprows=1,
                             usecols=["股票代码", "股票名称", "公告日期", "参会机构名称"])
            if df.empty:
                continue

            # 标准化公告日期格式
            df["公告日期"] = pd.to_datetime(df["公告日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["公告日期"])

            # 筛选窗口内记录
            mask = (df["公告日期"] >= window_start) & (df["公告日期"] <= trade_date)
            window_df = df[mask].copy()
            if window_df.empty:
                continue

            # 去重统计机构数（排除空机构名）
            orgs = window_df["参会机构名称"].dropna()
            org_count = orgs.nunique()

            if org_count < 3:
                continue

            # 股票代码和名称
            code = str(window_df["股票代码"].iloc[0]).strip()
            stock_name = str(window_df["股票名称"].iloc[0]).strip() if "股票名称" in window_df.columns else ""
            last_visit = window_df["公告日期"].max()

            insert_research_activity(trade_date, code, stock_name, org_count, last_visit)
            inserted += 1

        except Exception as e:
            logger.warning("[daily_compute] research_activity 读取 %s 失败: %s", csv_file.name, e)

    logger.info("[daily_compute] research_activity %s: %d 只股票写入", trade_date, inserted)


# ── 统一入口 ──────────────────────────────────────────────────────────────────

def run_daily_compute(trade_date: str = None) -> None:
    """
    执行所有每日批量计算任务。

    trade_date: 指定交易日期 (YYYY-MM-DD)；为 None 时自动获取最新交易日。
    每个子任务独立 try/except，单个失败不影响其他任务。
    """
    if trade_date is None:
        from quant.loader import get_latest_trade_date
        trade_date = get_latest_trade_date()

    if not trade_date:
        logger.warning("[daily_compute] 无法获取最新交易日期，跳过")
        return

    logger.info("[daily_compute] 开始计算 %s", trade_date)

    tasks = [
        ("market_emotion", compute_market_emotion),
        ("sector_zt_density", compute_sector_zt_density),
        ("sector_flow_acceleration", compute_sector_flow_acceleration),
        ("volume_breakout", compute_volume_breakout),
        ("chip_status", compute_chip_status),
        ("lianzban_chain", compute_lianzban_chain),
        ("research_activity", compute_research_activity),
    ]

    for name, fn in tasks:
        try:
            fn(trade_date)
            logger.info("[daily_compute] %s 完成", name)
        except Exception as e:
            logger.error("[daily_compute] %s 失败: %s", name, e)

    logger.info("[daily_compute] 全部任务执行完毕，日期=%s", trade_date)
