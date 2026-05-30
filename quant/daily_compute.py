"""
quant/daily_compute.py — 每日批量计算任务

统一入口: run_daily_compute(trade_date: str = None)
trade_date 为 None 时，调用 loader.get_latest_trade_date() 自动获取。

每个子任务用 try/except 包裹，单个任务失败不影响其他任务。
所有写入操作通过 db/storage.py 中的函数完成。

数据来源: 全部改为 load_daily_snapshot / load_daily_range，
不再依赖 factors/stock/daily/ 下的 parquet 文件。
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from quant.loader import DATA_ROOT, load_daily_snapshot, load_daily_range, get_latest_trade_date

logger = logging.getLogger(__name__)


# ── 任务1：市场情绪日度指标 ───────────────────────────────────────────────────

def compute_market_emotion(trade_date: str) -> None:
    """
    计算市场情绪日度指标并写入 market_emotion 表。

    字段来源: load_daily_snapshot
      - is_zt, is_zb, is_dt, lianzban_cnt
    """
    from db.storage import upsert_market_emotion

    df = load_daily_snapshot(trade_date)
    if df.empty:
        logger.warning("[daily_compute] market_emotion: %s 无数据", trade_date)
        return

    zt_total = int(df["is_zt"].sum())
    zb_total = int(df["is_zb"].sum())
    dt_total = int(df["is_dt"].sum())
    max_lianzban = int(df["lianzban_cnt"].max())
    # 非一字涨停：涨停且开盘价 < 涨停价（开盘即一字板的不算）
    real_zt = int(((df["is_zt"] == 1) & (df["open"] < df["up_limit"])).sum())

    # 昨日涨停今日溢价
    zt_yesterday_premium = None
    try:
        _dr = load_daily_range(trade_date, days=5)
        _dates = sorted(_dr["trade_date"].unique()) if not _dr.empty else []
        _idx = _dates.index(trade_date) if trade_date in _dates else -1
        prev_date = _dates[_idx - 1] if _idx > 0 else ""
        if prev_date:
            # 取含前日的两天数据
            df_range = load_daily_range(trade_date, days=3)
            if not df_range.empty:
                df_prev = df_range[df_range["trade_date"] == prev_date]
                yesterday_zt_codes = set(df_prev[df_prev["is_zt"] == 1]["code"].tolist())
                if yesterday_zt_codes:
                    df_today = df_range[df_range["trade_date"] == trade_date]
                    mask = df_today["code"].isin(yesterday_zt_codes)
                    prem_vals = df_today.loc[mask, "pct_chg"].dropna()
                    if len(prem_vals) > 0:
                        # 用中位数而非均值，避免科创板+20%等极端值干扰
                        zt_yesterday_premium = float(prem_vals.median())
    except Exception as e:
        logger.warning("[daily_compute] zt_yesterday_premium 计算失败: %s", e)

    zb_rate = zb_total / (zt_total + zb_total) if (zt_total + zb_total) > 0 else 0.0

    upsert_market_emotion(
        trade_date, zt_total, dt_total, zb_total,
        max_lianzban, zt_yesterday_premium, zb_rate,
        real_zt=real_zt,
    )
    logger.info(
        "[daily_compute] market_emotion %s: zt=%d zb=%d dt=%d max_lb=%d zb_rate=%.3f real_zt=%d",
        trade_date, zt_total, zb_total, dt_total, max_lianzban, zb_rate, real_zt,
    )


# ── 任务2：板块涨停密度 ───────────────────────────────────────────────────────

def compute_sector_zt_density(trade_date: str) -> None:
    """
    按申万一级行业计算涨停密度并写入 sector_zt_density 表。

    字段来源: load_daily_snapshot
      - is_zt, lianzban_cnt, industry_l1
    zt_density = 涨停股数 / 该行业总股数
    """
    from db.storage import insert_sector_zt_density

    df = load_daily_snapshot(trade_date)
    if df.empty:
        logger.warning("[daily_compute] sector_zt_density: %s 无数据", trade_date)
        return

    if "industry_l1" not in df.columns:
        logger.warning("[daily_compute] sector_zt_density: 缺少 industry_l1 列，跳过")
        return

    df = df.copy()
    df["industry_l1"] = df["industry_l1"].fillna("未知")

    grouped = df.groupby("industry_l1")
    total_per_ind = grouped["code"].count()
    zt_per_ind = grouped["is_zt"].sum()
    max_lb_per_ind = grouped["lianzban_cnt"].max()

    for industry in total_per_ind.index:
        total = int(total_per_ind[industry])
        zt_cnt = int(zt_per_ind[industry])
        max_lb = 0
        if industry in max_lb_per_ind.index:
            raw_max = max_lb_per_ind[industry]
            max_lb = int(raw_max) if (not pd.isna(raw_max) and raw_max <= 30) else 0
        density = zt_cnt / total if total > 0 else 0.0

        insert_sector_zt_density(trade_date, industry, zt_cnt, density, max_lb)

    logger.info("[daily_compute] sector_zt_density %s: %d 个行业写入", trade_date, len(total_per_ind))


# ── 任务3：板块资金流加速度 ──────────────────────────────────────────────────

def compute_sector_flow_acceleration(trade_date: str) -> None:
    """
    按申万行业聚合机构净买入占比，计算 3日均值 / 20日均值 作为加速度指标。

    数据来源: load_daily_range(trade_date, days=25)
    写入: sector_flow_accel 表
    """
    from db.storage import insert_sector_flow_accel

    try:
        df_range = load_daily_range(trade_date, days=25)
        if df_range.empty:
            logger.warning("[daily_compute] sector_flow_acceleration: %s 无数据", trade_date)
            return

        if "industry_l1" not in df_range.columns:
            logger.warning("[daily_compute] sector_flow_acceleration: 缺少 industry_l1 列，跳过")
            return

        df_range = df_range.dropna(subset=["industry_l1"]).copy()
        df_range = df_range[df_range["industry_l1"] != ""]

        # 按 (trade_date, industry_l1) 聚合 inst_net_pct 均值
        daily_ind = (
            df_range.groupby(["trade_date", "industry_l1"])["inst_net_pct"]
            .mean()
            .reset_index()
            .rename(columns={"inst_net_pct": "inst_mean"})
        )

        # 窗口日期
        all_dates = sorted(df_range["trade_date"].unique())
        dates_3d = all_dates[-3:] if len(all_dates) >= 3 else all_dates
        dates_20d = all_dates[-20:] if len(all_dates) >= 20 else all_dates

        industries = daily_ind["industry_l1"].unique()
        written = 0
        for ind in industries:
            ind_df = daily_ind[daily_ind["industry_l1"] == ind]
            inst_3d = float(ind_df[ind_df["trade_date"].isin(dates_3d)]["inst_mean"].mean())
            inst_20d = float(ind_df[ind_df["trade_date"].isin(dates_20d)]["inst_mean"].mean())
            # 用绝对值做分母，避免负/负=正的方向错误
            # inst_20d 为负表示近20日整体净卖出，accel 有意义的前提是趋势方向一致
            if abs(inst_20d) > 1e-9:
                accel = inst_3d / abs(inst_20d)
            else:
                accel = 0.0
            insert_sector_flow_accel(trade_date, ind, inst_3d, inst_20d, accel)
            written += 1

        logger.info("[daily_compute] sector_flow_acceleration %s: %d 个行业写入", trade_date, written)

    except Exception as e:
        logger.error("[daily_compute] sector_flow_acceleration 失败: %s", e)


# ── 任务4：成交额异动 ─────────────────────────────────────────────────────────

def compute_volume_breakout(trade_date: str) -> None:
    """
    筛选成交额异动股票并写入 volume_breakout 表。

    字段来源: load_daily_snapshot
      - amount_mean_5, amount_mean_20, industry_l1, name
    筛选条件: amount_mean_5 / amount_mean_20 > 2.0
    """
    from db.storage import insert_volume_breakout

    df = load_daily_snapshot(trade_date)
    if df.empty:
        logger.warning("[daily_compute] volume_breakout: %s 无数据", trade_date)
        return

    valid = df.dropna(subset=["amount_mean_5", "amount_mean_20"])
    valid = valid[valid["amount_mean_20"] > 0].copy()
    valid["ratio_5_20"] = valid["amount_mean_5"] / valid["amount_mean_20"]
    breakout = valid[valid["ratio_5_20"] > 2.0].copy()

    if breakout.empty:
        logger.info("[daily_compute] volume_breakout %s: 无异动股票", trade_date)
        return

    inserted = 0
    for _, row in breakout.iterrows():
        code = str(row["code"])
        industry = str(row.get("industry_l1", "未知")) if pd.notna(row.get("industry_l1")) else "未知"
        stock_name = str(row.get("name", "")) if pd.notna(row.get("name")) else ""
        ratio = float(row["ratio_5_20"])
        amount_5d = float(row["amount_mean_5"])

        insert_volume_breakout(trade_date, code, stock_name, industry, ratio, amount_5d)
        inserted += 1

    logger.info("[daily_compute] volume_breakout %s: %d 只股票写入", trade_date, inserted)


# ── 任务5：筹码状态 ───────────────────────────────────────────────────────────

def compute_chip_status(trade_date: str) -> None:
    """
    计算目标股票筹码状态并写入 chip_status 表。

    目标股票范围: 当日涨停池 + volume_breakout 名单（取自 DB）。
    数据来源: stock-chip-distribution/{code}.csv
    """
    from db.storage import get_zt_pool, get_volume_breakout, insert_chip_status

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
            df["交易日期"] = df["交易日期"].astype(str)
            day_row = df[df["交易日期"] == trade_date]
            if day_row.empty:
                continue

            r = day_row.iloc[0]
            cost_50 = float(r["50分位成本"]) if "50分位成本" in r.index else None
            cost_95 = float(r["95分位成本"]) if "95分位成本" in r.index else None
            win_rate = float(r["胜率"]) if "胜率" in r.index else None
            cur_price = float(r["后复权价格"]) if "后复权价格" in r.index else None

            if cost_95 is not None and cur_price is not None and cur_price > 0:
                overhead_ratio = 0.0 if cur_price >= cost_95 else (cost_95 - cur_price) / cur_price
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

    字段来源: load_daily_snapshot
      - lianzban_cnt >= 2, is_zb, industry_l1, name
    """
    from db.storage import insert_lianzban_chain

    df = load_daily_snapshot(trade_date)
    if df.empty:
        logger.warning("[daily_compute] lianzban_chain: %s 无数据", trade_date)
        return

    lb_df = df[df["lianzban_cnt"] >= 2].copy()
    if lb_df.empty:
        logger.info("[daily_compute] lianzban_chain %s: 无连板股", trade_date)
        return

    inserted = 0
    for _, row in lb_df.iterrows():
        code = str(row["code"])
        lianzban_cnt = int(row["lianzban_cnt"])
        is_zb = bool(row.get("is_zb", 0) == 1)
        industry = str(row.get("industry_l1", "未知")) if pd.notna(row.get("industry_l1")) else "未知"
        stock_name = str(row.get("name", "")) if pd.notna(row.get("name")) else ""

        insert_lianzban_chain(trade_date, code, stock_name, industry, lianzban_cnt, is_zb)
        inserted += 1

    logger.info("[daily_compute] lianzban_chain %s: %d 只连板股写入", trade_date, inserted)


