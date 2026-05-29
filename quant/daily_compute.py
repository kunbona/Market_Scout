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


# ── 辅助：前一交易日 ─────────────────────────────────────────────────────────────

def _prev_trade_date(trade_date: str) -> str:
    """获取 trade_date 的前一个交易日（从涨停相关因子.parquet 的日期列表推断）"""
    try:
        df = pd.read_parquet(
            DATA_ROOT / "factors" / "stock" / "daily" / "涨停相关因子.parquet",
            columns=["trade_date"],
        )
        dates = sorted(df["trade_date"].astype(str).unique())
        idx = dates.index(trade_date) if trade_date in dates else -1
        return dates[idx - 1] if idx > 0 else ""
    except Exception as e:
        logger.warning("[daily_compute] _prev_trade_date 失败: %s", e)
        return ""


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
                df_tmp = pd.read_csv(
                    csv_file, encoding="GBK", skiprows=1,
                    usecols=["股票代码", "股票名称"],
                )
                if not df_tmp.empty:
                    # 取最后一行（最新数据），名称最准确
                    last = df_tmp.iloc[-1]
                    code = str(last["股票代码"]).strip()
                    name = str(last["股票名称"]).strip()
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
    # 修复2：过滤异常连板次数（历史脏数据可能出现 1597 等不可能值）
    max_lianzban = int(df["连板次数"][df["连板次数"] <= 30].max()) if "连板次数" in df.columns else 0

    # 修复3：昨日涨停今日溢价
    zt_yesterday_premium = None
    try:
        prev_date = _prev_trade_date(trade_date)
        if prev_date:
            df_prev = get_factor_slice("涨停相关因子", prev_date)
            yesterday_zt_codes = set(df_prev[df_prev["收盘涨停"] == 1]["code"].tolist())
            if yesterday_zt_codes:
                df_ret = get_factor_slice("涨跌幅相关因子", trade_date)
                if not df_ret.empty and "pct_chg_1" in df_ret.columns:
                    mask = df_ret["code"].isin(yesterday_zt_codes)
                    prem_vals = df_ret.loc[mask, "pct_chg_1"].dropna()
                    if len(prem_vals) > 0:
                        zt_yesterday_premium = float(prem_vals.mean())
    except Exception as e:
        logger.warning("[daily_compute] zt_yesterday_premium 计算失败: %s", e)

    # 修复1：跌停数从涨跌幅相关因子读取（pct_chg_1 <= -0.099 近似跌停）
    dt_total = 0
    try:
        df_ret = get_factor_slice("涨跌幅相关因子", trade_date)
        dt_total = int((df_ret["pct_chg_1"] <= -0.099).sum()) if "pct_chg_1" in df_ret.columns else 0
    except Exception as e:
        logger.warning("[daily_compute] dt_total 计算失败: %s", e)

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
        # 修复2：过滤异常连板次数再取 max
        max_lb = 0
        if not max_lb_per_ind.empty and industry in max_lb_per_ind.index:
            raw_max = max_lb_per_ind[industry]
            max_lb = int(raw_max) if raw_max <= 30 else 0
        density = zt_cnt / total if total > 0 else 0.0

        insert_sector_zt_density(trade_date, industry, zt_cnt, density, max_lb)

    logger.info("[daily_compute] sector_zt_density %s: %d 个行业写入", trade_date, len(total_per_ind))


# ── 任务3：板块资金流加速度 ──────────────────────────────────────────────────

