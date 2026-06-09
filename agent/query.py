"""
市场数据查询脚本。供 Claude CLI 通过 Bash 工具调用，结果 JSON 输出到 stdout。

用法：
  python agent/query.py <command> [options]

Commands:
  data_health             【必须最先调用】数据健康检查：实时 vs 静态数据一致性，冲突检测，降级提示
  market_emotion          今日市场情绪（涨停/跌停/炸板率/连板/溢价）
  zt_pool                 今日涨停池
  sector_zt_density       行业涨停密度 Top10
  sector_flow_accel       机构资金加速度 Top10
  lhb                     今日龙虎榜
  lianzban_chain          连板链条（2板+）
  volume_breakout         成交额异动（5d/20d > 2x）
  news                    最近N小时财经新闻  --hours N（默认2）
  policy_news             近3日政策新闻
  f10                     涨停股F10基本面   --codes 300XXX,600XXX
  name                    查股票名称（从本地量价CSV）--codes 002491,688146,300197
  sector_flow             行业资金流最新快照
  context                 完整上下文（所有数据，供完整报告使用）
  market_pulse            最近1小时实时涨跌趋势（30秒一条，附炸板率和改善/恶化方向）
  northbound              北向资金最新净买入（沪深港通各渠道）
  industry_ranking        行业板块实时涨跌排行 Top20
  concept_flow            概念资金流 Top20
  advance_decline         全市涨跌家数 + 成交额（优先日线，缺则用实时快照）
  strong_pool             今日强势股池
  big_deal                大单异动最新50条
  market_breadth          实时全市涨跌家数+成交额（xtquant，1分钟粒度）
"""
import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# 确保从项目根目录能找到 db 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _out(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def _get_trade_calendar():
    """返回 AKShare 交易日历 DataFrame，列名 trade_date（datetime.date 类型）。"""
    import akshare as ak
    return ak.tool_trade_date_hist_sina()


def is_trade_date(d=None) -> bool:
    """查询指定日期（默认今天）是否为 A 股交易日。"""
    from datetime import date
    if d is None:
        d = date.today()
    return d in _get_trade_calendar()["trade_date"].values


def get_prev_trade_dates(d=None, n: int = 2) -> list:
    """
    返回 d（默认今天）之前最近 n 个交易日的 datetime.date 列表，从近到远排列。
    例：今天周五，n=2 → [周四, 周三]（跨越周末/节假日正确处理）
    """
    from datetime import date
    if d is None:
        d = date.today()
    cal = _get_trade_calendar()
    past = cal[cal["trade_date"] < d].sort_values("trade_date", ascending=False)
    return list(past["trade_date"].head(n).values)


def get_market_session(now=None) -> dict:
    """
    返回当前市场时段信息，供任何模块使用。

    返回字段：
      is_trade_day    — 今天是否交易日（AKShare 日历）
      is_trading_time — 现在是否在连续竞价/集合竞价时间内
      session         — 细粒度时段标签（见下表）
      date            — 今天日期 YYYY-MM-DD
      time            — 当前时间 HH:MM:SS

    session 值：
      pre_open      交易日 ~09:15，集合竞价未开始
      call_auction  09:15~09:30，集合竞价申报撮合
      morning       09:30~11:30，上午连续竞价
      lunch_break   11:30~13:00，午休
      afternoon     13:00~15:00，下午连续竞价
      post_close    15:00~17:00，收盘后（龙虎榜未出）
      evening       17:00~，盘后（龙虎榜/静态数据应就绪）
      weekend       周末
      holiday       节假日（AKShare 日历确认非交易日）
    """
    from datetime import datetime as _dt
    if now is None:
        now = _dt.now()

    is_weekend = now.weekday() >= 5
    try:
        cal = _get_trade_calendar()
        is_trade_day = now.date() in cal["trade_date"].values
    except Exception:
        cal = None
        is_trade_day = not is_weekend

    total_min = now.hour * 60 + now.minute

    if is_trade_day:
        if total_min < 9 * 60 + 15:
            session = "pre_open"
        elif total_min < 9 * 60 + 30:
            session = "call_auction"
        elif total_min < 11 * 60 + 30:
            session = "morning"
        elif total_min < 13 * 60:
            session = "lunch_break"
        elif total_min < 15 * 60:
            session = "afternoon"
        elif total_min < 17 * 60:
            session = "post_close"
        else:
            session = "evening"
    elif is_weekend:
        session = "weekend"
    else:
        session = "holiday"

    is_trading_time = session in ("call_auction", "morning", "afternoon")

    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "is_trade_day": is_trade_day,
        "is_trading_time": is_trading_time,
        "session": session,
    }