# ── 任务7：机构调研热度 ───────────────────────────────────────────────────────

def compute_research_activity(trade_date: str) -> None:
    """
    统计机构调研热度并写入 research_activity 表。

    数据来源: stock-activation-records/{code}.csv
    统计窗口: trade_date 前5个交易日（含 trade_date）内被调研的机构数
    筛选: org_count_5d >= 3
    """
    from db.storage import insert_research_activity

    act_dir = DATA_ROOT / "stock-activation-records"
    if not act_dir.exists():
        logger.warning("[daily_compute] research_activity: 调研记录目录不存在")
        return

    try:
        end_dt = pd.Timestamp(trade_date)
    except Exception as e:
        logger.error("[daily_compute] research_activity: 日期解析失败 %s: %s", trade_date, e)
        return

    window_start = (end_dt - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    inserted = 0
    for csv_file in act_dir.glob("*.csv"):
        try:
            df = pd.read_csv(csv_file, encoding="GBK", skiprows=1,
                             usecols=["股票代码", "股票名称", "公告日期", "参会机构名称"])
            if df.empty:
                continue

            df["公告日期"] = pd.to_datetime(df["公告日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["公告日期"])

            mask = (df["公告日期"] >= window_start) & (df["公告日期"] <= trade_date)
            window_df = df[mask].copy()
            if window_df.empty:
                continue

            orgs = window_df["参会机构名称"].dropna()
            org_count = orgs.nunique()

            if org_count < 3:
                continue

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

    数据来源: load_daily_snapshot (今日) + load_daily_range (前日)
    """
    try:
        from db.storage import upsert_lianzban_stats

        df_today = load_daily_snapshot(trade_date)
        if df_today.empty:
            logger.warning("[daily_compute] lianzban_stats: %s 无数据", trade_date)
            return

        lb_today = df_today[df_today["lianzban_cnt"] <= 30]
        tier_1 = int((lb_today["lianzban_cnt"] == 1).sum())
        tier_2 = int((lb_today["lianzban_cnt"] == 2).sum())
        tier_3 = int((lb_today["lianzban_cnt"] == 3).sum())
        tier_4plus = int((lb_today["lianzban_cnt"] >= 4).sum())

        advance_1to2 = advance_2to3 = advance_3to4 = 0.0
        _dr2 = load_daily_range(trade_date, days=5)
        _dates2 = sorted(_dr2["trade_date"].unique()) if not _dr2.empty else []
        _idx2 = _dates2.index(trade_date) if trade_date in _dates2 else -1
        prev_date = _dates2[_idx2 - 1] if _idx2 > 0 else ""
        if prev_date:
            # 用 load_daily_range 取两天，其中含 prev_date 的快照
            df_range = load_daily_range(trade_date, days=3)
            if not df_range.empty:
                df_prev = df_range[df_range["trade_date"] == prev_date]
                df_prev = df_prev[df_prev["lianzban_cnt"] <= 30]

                today_lb_map = dict(zip(df_today["code"], df_today["lianzban_cnt"]))

                for n, attr_idx in [(1, 0), (2, 1), (3, 2)]:
                    prev_n_codes = set(df_prev[df_prev["lianzban_cnt"] == n]["code"].tolist())
                    if prev_n_codes:
                        advanced = sum(
                            1 for c in prev_n_codes
                            if today_lb_map.get(c, 0) == n + 1
                        )
                        val = advanced / len(prev_n_codes)
                    else:
                        val = 0.0
                    if attr_idx == 0:
                        advance_1to2 = val
                    elif attr_idx == 1:
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
      - load_daily_snapshot: 今日涨停股（is_zt==1）
      - stock-popular-concept-detail/{code}.csv: 所属概念（逗号分隔）
    """
    try:
        from db.storage import insert_concept_zt_density

        df_today = load_daily_snapshot(trade_date)
        if df_today.empty:
            logger.warning("[daily_compute] concept_zt_density: %s 无数据", trade_date)
            return

        zt_codes = df_today[df_today["is_zt"] == 1]["code"].tolist()
        if not zt_codes:
            logger.info("[daily_compute] concept_zt_density %s: 无涨停股", trade_date)
            return

        concept_dir = DATA_ROOT / "stock-popular-concept-detail"
        if not concept_dir.exists():
            logger.warning("[daily_compute] concept_zt_density: 概念目录不存在")
            return

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

                r = day_df.iloc[-1]
                buy_vol = sum(float(r[c]) for c in buy_cols if c in r.index and pd.notna(r[c]))
                sell_vol = sum(float(r[c]) for c in sell_cols if c in r.index and pd.notna(r[c]))
                total_vol = buy_vol + sell_vol
                auction_ratio = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0
                auction_amount = (
                    float(r["集合竞价成交额"])
                    if "集合竞价成交额" in r.index and pd.notna(r["集合竞价成交额"])
                    else 0.0
                )

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

    数据来源: load_daily_snapshot（amount、circ_mv、is_zt）
    换手率 = amount / circ_mv * 100（%）
    """
    try:
        from db.storage import upsert_turnover_stats

        df = load_daily_snapshot(trade_date)
        if df.empty:
            logger.warning("[daily_compute] turnover_stats: %s 无数据", trade_date)
            return

        zt_df = df[df["is_zt"] == 1].copy()
        if zt_df.empty:
            logger.info("[daily_compute] turnover_stats %s: 无涨停股", trade_date)
            return

        zt_df = zt_df.dropna(subset=["amount", "circ_mv"])
        zt_df = zt_df[zt_df["circ_mv"] > 0]
        # amount 和 circ_mv 均为元，换手率 = amount/circ_mv * 100
        turnover_vals = (zt_df["amount"] / zt_df["circ_mv"] * 100).tolist()

        if not turnover_vals:
            logger.info("[daily_compute] turnover_stats %s: 未读到换手率数据", trade_date)
            return

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

    数据来源: load_daily_snapshot（is_zt、circ_mv）
    circ_mv 单位为元，/ 1e8 转亿元。
    """
    try:
        from db.storage import upsert_market_cap_dist

        df = load_daily_snapshot(trade_date)
        if df.empty:
            logger.warning("[daily_compute] market_cap_dist: %s 无数据", trade_date)
            return

        zt_df = df[df["is_zt"] == 1].copy()
        if zt_df.empty:
            logger.info("[daily_compute] market_cap_dist %s: 无涨停股", trade_date)
            return

        zt_df = zt_df.dropna(subset=["circ_mv"])
        zt_df["circ_mv_yi"] = zt_df["circ_mv"] / 1e8

        total = len(zt_df)
        if total == 0:
            logger.info("[daily_compute] market_cap_dist %s: 未匹配到市值数据", trade_date)
            return

        small = int((zt_df["circ_mv_yi"] < 50).sum())
        mid = int(((zt_df["circ_mv_yi"] >= 50) & (zt_df["circ_mv_yi"] < 300)).sum())
        large = int((zt_df["circ_mv_yi"] >= 300).sum())
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

    数据来源: load_daily_snapshot（pct_chg、amount）
             load_daily_range（历史 amount 求 MA20）
    上涨: pct_chg > 0.5%
    下跌: pct_chg < -0.5%
    平盘: |pct_chg| <= 0.5%
    """
    try:
        from db.storage import upsert_advance_decline

        df = load_daily_snapshot(trade_date)
        if df.empty:
            logger.warning("[daily_compute] advance_decline: %s 无数据", trade_date)
            return

        pct = df["pct_chg"].dropna()
        advance_count = int((pct > 0.005).sum())
        decline_count = int((pct < -0.005).sum())
        flat_count = int((pct.abs() <= 0.005).sum())
        ad_ratio = advance_count / decline_count if decline_count > 0 else float(advance_count)

        # 当日总成交额（元→亿元）
        total_amount = float(df["amount"].sum()) / 1e8

        # 20日均：从 load_daily_range 获取历史成交额
        # 取前20个交易日（不含当日）作为 MA20 基准，避免今日既是分子又混入分母
        amount_ma20 = 0.0
        amount_ratio = 0.0
        try:
            df_range = load_daily_range(trade_date, days=21)
            if not df_range.empty:
                daily_total = df_range.groupby("trade_date")["amount"].sum()
                # 排除当日，取前20个交易日的均值作为基准
                prev_totals = daily_total[daily_total.index < trade_date]
                amount_ma20 = float(prev_totals.iloc[-20:].mean()) / 1e8 if not prev_totals.empty else float(daily_total.mean()) / 1e8
                if amount_ma20 > 0:
                    amount_ratio = total_amount / amount_ma20
        except Exception as e:
            logger.warning("[daily_compute] advance_decline MA20 计算失败: %s", e)

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
        ("lianzban_chain", compute_lianzban_chain),
        ("lianzban_stats", compute_lianzban_stats),
        ("concept_zt_density", compute_concept_zt_density),
        ("research_activity", compute_research_activity),
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