def compute_sector_flow_acceleration(trade_date: str) -> None:
    """
    按申万行业聚合机构净买入占比，计算 3日均值 / 20日均值 作为加速度指标。

    数据来源: 资金流相关因子.parquet 字段: 机构净买入占比
    联表: 申万行业.parquet
    写入: sector_flow_accel 表（insert_sector_flow_accel）
    """
    from db.storage import insert_sector_flow_accel

    try:
        # 加载全量资金流数据，筛选最近 25 个交易日（覆盖 20 日窗口）
        flow_path = DATA_ROOT / "factors" / "stock" / "daily" / "资金流相关因子.parquet"
        df_flow = pd.read_parquet(flow_path, columns=["trade_date", "code", "机构净买入占比"])
        df_flow["trade_date"] = df_flow["trade_date"].astype(str)

        # 取 trade_date 及之前 25 个交易日
        all_dates = sorted(df_flow["trade_date"].unique())
        if trade_date not in all_dates:
            logger.warning("[daily_compute] sector_flow_acceleration: %s 无资金流数据", trade_date)
            return
        td_idx = all_dates.index(trade_date)
        window_dates = all_dates[max(0, td_idx - 24): td_idx + 1]  # 最多 25 天
        df_flow = df_flow[df_flow["trade_date"].isin(window_dates)].copy()

        # 联表行业
        industry_map = _load_industry_map(trade_date)
        if not industry_map:
            logger.warning("[daily_compute] sector_flow_acceleration: 行业映射为空，跳过")
            return
        df_flow["industry"] = df_flow["code"].map(industry_map)
        df_flow = df_flow.dropna(subset=["industry"])

        # 按 (trade_date, industry) 聚合机构净买入占比均值
        daily_ind = (
            df_flow.groupby(["trade_date", "industry"])["机构净买入占比"]
            .mean()
            .reset_index()
            .rename(columns={"机构净买入占比": "inst_mean"})
        )

        # 取近3日和近20日窗口日期
        dates_3d = window_dates[-3:]
        dates_20d = window_dates[-20:]

        industries = daily_ind["industry"].unique()
        written = 0
        for ind in industries:
            ind_df = daily_ind[daily_ind["industry"] == ind]
            inst_3d = float(ind_df[ind_df["trade_date"].isin(dates_3d)]["inst_mean"].mean())
            inst_20d = float(ind_df[ind_df["trade_date"].isin(dates_20d)]["inst_mean"].mean())
            accel = inst_3d / inst_20d if inst_20d != 0 else 0.0
            insert_sector_flow_accel(trade_date, ind, inst_3d, inst_20d, accel)
            written += 1

        logger.info("[daily_compute] sector_flow_acceleration %s: %d 个行业写入", trade_date, written)

    except Exception as e:
        logger.error("[daily_compute] sector_flow_acceleration 失败: %s", e)


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
        # 取最后一行名称（最新），避免 N/C 前缀旧名
        stock_name = ""
        csv_path = trading_dir / f"{code}.csv"
        if csv_path.exists():
            try:
                name_df = pd.read_csv(
                    csv_path, encoding="GBK", skiprows=1,
                    usecols=["股票名称"],
                )
                if not name_df.empty:
                    stock_name = str(name_df.iloc[-1]["股票名称"]).strip()
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

        # 获取股票名称：读取最后一行（最新），避免取到 N/C 前缀的上市初始名
        stock_name = ""
        csv_path = trading_dir / f"{code}.csv"
        if csv_path.exists():
            try:
                name_df = pd.read_csv(
                    csv_path, encoding="GBK", skiprows=1,
                    usecols=["股票名称"],
                )
                if not name_df.empty:
                    stock_name = str(name_df.iloc[-1]["股票名称"]).strip()
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


# ── 新任务1：连板梯队分布 + 晋级率 ──────────────────────────────────────────────