def cmd_data_health(args):
    """
    数据健康检查（Pre-flight hook）。
    检查实时数据与静态数据的新鲜度，检测两者冲突，输出降级建议。
    必须在所有其他查询之前调用。
    """
    import sqlite3
    from db.storage import DB_PATH

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_h = now.hour

    status = {}
    conflicts = []
    fallback_hints = {}

    with sqlite3.connect(DB_PATH) as conn:
        def _latest_date(table, date_col="trade_date"):
            try:
                row = conn.execute(
                    f"SELECT {date_col}, COUNT(*) FROM {table} WHERE {date_col} = "
                    f"(SELECT MAX({date_col}) FROM {table})"
                ).fetchone()
                return (row[0], row[1]) if row and row[0] else (None, 0)
            except Exception:
                return (None, 0)

        def _latest_ts(table, ts_col):
            try:
                row = conn.execute(
                    f"SELECT MAX({ts_col}) FROM {table}"
                ).fetchone()
                return row[0] if row else None
            except Exception:
                return None

        def _gap_days(date_str):
            if not date_str:
                return None
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                return (now - d).days
            except Exception:
                return None

        # ── 实时数据（东财抓取，盘中更新）
        zt_date, zt_count = _latest_date("zt_pool")
        dt_date, dt_count = _latest_date("dt_pool")
        news_ts = _latest_ts("cls_news", "pub_time")
        sector_flow_ts = _latest_ts("sector_flow", "fetch_time")
        lhb_date, lhb_count = _latest_date("lhb_data")
        northbound_ts = _latest_ts("northbound_flow", "fetch_time")

        # ── 静态数据（本地量化日线，daily_compute 每日09:00生成）
        emotion_date, _ = _latest_date("market_emotion")
        sector_density_date, _ = _latest_date("sector_zt_density")
        advance_decline_date, _ = _latest_date("advance_decline")
        lianzban_date, lianzban_count = _latest_date("lianzban_chain")

    # ── 盘期判断（复用 get_market_session）
    mkt = get_market_session(now)
    is_trade_day = mkt["is_trade_day"]
    is_trading_time = mkt["is_trading_time"]
    session = mkt["session"]

    status["realtime"] = {
        "zt_pool":        {"latest_date": zt_date, "count": zt_count, "fresh": zt_date == today},
        "dt_pool":        {"latest_date": dt_date, "count": dt_count, "fresh": dt_date == today},
        "cls_news":       {"latest_time": news_ts, "fresh": bool(news_ts and news_ts[:10] == today)},
        "sector_flow":    {"latest_time": sector_flow_ts, "fresh": bool(sector_flow_ts and sector_flow_ts[:10] == today)},
        "lhb_data":       {
            "latest_date": lhb_date, "count": lhb_count,
            "fresh": lhb_date == today,
            "note": "17:30后才有，盘中为空属正常" if session not in ("evening",) else ("已出榜" if lhb_date == today else "盘后仍为空，可能抓取失败"),
        },
        "northbound":     {"latest_time": northbound_ts, "fresh": bool(northbound_ts and northbound_ts[:10] == today)},
    }

    emotion_gap = _gap_days(emotion_date)
    status["static"] = {
        "market_emotion":   {
            "latest_date": emotion_date,
            "fresh": emotion_date == today,
            "gap_days": emotion_gap,
            "note": "由 daily_compute 每日09:00生成，依赖 QUANT_DATA_ROOT 本地量价文件",
        },
        "sector_zt_density": {"latest_date": sector_density_date, "fresh": sector_density_date == today, "gap_days": _gap_days(sector_density_date)},
        "advance_decline":   {"latest_date": advance_decline_date, "fresh": advance_decline_date == today, "gap_days": _gap_days(advance_decline_date)},
        "lianzban_chain":    {"latest_date": lianzban_date, "count": lianzban_count, "fresh": lianzban_date == today},
    }

    # ── 冲突检测（盘前 session 下静态数据是昨天属正常，不触发冲突）
    if is_trade_day and not status["static"]["market_emotion"]["fresh"]:
        conflicts.append(
            f"【数据冲突】zt_pool今日有{zt_count}条（确认交易日），"
            f"但market_emotion最新日期是{emotion_date}（落后{emotion_gap}天）。"
            f"daily_compute可能未运行或QUANT_DATA_ROOT未配置。"
        )

    if is_trade_day and not status["static"]["sector_zt_density"]["fresh"]:
        conflicts.append(
            f"【数据冲突】zt_pool今日有数据，但sector_zt_density最新日期是{sector_density_date}，"
            f"板块密度分析需切换到实时降级推算。"
        )

    # ── 降级提示：静态数据不可用时如何用实时数据替代
    if not status["static"]["market_emotion"]["fresh"] and is_trade_day:
        fallback_hints["emotion_proxy"] = (
            "market_emotion缺失时，可从zt_pool/dt_pool直接推算市场温度：\n"
            f"  - zt_count = {zt_count}（来自zt_pool实时数据）\n"
            f"  - dt_count = {dt_count}（来自dt_pool实时数据）\n"
            "  - zb_rate = zt_pool中zb_count>0的行数 / zt_pool总行数\n"
            "  - max_lianzban = zt_pool中zt_count的最大值\n"
            "  注意：这是实时快照推算，精度低于日线静态数据，请在结论中标注"
        )

    if not status["static"]["sector_zt_density"]["fresh"] and is_trade_day:
        fallback_hints["sector_proxy"] = (
            "sector_zt_density缺失时，可从zt_pool按sector字段分组统计：\n"
            "  - 同一行业/板块涨停数 ÷ 该行业总股票数（粗估）\n"
            "  - 或直接按sector出现频次排序，找高频板块作为主线线索\n"
            "  注意：这是粗略估算，结论中需标注sector_zt_density数据不可用"
        )

    if not status["realtime"]["lhb_data"]["fresh"] and now.hour >= 18:
        fallback_hints["lhb_missing"] = (
            "当前时间已过18:00但龙虎榜仍无今日数据，可能抓取失败。"
            "个股评级应降低置信度，data_gaps中标注'龙虎榜缺失'。"
        )

    # ── 检查一：静态数据是否滞后超过1个交易日（T-2 告警）
    # 逻辑：找到今天之前最近2个交易日 [T-1, T-2]
    #   - 已过 daily_compute 运行时间（09:30后）且静态数据不是 T-1 或今天 → 告警
    #   - 09:30前盘前：静态数据是 T-1 属正常（daily_compute 还没跑）
    data_alerts = []
    static_stale_abort = False
    if is_trade_day:
        try:
            prev_dates = get_prev_trade_dates(now.date(), n=2)
            # prev_dates[0] = T-1, prev_dates[1] = T-2，均为 numpy datetime64 或 date
            t1 = str(prev_dates[0])[:10] if len(prev_dates) > 0 else None
            t2 = str(prev_dates[1])[:10] if len(prev_dates) > 1 else None
            after_compute = (now.hour * 60 + now.minute) >= 9 * 60 + 30
            emotion_latest = emotion_date  # YYYY-MM-DD str or None
            # 允许的最新日期：今天（盘后计算完）或 T-1（盘前/刚开盘）
            allowed = {today, t1} if t1 else {today}
            if after_compute and emotion_latest and emotion_latest not in allowed:
                # 静态数据比 T-1 还老，说明昨天的数据没算出来
                data_alerts.append({
                    "level": "error",
                    "code": "STATIC_DATA_STALE",
                    "message": (
                        f"静态数据滞后超过1个交易日：市场情绪数据最新是 {emotion_latest}，"
                        f"上一交易日是 {t1}，数据缺失一个完整交易日。"
                        f"每日计算任务可能昨日未执行，建议在设置页手动触发计算后再跑分析。"
                    ),
                    "latest_date": emotion_latest,
                    "expected_date": t1,
                })
                static_stale_abort = True
        except Exception:
            pass  # 日历查询失败，不阻断

    # ── 检查二：盘中实时数据是否严重滞后（接口挂掉）
    realtime_stale_abort = False
    if is_trading_time:
        def _stale_minutes(ts_str):
            """返回 ts_str 距现在多少分钟，ts_str 格式 YYYY-MM-DD HH:MM:SS 或 ISO"""
            if not ts_str:
                return None
            try:
                from datetime import datetime as _dt2
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        t = _dt2.strptime(ts_str[:19], fmt)
                        return (now - t).total_seconds() / 60
                    except ValueError:
                        continue
                return None
            except Exception:
                return None

        # market_pulse (realtime_snapshot)：30秒更新，超5分钟告警
        pulse_ts = _latest_ts("market_pulse", "fetch_time") if False else sector_flow_ts  # 下面重新查
        # 直接从 DB 查 market_pulse 最新时间
        import sqlite3 as _sql
        from db.storage import DB_PATH as _DB
        with _sql.connect(_DB) as _conn:
            _row = _conn.execute("SELECT MAX(fetch_time) FROM market_pulse").fetchone()
            pulse_ts = _row[0] if _row else None

        pulse_lag = _stale_minutes(pulse_ts)
        flow_lag = _stale_minutes(sector_flow_ts)

        if pulse_lag is not None and pulse_lag > 5:
            data_alerts.append({
                "level": "error",
                "code": "REALTIME_SNAPSHOT_STALE",
                "message": (
                    f"实时行情快照已 {pulse_lag:.0f} 分钟未更新（最后更新：{pulse_ts}），"
                    f"正常应每30秒刷新一次，可能是数据接口临时故障，盘中分析数据暂不可靠。"
                ),
                "last_update": pulse_ts,
                "lag_minutes": round(pulse_lag, 1),
                "threshold_minutes": 5,
            })
            realtime_stale_abort = True

        if flow_lag is not None and flow_lag > 45:
            data_alerts.append({
                "level": "warning",
                "code": "SECTOR_FLOW_STALE",
                "message": (
                    f"行业资金流已 {flow_lag:.0f} 分钟未更新（最后更新：{sector_flow_ts}），"
                    f"正常应每15分钟刷新一次，资金流向分析结果可能有所滞后。"
                ),
                "last_update": sector_flow_ts,
                "lag_minutes": round(flow_lag, 1),
                "threshold_minutes": 45,
            })
        # zt_pool 只有 trade_date 没有精确时间戳，盘中无法判断滞后，跳过

    # abort_reason 供 orchestrator 读取，决定是否跳过分析
    abort_reason = None
    if static_stale_abort:
        abort_reason = next(a["message"] for a in data_alerts if a["code"] == "STATIC_DATA_STALE")
    elif realtime_stale_abort:
        abort_reason = next(a["message"] for a in data_alerts if a["code"] == "REALTIME_SNAPSHOT_STALE")

    SESSION_INSTRUCTIONS = {
        "pre_market": (
            "当前为盘前时段（工作日 06:00–09:15）。"
            "静态数据（market_emotion/sector_zt_density）显示昨日日期属正常——"
            "daily_compute 尚未运行，数据将在集合竞价后刷新。"
            "此时应基于昨日静态数据 + 今日新闻做前瞻判断，"
            "重点关注催化剂质量和昨日情绪延续性，结论中注明'盘前预判'。"
        ),
        "call_auction": (
            "当前为集合竞价（09:15–09:30）。实时数据刚开始采集，zt_pool 可能尚无数据。"
            "以昨日静态数据为主，结合今日新闻/竞价委比做开盘前判断，结论注明'竞价阶段预判'。"
        ),
        "morning": (
            "当前为上午连续竞价（09:30–11:30）。实时数据持续更新。"
            "若 static 数据 stale 使用 fallback_hints 降级推算；"
            "若 static 数据 fresh，以 static 为主、realtime 交叉验证。"
        ),
        "lunch_break": (
            "当前为午休（11:30–13:00）。上午行情已定格，下午尚未开盘。"
            "可基于上午涨停池/资金流做半日复盘，结论注明'午盘快照'。"
        ),
        "afternoon": (
            "当前为下午连续竞价（13:00–15:00）。实时数据持续更新，逻辑同 morning。"
        ),
        "post_close": (
            "当前为收盘后（15:00–17:00）。龙虎榜尚未发布（17:30 后才有），"
            "个股评级置信度受限，data_gaps 中标注'龙虎榜待出'。"
        ),
        "evening": (
            "当前为盘后时段（17:00+）。龙虎榜应已发布；若 lhb_data.fresh=false 说明抓取失败，需标注。"
            "静态数据应已由 daily_compute 更新；若仍 stale 说明 QUANT_DATA_ROOT 未配置，"
            "使用 fallback_hints 降级推算。"
        ),
        "pre_open": (
            "当前为交易日集合竞价前（09:15 前）。静态数据显示昨日日期属正常，处理方式同 pre_market。"
        ),
        "weekend": (
            "当前为周末。无实时交易数据，只能基于新闻/历史静态数据做下周前瞻判断，"
            "结论中明确标注'周末前瞻，数据截至上周五收盘'。"
        ),
        "holiday": (
            "当前为节假日（AKShare 交易日历确认非交易日）。"
            "无实时交易数据，只能基于新闻/历史静态数据做节后前瞻判断，"
            "结论中明确标注'节假日前瞻，数据截至节前最后交易日收盘'。"
        ),
    }

    # ── QMT 数据源状态
    import os as _os
    qmt_enabled = _os.environ.get("QMT_ENABLED", "false").lower() in ("true", "1", "yes")
    qmt_connected = False
    if qmt_enabled:
        try:
            from fetcher.xtquant_breadth import connect as _qmt_connect
            qmt_connected = _qmt_connect()
        except Exception:
            pass
    if qmt_enabled and not qmt_connected:
        data_alerts.append({
            "level": "warning",
            "code": "QMT_DISCONNECTED",
            "message": "QMT 已启用但 miniQMT 连接失败，market_breadth 数据暂不可用，请确认 QMT 客户端已启动。",
        })

    # static_emotion_date：skill 用此字段判断静态数据是否与今日匹配
    # 若 static_emotion_date < today 且处于交易时段 → 触发路径 D（实时重建）
    _out({
        "check_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "session": session,
        "is_trade_day": is_trade_day,
        "is_trading_time": is_trading_time,
        "static_emotion_date": emotion_date,   # 静态 market_emotion 实际对应的交易日
        "data_alerts": data_alerts,            # 前端直接展示，level=error 时用户可见
        "abort_reason": abort_reason,          # 非空时 orchestrator 应跳过分析
        "realtime_data_status": {
            **status["realtime"],
            "xtquant": {"enabled": qmt_enabled, "connected": qmt_connected},
        },
        "static_data_status": status["static"],
        "conflicts": conflicts,
        "fallback_hints": {
            **fallback_hints,
            "market_breadth_source": (
                "xtquant（全市逐票精确值）" if qmt_connected
                else ("不可用（QMT 未启用）" if not qmt_enabled
                      else "不可用（QMT 已启用但未连接）")
            ),
        },
        "instruction": SESSION_INSTRUCTIONS.get(session, ""),
    })


def cmd_market_emotion(args):
    from db.storage import get_agent_context
    ctx = get_agent_context()
    emotion = ctx.get("market_emotion_today")
    if not emotion:
        _out({"error": "INSUFFICIENT_DATA", "reason": "今日 market_emotion 记录不存在，可能是非交易日或日线计算未运行"})
        return
    # 字段重命名：DB 用 *_total，skill 规范和前端用 *_count / yesterday_premium
    emotion = dict(emotion)
    emotion["zt_count"] = emotion.pop("zt_total", None)
    emotion["dt_count"] = emotion.pop("dt_total", None)
    emotion["zb_count"] = emotion.pop("zb_total", None)
    emotion["yesterday_premium"] = emotion.pop("zt_yesterday_premium", None)
    _out(emotion)


def cmd_yesterday_premium(args):
    """
    实时推算隔日溢价率（ZTBX）。

    取昨日（最近一个有数据的交易日）涨停股名单，用 market_pulse 中最新实时价格
    计算均值收益率。盘中结果为未收盘近似值，标注 intraday_estimate=true。
    收盘后结果为收盘价，标注 intraday_estimate=false。

    用途：当 static_emotion_date < today（路径 D）时，替代静态 yesterday_premium。
    """
    import sqlite3 as _sql
    from db.storage import DB_PATH, get_zt_pool

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    is_market_closed = now.hour >= 15

    with _sql.connect(DB_PATH) as conn:
        # 找最近有涨停数据的交易日（通常是昨日，节后可能隔多天）
        row = conn.execute(
            "SELECT MAX(trade_date) FROM zt_pool"
        ).fetchone()
        prev_trade_date = row[0] if row and row[0] else None

    if not prev_trade_date or prev_trade_date == today:
        _out({
            "error": "NO_PREV_ZT_DATA",
            "reason": "未找到前一交易日涨停池数据，无法推算隔日溢价",
        })
        return

    # 拿前一交易日全部涨停股的收盘价（prev_close）
    prev_zt = get_zt_pool(prev_trade_date)
    if not prev_zt:
        _out({
            "error": "NO_PREV_ZT_DATA",
            "reason": f"前一交易日（{prev_trade_date}）涨停池为空",
        })
        return

    prev_codes = [r.get("stock_code") for r in prev_zt if r.get("stock_code")]

    with _sql.connect(DB_PATH) as conn:
        # 用 market_pulse 最新快照中这些股票的价格
        placeholders = ",".join("?" * len(prev_codes))
        rows = conn.execute(
            f"""
            SELECT mp.stock_code, mp.price, mp.prev_close
            FROM market_pulse mp
            INNER JOIN (
                SELECT stock_code, MAX(fetch_time) AS latest
                FROM market_pulse
                WHERE trade_date = ?
                GROUP BY stock_code
            ) latest_snap ON mp.stock_code = latest_snap.stock_code
                          AND mp.fetch_time = latest_snap.latest
            WHERE mp.stock_code IN ({placeholders})
              AND mp.prev_close IS NOT NULL
              AND mp.prev_close > 0
            """,
            [today] + prev_codes,
        ).fetchall()

    if not rows:
        _out({
            "error": "NO_PULSE_DATA",
            "reason": f"market_pulse 中未找到前一交易日涨停股（{prev_trade_date}）的今日价格数据",
            "prev_trade_date": prev_trade_date,
            "prev_zt_count": len(prev_codes),
        })
        return

    returns = [(price - prev_close) / prev_close for _, price, prev_close in rows if prev_close > 0]
    if not returns:
        _out({"error": "CALC_FAILED", "reason": "收益率计算失败，价格数据异常"})
        return

    ztbx = sum(returns) / len(returns)
    positive = sum(1 for r in returns if r > 0)

    _out({
        "ztbx": round(ztbx * 100, 2),          # 百分比，如 2.3 表示 +2.3%
        "ztbx_pct": f"{ztbx * 100:+.2f}%",
        "sample_count": len(returns),           # 实际有价格的样本数
        "prev_zt_count": len(prev_codes),       # 前日涨停总数
        "positive_ratio": round(positive / len(returns), 3),  # 正收益占比
        "prev_trade_date": prev_trade_date,
        "price_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "intraday_estimate": not is_market_closed,
        "note": (
            "盘中近似值，使用实时价格（未收盘），收盘后将更准确" if not is_market_closed
            else "收盘后计算，使用收盘价，结果准确"
        ),
    })