def compute_lianzban_stats(trade_date: str) -> None:
    """
    计算连板梯队分布（各板数量）和晋级率（N→N+1），写入 lianzban_stats 表。

    数据来源: 涨停相关因子.parquet，需今日和昨日两天截面。
    晋级率: 昨日N板股票中，今日连板次数升至N+1的比例。
    """
    try:
        from db.storage import upsert_lianzban_stats

        df_today = get_factor_slice("涨停相关因子", trade_date)
        if df_today.empty:
            logger.warning("[daily_compute] lianzban_stats: %s 无数据", trade_date)
            return

        # 今日各梯队数量（连板次数异常值 > 30 排除）
        lb_today = df_today[df_today["连板次数"] <= 30]
        tier_1 = int((lb_today["连板次数"] == 1).sum())
        tier_2 = int((lb_today["连板次数"] == 2).sum())
        tier_3 = int((lb_today["连板次数"] == 3).sum())
        tier_4plus = int((lb_today["连板次数"] >= 4).sum())

        # 昨日截面用于计算晋级率
        advance_1to2 = advance_2to3 = advance_3to4 = 0.0
        prev_date = _prev_trade_date(trade_date)
        if prev_date:
            df_prev = get_factor_slice("涨停相关因子", prev_date)
            if not df_prev.empty:
                df_prev = df_prev[df_prev["连板次数"] <= 30]

                # 今日以 code 为索引方便查找
                today_lb_map = dict(zip(df_today["code"], df_today["连板次数"]))

                for n, attr in [(1, "advance_1to2"), (2, "advance_2to3"), (3, "advance_3to4")]:
                    prev_n_codes = set(df_prev[df_prev["连板次数"] == n]["code"].tolist())
                    if prev_n_codes:
                        advanced = sum(
                            1 for c in prev_n_codes
                            if today_lb_map.get(c, 0) == n + 1
                        )
                        val = advanced / len(prev_n_codes)
                    else:
                        val = 0.0
                    if attr == "advance_1to2":
                        advance_1to2 = val
                    elif attr == "advance_2to3":
                        advance_2to3 = val
                    else:
                        advance_3to4 = val

        upsert_lianzban_stats(
            trade_date, tier_1, tier_2, tier_3, tier_4plus,
            advance_1to2, advance_2to3, advance_3to4,
        )
        logger.info(
            "[daily_compute] lianzban_stats %s: 1板=%d 2板=%d 3板=%d 4板+=%d "
            "晋级率=%.2f/%.2f/%.2f",
            trade_date, tier_1, tier_2, tier_3, tier_4plus,
            advance_1to2, advance_2to3, advance_3to4,
        )
    except Exception as e:
        logger.error("[daily_compute] lianzban_stats 失败: %s", e)


# ── 新任务2：概念涨停密度 ─────────────────────────────────────────────────────

def compute_concept_zt_density(trade_date: str) -> None:
    """
    统计今日涨停股票所属概念的涨停数量，写入 concept_zt_density 表。

    数据来源:
      - 涨停相关因子.parquet: 今日涨停股（收盘涨停==1）
      - stock-popular-concept-detail/{code}.csv: 字段 所属概念（逗号分隔）
    只读涨停股的概念文件，不全量扫描。
    """
    try:
        from db.storage import insert_concept_zt_density

        df_zt = get_factor_slice("涨停相关因子", trade_date)
        if df_zt.empty:
            logger.warning("[daily_compute] concept_zt_density: %s 无数据", trade_date)
            return

        zt_codes = df_zt[df_zt["收盘涨停"] == 1]["code"].tolist()
        if not zt_codes:
            logger.info("[daily_compute] concept_zt_density %s: 无涨停股", trade_date)
            return

        concept_dir = DATA_ROOT / "stock-popular-concept-detail"
        if not concept_dir.exists():
            logger.warning("[daily_compute] concept_zt_density: 概念目录不存在")
            return

        # 统计每个概念下涨停股数量
        concept_zt_count: dict[str, int] = {}
        read_count = 0
        for code in zt_codes:
            csv_path = concept_dir / f"{code}.csv"
            if not csv_path.exists():
                continue
            try:
                df_c = pd.read_csv(csv_path, encoding="gbk", skiprows=1,
                                   usecols=["交易日期", "所属概念"])
                if df_c.empty:
                    continue
                # 优先取 trade_date 当日行，若无则取最新行
                df_c["交易日期"] = df_c["交易日期"].astype(str)
                day_rows = df_c[df_c["交易日期"] == trade_date]
                target_row = day_rows.iloc[-1] if not day_rows.empty else df_c.iloc[-1]
                concept_val = target_row["所属概念"]
                if pd.isna(concept_val):
                    continue
                concepts = [c.strip() for c in str(concept_val).replace(",", "、").split("、") if c.strip()]
                for c in concepts:
                    concept_zt_count[c] = concept_zt_count.get(c, 0) + 1
                read_count += 1
            except Exception:
                pass

        written = 0
        for concept, cnt in concept_zt_count.items():
            insert_concept_zt_density(trade_date, concept, cnt)
            written += 1

        logger.info(
            "[daily_compute] concept_zt_density %s: 读取%d只涨停股，写入%d个概念",
            trade_date, read_count, written,
        )
    except Exception as e:
        logger.error("[daily_compute] concept_zt_density 失败: %s", e)


# ── 新任务3：集合竞价委比 ─────────────────────────────────────────────────────