def cmd_zt_pool(args):
    from db.storage import get_zt_pool
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_zt_pool(today)
    if not data:
        _out({"note": "今日涨停池为空，可能是非交易日或数据未采集", "data": []})
        return
    # 字段规范化：将 stock_code/stock_name 映射为 code/name，供 agent 统一访问
    normalized = []
    for row in data:
        normalized.append({
            "code": row.get("stock_code", ""),
            "name": row.get("stock_name", ""),
            "zt_count": row.get("zt_count", 1),
            "sector": row.get("sector", ""),
            "seal_time": row.get("first_zt_time", ""),
            "last_zt_time": row.get("last_zt_time", ""),
            "seal_amount": row.get("seal_amount"),
            "zb_count": row.get("zb_count", 0),
            "turnover_rate": row.get("turnover_rate"),
            "circ_mv": row.get("circ_mv"),
            "trade_date": row.get("trade_date", ""),
        })
    _out(normalized)


def cmd_sector_zt_density(args):
    from db.storage import get_sector_zt_density
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_sector_zt_density(today)
    if not data:
        _out({"note": "今日行业涨停密度数据为空", "data": []})
        return
    _out(data[:10])


def cmd_sector_flow_accel(args):
    from db.storage import get_sector_flow_accel
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_sector_flow_accel(today)
    if not data:
        _out({"note": "今日机构资金加速度数据为空", "data": []})
        return
    _out(data[:10])


def cmd_lhb(args):
    from db.storage import get_lhb_data, get_lhb_seat
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_lhb_data(today)

    # 附加席位明细（来自本地量化数据，含游资识别）
    seats = get_lhb_seat(today)
    seat_map: dict = {}
    for s in seats:
        code = s.get("stock_code", "")
        seat_map.setdefault(code, []).append({
            "seat_name":  s.get("seat_name"),
            "seat_type":  s.get("seat_type"),   # 游资/机构/北向/其他
            "net_amount": s.get("net_amount"),
            "buy_amount": s.get("buy_amount"),
            "sell_amount": s.get("sell_amount"),
        })

    if not data and not seat_map:
        _out({
            "note": "龙虎榜今日暂无数据。通常收盘后17:30发布，本地席位数据18:30同步。",
            "data": []
        })
        return

    # 将席位信息合并到 lhb_data
    for row in data:
        code = row.get("stock_code", "")
        row_seats = seat_map.get(code, [])
        row["seats"] = row_seats
        # 快捷字段：席位性质汇总（供 Claude 直接判断）
        types = {s["seat_type"] for s in row_seats}
        if "游资" in types and "机构" in types:
            row["seat_nature"] = "游资+机构混合"
        elif "机构" in types:
            row["seat_nature"] = "机构主导"
        elif "游资" in types:
            row["seat_nature"] = "游资主导"
        elif row_seats:
            row["seat_nature"] = "其他"
        else:
            row["seat_nature"] = "席位数据未就绪"

    _out(data if data else list(seat_map.values()))


def cmd_lianzban_chain(args):
    from db.storage import get_lianzban_chain
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_lianzban_chain(today)
    if not data:
        _out({"note": "今日连板链条数据为空（无2板及以上个股）", "data": []})
        return
    _out(data)


def cmd_volume_breakout(args):
    from db.storage import get_volume_breakout
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_volume_breakout(today)
    if not data:
        _out({"note": "今日成交额异动数据为空", "data": []})
        return
    _out(data)