def compute_call_auction_stats(trade_date: str) -> None:
    """
    计算今日涨停池股票的集合竞价委比，写入 call_auction_stats 表。

    数据来源: stock-call-auction-data/{code}.csv
      字段: 交易日期, 买1量~买5量, 卖1量~卖5量, 集合竞价成交额
    委比 = (买1~5量之和 - 卖1~5量之和) / (买1~5量之和 + 卖1~5量之和)
    只处理今日涨停池股票（不全量计算）。
    """
    try:
        from db.storage import get_zt_pool, insert_call_auction_stats

        zt_rows = get_zt_pool(trade_date)
        if not zt_rows:
            logger.info("[daily_compute] call_auction_stats %s: 涨停池为空", trade_date)
            return

        auction_dir = DATA_ROOT / "stock-call-auction-data"
        if not auction_dir.exists():
            logger.warning("[daily_compute] call_auction_stats: 竞价数据目录不存在")
            return

        # 字段名（已通过探索确认）
        buy_cols = ["买1量", "买2量", "买3量", "买4量", "买5量"]
        sell_cols = ["卖1量", "卖2量", "卖3量", "卖4量", "卖5量"]

        inserted = 0
        for row in zt_rows:
            code = row["stock_code"]
            stock_name = row.get("stock_name", "")
            csv_path = auction_dir / f"{code}.csv"
            if not csv_path.exists():
                continue
            try:
                df = pd.read_csv(csv_path, encoding="gbk", skiprows=1,
                                 usecols=["交易日期"] + buy_cols + sell_cols + ["集合竞价成交额"])
                df["交易日期"] = df["交易日期"].astype(str)
                day_df = df[df["交易日期"] == trade_date]
                if day_df.empty:
                    continue

                r = day_df.iloc[-1]  # 取当日最后一条（竞价快照）
                buy_vol = sum(float(r[c]) for c in buy_cols if c in r.index and pd.notna(r[c]))
                sell_vol = sum(float(r[c]) for c in sell_cols if c in r.index and pd.notna(r[c]))
                total_vol = buy_vol + sell_vol
                auction_ratio = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0
                auction_amount = float(r["集合竞价成交额"]) if "集合竞价成交额" in r.index and pd.notna(r["集合竞价成交额"]) else 0.0

                insert_call_auction_stats(trade_date, code, stock_name, auction_ratio, auction_amount)
                inserted += 1
            except Exception as e:
                logger.warning("[daily_compute] call_auction_stats %s 读取失败: %s", code, e)

        logger.info("[daily_compute] call_auction_stats %s: %d 只股票写入", trade_date, inserted)
    except Exception as e:
        logger.error("[daily_compute] call_auction_stats 失败: %s", e)


# ── 补充任务1：涨停池换手率分层 ──────────────────────────────────────────────

def compute_turnover_stats(trade_date: str) -> None:
    """
    计算当日涨停股的换手率分层，写入 turnover_stats 表。

    数据来源: 股票预处理数据.parquet 中 amount（元）和 circ_mv（元）。
    换手率 = amount / circ_mv * 100（%）
    分层规则（换手率 %）:
      低换手: < 5%   → 主力锁仓 / 一字板
      中换手: 5-20%  → 正常分歧换手
      高换手: ≥ 20%  → 充分换手，游资接力偏好
    """
    try:
        from db.storage import upsert_turnover_stats

        df_zt = get_factor_slice("涨停相关因子", trade_date)
        if df_zt.empty:
            logger.warning("[daily_compute] turnover_stats: %s 无数据", trade_date)
            return

        zt_codes = set(df_zt[df_zt["收盘涨停"] == 1]["code"].tolist())
        if not zt_codes:
            logger.info("[daily_compute] turnover_stats %s: 无涨停股", trade_date)
            return

        preproc_path = DATA_ROOT / "stg_cache" / "预处理数据" / "股票预处理数据.parquet"
        if not preproc_path.exists():
            logger.warning("[daily_compute] turnover_stats: 预处理数据不存在")
            return

        df = pd.read_parquet(preproc_path, columns=["trade_date", "code", "amount", "circ_mv"])
        df["trade_date"] = df["trade_date"].astype(str)
        df_day = df[df["trade_date"] == trade_date]
        if df_day.empty:
            logger.info("[daily_compute] turnover_stats %s: 预处理数据无当日记录", trade_date)
            return

        df_zt_day = df_day[df_day["code"].isin(zt_codes)].copy()
        df_zt_day = df_zt_day.dropna(subset=["amount", "circ_mv"])
        df_zt_day = df_zt_day[df_zt_day["circ_mv"] > 0]
        # amount 和 circ_mv 均为元，换手率 = amount/circ_mv * 100
        turnover_vals = (df_zt_day["amount"] / df_zt_day["circ_mv"] * 100).tolist()

        if not turnover_vals:
            logger.info("[daily_compute] turnover_stats %s: 未读到换手率数据", trade_date)
            return

        import numpy as np
        arr = np.array(turnover_vals)
        low_count = int((arr < 5).sum())
        mid_count = int(((arr >= 5) & (arr < 20)).sum())
        high_count = int((arr >= 20).sum())
        median_to = float(np.median(arr))
        avg_to = float(arr.mean())

        upsert_turnover_stats(trade_date, low_count, mid_count, high_count, median_to, avg_to)
        logger.info(
            "[daily_compute] turnover_stats %s: 低=%d 中=%d 高=%d 中位=%.1f%% 均值=%.1f%%",
            trade_date, low_count, mid_count, high_count, median_to, avg_to,
        )
    except Exception as e:
        logger.error("[daily_compute] turnover_stats 失败: %s", e)