def cmd_news(args):
    from db.storage import get_cls_news
    from agent.classifier import batch_classify

    hours = getattr(args, "hours", 2)
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    all_news = get_cls_news(limit=100)
    recent = [n for n in all_news if n.get("pub_time", "") >= cutoff]

    # 过滤分类，只留有价值的
    classified = batch_classify(recent)
    filtered = [n for n in classified if n.get("keep", True)]

    _out({
        "query_hours": hours,
        "cutoff": cutoff,
        "total_raw": len(recent),
        "after_filter": len(filtered),
        "news": filtered[:30]
    })


def cmd_policy_news(args):
    from db.storage import get_policy_news
    from agent.classifier import batch_classify

    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    all_policy = get_policy_news(limit=50)
    recent = [n for n in all_policy if (n.get("pub_time") or "") >= three_days_ago]

    classified = batch_classify(recent)
    filtered = [n for n in classified if n.get("urgency", 1) >= 2]

    _out({
        "since": three_days_ago,
        "total_raw": len(recent),
        "after_filter": len(filtered),
        "news": filtered[:20]
    })


def cmd_f10(args):
    from db.storage import get_fundamentals_f10
    today = datetime.now().strftime("%Y-%m-%d")

    codes_str = getattr(args, "codes", "") or ""
    if not codes_str:
        _out({"error": "请通过 --codes 300XXX,600XXX 指定股票代码"})
        return

    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    results = []
    for code in codes[:20]:  # 最多20只
        rows = get_fundamentals_f10(today, code)
        if rows:
            # 只取公司概况
            overview = next((r for r in rows if r.get("category") == "公司概况"), None)
            if overview:
                results.append({
                    "stock_code": code,
                    "category": "公司概况",
                    "content": (overview.get("content") or "")[:500]
                })
            else:
                results.append({"stock_code": code, "note": "无公司概况数据"})
        else:
            results.append({"stock_code": code, "note": "无F10数据（该股可能未进入今日涨停/强势池）"})

    _out(results)


def cmd_name(args):
    """从本地量价 CSV 查股票名称。--codes 逗号分隔代码列表。
    优先查 zt_pool/volume_breakout 当日数据，找不到再读 CSV 最后一行。
    """
    from db.storage import get_zt_pool, get_volume_breakout
    from quant.loader import DATA_ROOT

    codes_str = getattr(args, "codes", "") or ""
    if not codes_str:
        _out({"error": "请通过 --codes 002491,688146 指定股票代码"})
        return

    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    today = datetime.now().strftime("%Y-%m-%d")

    # 先从当日 zt_pool / volume_breakout 建立 code→name 映射（最快，不读文件）
    name_map: dict[str, str] = {}
    try:
        for row in (get_zt_pool(today) or []):
            code = row.get("stock_code", "")
            name = row.get("stock_name", "")
            if code and name:
                name_map[code] = name
    except Exception:
        pass
    try:
        for row in (get_volume_breakout(today) or []):
            code = row.get("stock_code", "")
            name = row.get("stock_name", "")
            if code and name:
                name_map[code] = name
    except Exception:
        pass

    results = []
    for raw_code in codes[:50]:
        if raw_code in name_map:
            results.append({"code": raw_code, "name": name_map[raw_code], "source": "db"})
            continue

        # 回落到本地 CSV
        name = ""
        if DATA_ROOT:
            # 尝试几种常见前缀格式
            for candidate in [raw_code, f"sh{raw_code}", f"sz{raw_code}", f"bj{raw_code}"]:
                csv_path = DATA_ROOT / "stock-trading-data-pro" / f"{candidate}.csv"
                if csv_path.exists():
                    try:
                        import csv as _csv
                        with open(csv_path, encoding="gbk") as f:
                            lines = f.readlines()
                        # 第2行是表头，最后一行是最新数据
                        if len(lines) >= 3:
                            last = lines[-1].strip()
                            if last:
                                cols = next(_csv.reader([last]))
                                name = cols[1] if len(cols) > 1 else ""
                    except Exception:
                        pass
                    if name:
                        break

        if name:
            results.append({"code": raw_code, "name": name, "source": "csv"})
        else:
            results.append({"code": raw_code, "name": "", "source": "not_found",
                            "note": "本地数据未找到，禁止从记忆猜测名称"})

    _out(results)


def cmd_sector_flow(args):
    from db.storage import get_sector_flow_latest
    data = get_sector_flow_latest(source_type="industry")
    if not data:
        _out({"note": "行业资金流数据为空", "data": []})
        return
    _out(data[:20])


def cmd_lockup(args):
    """查询解禁数据。--codes 指定个股，不传则返回未来30天全市场解禁列表。"""
    codes_str = getattr(args, "codes", "") or ""

    if codes_str:
        from db.storage import get_lockup_expiry_by_code
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        results = []
        for code in codes[:20]:
            rows = get_lockup_expiry_by_code(code, days=30)
            if rows:
                total_ratio = sum(r.get("lift_ratio", 0) for r in rows)
                results.append({
                    "stock_code": code,
                    "stock_name": rows[0].get("stock_name", ""),
                    "lockup_events": rows,
                    "total_lift_ratio_30d": round(total_ratio, 4),
                    "risk_level": "高" if total_ratio >= 0.10 else ("中" if total_ratio >= 0.05 else "低"),
                })
            else:
                results.append({
                    "stock_code": code,
                    "note": "30天内无解禁记录",
                    "risk_level": "无",
                })
        _out(results)
    else:
        from db.storage import get_lockup_expiry
        data = get_lockup_expiry(days=30)
        if not data:
            _out({"note": "未来30天无解禁记录", "data": []})
            return
        _out(data[:50])


def cmd_market_pulse(args):
    """最近1小时实时涨跌趋势。附当前炸板率和趋势方向（改善中/恶化中/平稳）。"""
    from db.storage import get_market_pulse_latest

    rows = get_market_pulse_latest(n=120)
    if not rows:
        _out({"note": "market_pulse 数据为空，可能是非交易日或数据未采集", "data": []})
        return

    # 按时间升序排列（DB 返回的是 DESC，翻转便于计算趋势）
    rows = list(reversed(rows))

    # ── 炸板率：取最新一条，用 zb_count / zt_count
    latest = rows[-1]
    zt = latest.get("real_zt") or latest.get("zt_count") or 0
    zb = latest.get("zb_count") or 0
    zb_rate = round(zb / zt, 4) if zt > 0 else None

    # ── 趋势方向：最近10条 advance 均值 vs 前10条均值
    trend = "平稳"
    if len(rows) >= 20:
        recent10 = [r.get("advance") or 0 for r in rows[-10:]]
        prev10   = [r.get("advance") or 0 for r in rows[-20:-10]]
        recent_avg = sum(recent10) / len(recent10)
        prev_avg   = sum(prev10) / len(prev10)
        if prev_avg > 0:
            diff_pct = (recent_avg - prev_avg) / prev_avg
            if diff_pct > 0.02:
                trend = "改善中"
            elif diff_pct < -0.02:
                trend = "恶化中"

    # 输出字段白名单（保持轻量）
    output_fields = ("fetch_time", "real_zt", "real_dt", "advance", "decline", "activity", "zt_dt_ratio")
    data = [
        {k: r.get(k) for k in output_fields}
        for r in rows
    ]

    _out({
        "zb_rate": zb_rate,
        "trend": trend,
        "sample_count": len(data),
        "data": data,
    })


def cmd_northbound(args):
    """北向资金最新净买入（沪深港通各渠道）。"""
    from db.storage import get_northbound_flow_latest

    data = get_northbound_flow_latest()
    if not data:
        _out({"note": "北向资金数据为空，可能是非交易日或数据未采集", "data": []})
        return
    _out(data)


def cmd_industry_ranking(args):
    """行业板块实时涨跌排行 Top20，按 change_pct 降序。"""
    from db.storage import get_industry_ranking_latest

    data = get_industry_ranking_latest()
    if not data:
        _out({"note": "行业板块排行数据为空", "data": []})
        return

    # 已按 change_pct 降序从 DB 返回，直接取前20
    output_fields = ("sector_name", "change_pct", "up_count", "down_count",
                     "lead_stock", "lead_pct", "fetch_time")
    top20 = [
        {k: r.get(k) for k in output_fields}
        for r in data[:20]
    ]
    _out(top20)


def cmd_concept_flow(args):
    """概念资金流 Top20，按 net_amount 降序。"""
    from db.storage import get_concept_flow_latest

    data = get_concept_flow_latest(top_n=20)
    if not data:
        _out({"note": "概念资金流数据为空", "data": []})
        return
    _out(data)


def cmd_advance_decline(args):
    """
    全市涨跌家数 + 成交额。
    优先返回日线数据（含 amount_ratio）；日线无数据时降级使用 market_pulse 实时快照。
    """
    from db.storage import get_advance_decline, get_market_pulse_latest

    today = datetime.now().strftime("%Y-%m-%d")
    daily = get_advance_decline(today)

    if daily:
        daily["data_source"] = "daily"
        _out(daily)
        return

    # 日线数据不可用，降级到 market_pulse 最新一条
    pulse_rows = get_market_pulse_latest(n=1)
    if not pulse_rows:
        _out({
            "error": "INSUFFICIENT_DATA",
            "reason": "日线 advance_decline 和 market_pulse 均无今日数据",
        })
        return

    p = pulse_rows[0]
    _out({
        "trade_date": today,
        "advance":    p.get("advance"),
        "decline":    p.get("decline"),
        "fetch_time": p.get("fetch_time"),
        "data_source": "realtime_pulse_estimate",
        "note": "日线数据未就绪，使用实时快照估算，精度低于日线，结论中需标注",
    })


def cmd_strong_pool(args):
    """今日强势股池（按 change_pct 降序）。"""
    from db.storage import get_strong_pool

    today = datetime.now().strftime("%Y-%m-%d")
    data = get_strong_pool(today)
    if not data:
        _out({"note": "今日强势股池为空，可能是非交易日或数据未计算", "data": []})
        return

    output_fields = ("stock_code", "stock_name", "sector", "change_pct", "trade_date")
    result = [
        {k: r.get(k) for k in output_fields}
        for r in data
    ]
    _out(result)


def cmd_big_deal(args):
    """大单异动最新50条，按成交时间降序。"""
    from db.storage import get_big_deal_latest

    data = get_big_deal_latest(limit=50)
    if not data:
        _out({"note": "大单异动数据为空", "data": []})
        return

    result = []
    for r in data:
        result.append({
            "deal_time":   r.get("deal_time"),
            "stock_code":  r.get("stock_code"),
            "stock_name":  r.get("stock_name"),
            "direction":   r.get("deal_type"),   # DB 字段 deal_type 映射为 direction
            "amount":      r.get("amount"),
            "price":       r.get("price"),
        })
    _out(result)