# ── 补充任务2：涨停股流通市值分布 ─────────────────────────────────────────────

def compute_market_cap_dist(trade_date: str) -> None:
    """
    计算当日涨停股的流通市值分布，写入 market_cap_dist 表。

    数据来源: 涨停相关因子.parquet（涨停股列表）+ 股票预处理数据.parquet（circ_mv）
    分桶规则（亿元）:
      小盘: < 50 亿   → 空间龙/妖股
      中盘: 50-300 亿  → 游资主战场
      大盘: ≥ 300 亿  → 机构/指数权重
    """
    try:
        from db.storage import upsert_market_cap_dist

        df_zt = get_factor_slice("涨停相关因子", trade_date)
        if df_zt.empty:
            logger.warning("[daily_compute] market_cap_dist: %s 无数据", trade_date)
            return

        zt_codes = set(df_zt[df_zt["收盘涨停"] == 1]["code"].tolist())
        if not zt_codes:
            logger.info("[daily_compute] market_cap_dist %s: 无涨停股", trade_date)
            return

        # 从预处理数据读 circ_mv（单位：万元，需换算成亿元）
        preproc_path = DATA_ROOT / "stg_cache" / "预处理数据" / "股票预处理数据.parquet"
        if not preproc_path.exists():
            logger.warning("[daily_compute] market_cap_dist: 预处理数据不存在")
            return

        df_mv = pd.read_parquet(
            preproc_path,
            columns=["trade_date", "code", "circ_mv"],
        )
        df_mv["trade_date"] = df_mv["trade_date"].astype(str)
        df_day = df_mv[df_mv["trade_date"] == trade_date]

        # 若当日无数据，取最近可用日期
        if df_day.empty:
            latest = df_mv["trade_date"].max()
            df_day = df_mv[df_mv["trade_date"] == latest]

        df_zt_mv = df_day[df_day["code"].isin(zt_codes)].copy()
        df_zt_mv = df_zt_mv.dropna(subset=["circ_mv"])
        # circ_mv 单位为元，除以 1e8 转亿元
        df_zt_mv["circ_mv_yi"] = df_zt_mv["circ_mv"] / 1e8

        total = len(df_zt_mv)
        if total == 0:
            logger.info("[daily_compute] market_cap_dist %s: 未匹配到市值数据", trade_date)
            return

        small = int((df_zt_mv["circ_mv_yi"] < 50).sum())
        mid = int(((df_zt_mv["circ_mv_yi"] >= 50) & (df_zt_mv["circ_mv_yi"] < 300)).sum())
        large = int((df_zt_mv["circ_mv_yi"] >= 300).sum())
        small_pct = small / total
        mid_pct = mid / total
        large_pct = large / total

        upsert_market_cap_dist(trade_date, small, mid, large, small_pct, mid_pct, large_pct)
        logger.info(
            "[daily_compute] market_cap_dist %s: 小盘=%d(%.0f%%) 中盘=%d(%.0f%%) 大盘=%d(%.0f%%)",
            trade_date, small, small_pct * 100, mid, mid_pct * 100, large, large_pct * 100,
        )
    except Exception as e:
        logger.error("[daily_compute] market_cap_dist 失败: %s", e)