def cmd_market_breadth(args):
    """
    实时全市涨跌家数 + 成交额（1分钟粒度，来源：xtquant 全市逐票精确值）。
    返回最近120条，按 fetch_time 升序（最旧在前）便于趋势判断。
    额外计算 trend：最近10条 up_count 均值 vs 前10条均值，判断改善/恶化/平稳。
    """
    from db.storage import get_market_breadth_latest

    rows = get_market_breadth_latest(n=120)
    if not rows:
        _out({"note": "market_breadth 数据为空，xtquant 不可用或 QMT 客户端未启动", "data": []})
        return

    # 判断当前实际数据来源（取最新一条的 source 字段）
    latest_source = rows[-1].get("source", "unknown") if rows else "unknown"
    is_xtquant = latest_source == "xtquant"

    # 趋势计算：用 TOTAL 行，无则用 SH 行
    trend_rows = [r for r in rows if r.get("market") == "TOTAL"]
    if not trend_rows:
        trend_rows = [r for r in rows if r.get("market") == "SH"]

    trend = "平稳"
    if len(trend_rows) >= 20:
        recent10 = [r.get("up_count") or 0 for r in trend_rows[-10:]]
        prev10   = [r.get("up_count") or 0 for r in trend_rows[-20:-10]]
        recent_avg = sum(recent10) / len(recent10)
        prev_avg   = sum(prev10) / len(prev10)
        if prev_avg > 0:
            diff_pct = (recent_avg - prev_avg) / prev_avg
            if diff_pct > 0.02:
                trend = "改善中"
            elif diff_pct < -0.02:
                trend = "恶化中"

    # 最新快照：TOTAL 行为全市合计，SH/SZ 为分市场
    latest_total = next((r for r in reversed(rows) if r.get("market") == "TOTAL"), None)
    latest_sh    = next((r for r in reversed(rows) if r.get("market") == "SH"), None)
    latest_sz    = next((r for r in reversed(rows) if r.get("market") == "SZ"), None)

    output_fields = ("fetch_time", "market", "source", "up_count", "down_count",
                     "flat_count", "ad_ratio", "index_amount", "index_price", "total_amount")
    data = [
        {k: r.get(k) for k in output_fields}
        for r in rows
    ]

    _out({
        "data_source": latest_source,
        "data_source_note": (
            "全市逐票精确值（xtquant），total_amount 为 SH+SZ 合计成交额" if is_xtquant
            else "数据来源未知，total_amount 可能为 null"
        ),
        "trend": trend,
        "sample_count": len(data),
        "latest_total": {k: latest_total.get(k) for k in output_fields} if latest_total else None,
        "latest_sh":    {k: latest_sh.get(k)    for k in output_fields} if latest_sh    else None,
        "latest_sz":    {k: latest_sz.get(k)    for k in output_fields} if latest_sz    else None,
        "data": data,
    })


def cmd_context(args):
    """完整上下文，一次性返回所有分析所需数据。"""
    from db.storage import get_agent_context
    from agent.classifier import batch_classify

    ctx = get_agent_context()

    # 新闻过滤
    all_news = ctx.get("recent_cls_news", []) + ctx.get("policy_news_today", [])
    classified = batch_classify(all_news)
    filtered_news = [n for n in classified if n.get("keep") and n.get("urgency", 1) >= 2]

    _out({
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_emotion": ctx.get("market_emotion_today"),
        "sector_zt_density_top10": ctx.get("sector_zt_density_top10", []),
        "sector_flow_accel_top10": ctx.get("sector_flow_accel_today", []),
        "zt_pool": ctx.get("zt_pool_today", []),
        "lianzban_chain": ctx.get("lianzban_chain_today", []),
        "volume_breakout": ctx.get("volume_breakout_today", []),
        "lhb": ctx.get("lhb_today", []),
        "filtered_news": filtered_news[:20],
        "f10": ctx.get("f10_today", []),
    })


COMMANDS = {
    "data_health":         cmd_data_health,
    "market_emotion":      cmd_market_emotion,
    "yesterday_premium":   cmd_yesterday_premium,
    "zt_pool":             cmd_zt_pool,
    "sector_zt_density": cmd_sector_zt_density,
    "sector_flow_accel": cmd_sector_flow_accel,
    "lhb":               cmd_lhb,
    "lianzban_chain":    cmd_lianzban_chain,
    "volume_breakout":   cmd_volume_breakout,
    "news":              cmd_news,
    "policy_news":       cmd_policy_news,
    "f10":               cmd_f10,
    "name":              cmd_name,
    "sector_flow":       cmd_sector_flow,
    "lockup":            cmd_lockup,
    "context":           cmd_context,
    "market_pulse":      cmd_market_pulse,
    "northbound":        cmd_northbound,
    "industry_ranking":  cmd_industry_ranking,
    "concept_flow":      cmd_concept_flow,
    "advance_decline":   cmd_advance_decline,
    "strong_pool":       cmd_strong_pool,
    "big_deal":          cmd_big_deal,
    "market_breadth":          cmd_market_breadth,
}


def main():
    parser = argparse.ArgumentParser(description="market-radar 数据查询工具")
    parser.add_argument("command", choices=list(COMMANDS.keys()), help="查询类型")
    parser.add_argument("--hours", type=int, default=2, help="新闻查询小时数（news 命令使用）")
    parser.add_argument("--codes", type=str, default="", help="股票代码列表，逗号分隔（f10/lockup 命令使用）")
    args = parser.parse_args()

    try:
        COMMANDS[args.command](args)
    except Exception as e:
        _out({"error": str(e), "command": args.command})
        sys.exit(1)


if __name__ == "__main__":
    main()