# ── 补充任务3：市场宽度（涨跌家数 + 成交额比值）──────────────────────────────

def compute_advance_decline(trade_date: str) -> None:
    """
    计算全市场上涨/下跌/平家数及成交额 vs 20日均值，写入 advance_decline 表。

    数据来源:
      - 涨跌幅相关因子.parquet: pct_chg_1（当日涨跌幅）
      - 成交额相关因子.parquet: amount_mean_20（20日均成交额）+ amount_mean_5（近5日）
    上涨: pct_chg_1 > 0.5%
    下跌: pct_chg_1 < -0.5%
    平盘: |pct_chg_1| <= 0.5%
    total_amount: 当日全市场成交额总和（亿元）
    amount_ma20: 20日均全市场成交额（亿元）
    amount_ratio: total_amount / amount_ma20
    """
    try:
        from db.storage import upsert_advance_decline

        df_ret = get_factor_slice("涨跌幅相关因子", trade_date)
        if df_ret.empty:
            logger.warning("[daily_compute] advance_decline: %s 无数据", trade_date)
            return

        pct = df_ret["pct_chg_1"].dropna()
        advance_count = int((pct > 0.005).sum())
        decline_count = int((pct < -0.005).sum())
        flat_count = int((pct.abs() <= 0.005).sum())
        ad_ratio = advance_count / decline_count if decline_count > 0 else float(advance_count)

        # 成交额（万元 → 亿元）
        df_amt = get_factor_slice("成交额相关因子", trade_date)
        total_amount = 0.0
        amount_ma20 = 0.0
        amount_ratio = 0.0

        if not df_amt.empty:
            # amount_mean_5 近似当日（取各股 amount_mean_5 之和 × 5 并不准确）
            # 更好方式：从预处理数据取当日 amount 列求和
            preproc_path = DATA_ROOT / "stg_cache" / "预处理数据" / "股票预处理数据.parquet"
            if preproc_path.exists():
                df_pre = pd.read_parquet(preproc_path, columns=["trade_date", "code", "amount"])
                df_pre["trade_date"] = df_pre["trade_date"].astype(str)

                # 当日成交额总和（元→亿元）
                df_today_amt = df_pre[df_pre["trade_date"] == trade_date]
                if not df_today_amt.empty:
                    total_amount = float(df_today_amt["amount"].sum()) / 1e8

                # 20日均：取最近 20 个交易日的均值
                all_dates = sorted(df_pre["trade_date"].unique())
                if trade_date in all_dates:
                    td_idx = all_dates.index(trade_date)
                    window_dates = all_dates[max(0, td_idx - 19): td_idx + 1]
                    df_window = df_pre[df_pre["trade_date"].isin(window_dates)]
                    daily_total = df_window.groupby("trade_date")["amount"].sum()
                    amount_ma20 = float(daily_total.mean()) / 1e8

                if amount_ma20 > 0:
                    amount_ratio = total_amount / amount_ma20

        upsert_advance_decline(
            trade_date, advance_count, decline_count, flat_count, ad_ratio,
            total_amount, amount_ma20, amount_ratio,
        )
        logger.info(
            "[daily_compute] advance_decline %s: 涨=%d 跌=%d 平=%d A/D=%.2f "
            "成交额=%.0f亿(MA20=%.0f亿,比值=%.2f)",
            trade_date, advance_count, decline_count, flat_count, ad_ratio,
            total_amount, amount_ma20, amount_ratio,
        )
    except Exception as e:
        logger.error("[daily_compute] advance_decline 失败: %s", e)


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
        ("lianzban_stats", compute_lianzban_stats),
        ("concept_zt_density", compute_concept_zt_density),
        ("call_auction_stats", compute_call_auction_stats),
        ("turnover_stats", compute_turnover_stats),
        ("market_cap_dist", compute_market_cap_dist),
        ("advance_decline", compute_advance_decline),
    ]

    for name, fn in tasks:
        try:
            fn(trade_date)
            logger.info("[daily_compute] %s 完成", name)
        except Exception as e:
            logger.error("[daily_compute] %s 失败: %s", name, e)

    logger.info("[daily_compute] 全部任务执行完毕，日期=%s", trade_date)
