from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "market.db"

# 进程级写锁：SQLite WAL 允许并发读，但并发写仍会产生 "database is locked"。
# 用一个 threading.Lock 在 Python 层序列化所有写操作，彻底消除锁冲突。
_write_lock = threading.Lock()


def _migrate(conn):
    sf_cols = {row[1] for row in conn.execute("PRAGMA table_info(sector_flow)")}
    if "source_type" not in sf_cols:
        conn.execute("ALTER TABLE sector_flow ADD COLUMN source_type TEXT DEFAULT 'industry'")
    cls_cols = {row[1] for row in conn.execute("PRAGMA table_info(cls_news)")}
    if "source" not in cls_cols:
        conn.execute("ALTER TABLE cls_news ADD COLUMN source TEXT DEFAULT '财联社'")
    if "link" not in cls_cols:
        conn.execute("ALTER TABLE cls_news ADD COLUMN link TEXT DEFAULT ''")
    # zt_pool 新增字段迁移：兼容旧数据库，如果字段不存在则 ALTER TABLE 追加
    zt_cols = {row[1] for row in conn.execute("PRAGMA table_info(zt_pool)")}
    for col, coldef in [
        ("last_zt_time",  "TEXT"),
        ("seal_amount",   "REAL"),
        ("zb_count",      "INTEGER DEFAULT 0"),
        ("turnover_rate", "REAL"),
        ("circ_mv",       "REAL"),
    ]:
        if col not in zt_cols:
            conn.execute(f"ALTER TABLE zt_pool ADD COLUMN {col} {coldef}")
    # lhb_data 新增字段迁移
    lhb_cols = {row[1] for row in conn.execute("PRAGMA table_info(lhb_data)")}
    for col, coldef in [
        ("change_pct",    "REAL"),
        ("interpret",     "TEXT"),
        ("net_buy_ratio", "REAL"),
    ]:
        if col not in lhb_cols:
            conn.execute(f"ALTER TABLE lhb_data ADD COLUMN {col} {coldef}")
    # reason 字段已存在于建表语句，仅在确实缺失时才补充（理论上不会触发）
    if "reason" not in lhb_cols:
        conn.execute("ALTER TABLE lhb_data ADD COLUMN reason TEXT")
    # sector_flow_accel 新增成交额均线和占比字段
    sfa_cols = {row[1] for row in conn.execute("PRAGMA table_info(sector_flow_accel)")}
    for col, coldef in [
        ("amount_ma5",       "REAL"),
        ("amount_ma20",      "REAL"),
        ("ma5_slope",        "REAL"),
        ("amount_share_3d",  "REAL"),
        ("amount_share_30d", "REAL"),
    ]:
        if col not in sfa_cols:
            conn.execute(f"ALTER TABLE sector_flow_accel ADD COLUMN {col} {coldef}")
    # lhb_seat 表（本地量化数据营业部席位明细）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lhb_seat (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date   TEXT,
            stock_code   TEXT,
            seat_name    TEXT,
            buy_amount   REAL,
            sell_amount  REAL,
            net_amount   REAL,
            buy_ratio    REAL,
            sell_ratio   REAL,
            seat_type    TEXT,
            reason       TEXT,
            rank         INTEGER,
            UNIQUE(trade_date, stock_code, seat_name)
        )
    """)


def _conn():
    """获取带写锁超时配置的 SQLite 连接（供内部使用）。"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
CREATE TABLE IF NOT EXISTS cls_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT,
    title      TEXT,
    content    TEXT,
    pub_time   TEXT,
    link       TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pub_time, title)
);

CREATE TABLE IF NOT EXISTS policy_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    link       TEXT,
    pub_time   TEXT,
    source     TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(title, source)
);

CREATE TABLE IF NOT EXISTS sector_flow (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time       TEXT,
    source_type      TEXT,
    sector_name      TEXT,
    change_pct       REAL,
    main_inflow      REAL,
    main_inflow_pct  REAL
);

CREATE TABLE IF NOT EXISTS lhb_data (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT,
    stock_code  TEXT,
    stock_name  TEXT,
    reason      TEXT,
    net_buy     REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS lhb_seat (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    stock_code   TEXT,
    seat_name    TEXT,
    buy_amount   REAL,
    sell_amount  REAL,
    net_amount   REAL,
    buy_ratio    REAL,
    sell_ratio   REAL,
    seat_type    TEXT,
    reason       TEXT,
    rank         INTEGER,
    UNIQUE(trade_date, stock_code, seat_name)
);

CREATE TABLE IF NOT EXISTS zt_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    zt_count      INTEGER,
    first_zt_time TEXT,
    sector        TEXT,
    last_zt_time  TEXT,
    seal_amount   REAL,
    zb_count      INTEGER DEFAULT 0,
    turnover_rate REAL,
    circ_mv       REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS dt_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    first_dt_time TEXT,
    sector        TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS dt_pool_v2 (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    first_dt_time TEXT,
    sector        TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS dt_pool_v3 (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    stock_code TEXT,
    stock_name TEXT,
    last_price REAL,
    last_close REAL,
    down_limit REAL,
    sector     TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS quant_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date  TEXT,
    stock_code   TEXT,
    signal_type  TEXT,
    signal_value REAL,
    extra_json   TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_summary (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_time       TEXT,
    content            TEXT,
    data_snapshot_json TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_report (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    stock_code   TEXT,
    stock_name   TEXT,
    org_name     TEXT,
    researcher   TEXT,
    publish_date TEXT,
    rating       TEXT,
    aim_price    TEXT,
    report_url   TEXT UNIQUE,
    qtype        INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_emotion (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date           TEXT UNIQUE,
    zt_total             INTEGER,
    dt_total             INTEGER,
    zb_total             INTEGER,
    max_lianzban         INTEGER,
    zt_yesterday_premium REAL,
    zb_rate              REAL,
    real_zt              INTEGER
);

CREATE TABLE IF NOT EXISTS sector_zt_density (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    industry     TEXT,
    zt_count     INTEGER,
    zt_density   REAL,
    max_lianzban INTEGER,
    UNIQUE(trade_date, industry)
);

CREATE TABLE IF NOT EXISTS volume_breakout (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    stock_code TEXT,
    stock_name TEXT,
    industry   TEXT,
    ratio_5_20 REAL,
    amount_5d  REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS chip_status (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT,
    stock_code     TEXT,
    cost_50        REAL,
    win_rate       REAL,
    overhead_ratio REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS lianzban_chain (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    stock_code   TEXT,
    stock_name   TEXT,
    industry     TEXT,
    lianzban_cnt INTEGER,
    is_zb        BOOLEAN,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS research_activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    stock_code      TEXT,
    stock_name      TEXT,
    org_count_5d    INTEGER,
    last_visit_date TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS sector_flow_accel (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    industry        TEXT,
    inst_inflow_3d  REAL,
    inst_inflow_20d REAL,
    acceleration    REAL,
    amount_ma5      REAL,
    amount_ma20     REAL,
    ma5_slope       REAL,
    amount_share_3d  REAL,
    amount_share_30d REAL,
    UNIQUE(trade_date, industry)
);

CREATE TABLE IF NOT EXISTS lianzban_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT UNIQUE,
    tier_1          INTEGER,
    tier_2          INTEGER,
    tier_3          INTEGER,
    tier_4plus      INTEGER,
    advance_1to2    REAL,
    advance_2to3    REAL,
    advance_3to4    REAL
);

CREATE TABLE IF NOT EXISTS concept_zt_density (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    concept    TEXT,
    zt_count   INTEGER,
    UNIQUE(trade_date, concept)
);

CREATE TABLE IF NOT EXISTS call_auction_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT,
    stock_code     TEXT,
    stock_name     TEXT,
    auction_ratio  REAL,
    auction_amount REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS sector_chip_pressure (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    industry        TEXT,
    stock_count     INTEGER,
    avg_overhead    REAL,
    avg_win_rate    REAL,
    high_overhead_cnt INTEGER,
    UNIQUE(trade_date, industry)
);

CREATE TABLE IF NOT EXISTS sector_auction_sentiment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    industry        TEXT,
    stock_count     INTEGER,
    avg_auction_ratio REAL,
    strong_cnt      INTEGER,
    UNIQUE(trade_date, industry)
);

CREATE TABLE IF NOT EXISTS turnover_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT UNIQUE,
    low_count   INTEGER,
    mid_count   INTEGER,
    high_count  INTEGER,
    median_to   REAL,
    avg_to      REAL
);

CREATE TABLE IF NOT EXISTS market_cap_dist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT UNIQUE,
    small_count  INTEGER,
    mid_count    INTEGER,
    large_count  INTEGER,
    small_pct    REAL,
    mid_pct      REAL,
    large_pct    REAL
);

CREATE TABLE IF NOT EXISTS advance_decline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT UNIQUE,
    advance_count   INTEGER,
    decline_count   INTEGER,
    flat_count      INTEGER,
    ad_ratio        REAL,
    total_amount    REAL,
    amount_ma20     REAL,
    amount_ratio    REAL
);

CREATE TABLE IF NOT EXISTS concept_flow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    concept     TEXT,
    change_pct  REAL,
    net_amount  REAL,
    in_amount   REAL,
    out_amount  REAL,
    lead_stock  TEXT,
    lead_pct    REAL,
    stock_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_concept_flow_time ON concept_flow(fetch_time);

CREATE TABLE IF NOT EXISTS zbgc_pool (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    stock_code      TEXT,
    stock_name      TEXT,
    first_zt_time   TEXT,
    zb_count        INTEGER DEFAULT 0,
    amplitude       REAL,
    sector          TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS strong_pool (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    stock_code      TEXT,
    stock_name      TEXT,
    change_pct      REAL,
    is_new_high     TEXT,
    volume_ratio    REAL,
    reason          TEXT,
    sector          TEXT,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS hot_rank_up (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time   TEXT,
    rank_change  INTEGER,
    current_rank INTEGER,
    stock_code   TEXT,
    stock_name   TEXT,
    price        REAL,
    change_pct   REAL
);
CREATE INDEX IF NOT EXISTS idx_hot_rank_up_time ON hot_rank_up(fetch_time);

CREATE TABLE IF NOT EXISTS northbound_flow (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time   TEXT,
    trade_date   TEXT,
    channel      TEXT,
    direction    TEXT,
    net_buy      REAL,
    net_inflow   REAL,
    UNIQUE(fetch_time, channel)
);

CREATE TABLE IF NOT EXISTS xq_hot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    rank        INTEGER,
    stock_code  TEXT,
    stock_name  TEXT,
    follow_cnt  REAL,
    price       REAL
);
CREATE INDEX IF NOT EXISTS idx_xq_hot_time ON xq_hot(fetch_time);

CREATE TABLE IF NOT EXISTS big_deal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    deal_time   TEXT,
    stock_code  TEXT,
    stock_name  TEXT,
    price       REAL,
    volume      INTEGER,
    amount      REAL,
    deal_type   TEXT,
    change_pct  REAL,
    change_amt  REAL,
    UNIQUE(deal_time, stock_code, amount)
);

CREATE TABLE IF NOT EXISTS margin (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time TEXT, trade_date TEXT,
    stock_code TEXT, stock_name TEXT,
    rzye REAL, rzmre REAL, rzche REAL,
    rqye REAL, rqmcl REAL, rzrqye REAL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS block_trade (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT, stock_code TEXT, stock_name TEXT,
    deal_price  REAL, close_price REAL,
    deal_volume INTEGER, deal_amt REAL,
    buyer_name  TEXT, seller_name TEXT,
    UNIQUE(trade_date, stock_code, deal_amt)
);

CREATE TABLE IF NOT EXISTS holder_count (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    end_date         TEXT, stock_code TEXT, stock_name TEXT,
    holder_num       INTEGER, holder_num_change REAL,
    holder_num_ratio REAL, avg_free_shares REAL,
    UNIQUE(end_date, stock_code)
);

CREATE TABLE IF NOT EXISTS fundamentals_finance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date  TEXT, stock_code TEXT,
    data_json   TEXT,
    UNIQUE(fetch_date, stock_code)
);

CREATE TABLE IF NOT EXISTS fundamentals_f10 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date  TEXT, stock_code TEXT, category TEXT,
    content     TEXT,
    UNIQUE(fetch_date, stock_code, category)
);

CREATE TABLE IF NOT EXISTS lockup_expiry (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    free_date        TEXT,
    stock_code       TEXT,
    stock_name       TEXT,
    lift_shares      REAL,
    lift_market_cap  REAL,
    lift_ratio       REAL,
    hold_num         INTEGER,
    lift_type        TEXT,
    UNIQUE(free_date, stock_code, lift_type)
);

CREATE TABLE IF NOT EXISTS dividend (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ex_dividend_date  TEXT,
    stock_code        TEXT,
    stock_name        TEXT,
    pretax_bonus_rmb  REAL,
    transfer_ratio    REAL,
    bonus_ratio       REAL,
    assign_progress   TEXT,
    UNIQUE(ex_dividend_date, stock_code)
);

CREATE TABLE IF NOT EXISTS industry_ranking (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time   TEXT,
    sector_code  TEXT,
    sector_name  TEXT,
    change_pct   REAL,
    price        REAL,
    up_count     INTEGER,
    down_count   INTEGER,
    lead_stock   TEXT,
    lead_pct     REAL
);
CREATE INDEX IF NOT EXISTS idx_industry_ranking_time ON industry_ranking(fetch_time);

CREATE TABLE IF NOT EXISTS ths_hot_stocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    stock_code  TEXT,
    stock_name  TEXT,
    reason      TEXT,
    industry    TEXT,
    change_pct  REAL
);
CREATE INDEX IF NOT EXISTS idx_ths_hot_time ON ths_hot_stocks(fetch_time);

CREATE TABLE IF NOT EXISTS market_breadth (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time   TEXT NOT NULL,
    source       TEXT,
    market       TEXT,
    up_count     INTEGER,
    down_count   INTEGER,
    flat_count   INTEGER,
    ad_ratio     REAL,
    index_amount REAL,
    index_price  REAL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_breadth_time ON market_breadth(fetch_time);

CREATE TABLE IF NOT EXISTS fundamental_coverage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at   TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    project_ids    TEXT,
    coverage_json  TEXT,
    report_html    TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL UNIQUE,
    payload     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pool       TEXT NOT NULL DEFAULT '默认',
    code       TEXT NOT NULL,
    name       TEXT,
    note       TEXT DEFAULT '',
    added_at   TEXT,
    sort       INTEGER DEFAULT 0,
    UNIQUE(pool, code)
);

CREATE TABLE IF NOT EXISTS watchlist_pools (
    name       TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
        """)
        # watchlist 老表迁移：无 pool 列时重建为 (pool, code) 唯一的新表，原数据归入'默认'池
        wl_cols = [r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()]
        if wl_cols and "pool" not in wl_cols:
            conn.executescript("""
CREATE TABLE watchlist_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pool       TEXT NOT NULL DEFAULT '默认',
    code       TEXT NOT NULL,
    name       TEXT,
    note       TEXT DEFAULT '',
    added_at   TEXT,
    sort       INTEGER DEFAULT 0,
    UNIQUE(pool, code)
);
INSERT INTO watchlist_new (pool, code, name, note, added_at, sort)
    SELECT '默认', code, name, note, added_at, sort FROM watchlist;
DROP TABLE watchlist;
ALTER TABLE watchlist_new RENAME TO watchlist;
            """)
        # '默认'池必存在；watchlist 中出现的其他池名也补进 pools 表
        conn.execute("INSERT OR IGNORE INTO watchlist_pools (name) VALUES ('默认')")
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_pools (name) SELECT DISTINCT pool FROM watchlist"
        )
        # 增量迁移：为旧版 DB 补充新增列（列已存在时忽略）
        _migrations = [
            "ALTER TABLE market_emotion ADD COLUMN real_zt INTEGER",
            "ALTER TABLE market_breadth ADD COLUMN total_amount REAL",
        ]
        for sql in _migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass  # 列已存在


def _rows_to_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _latest_trade_date(conn, table: str) -> str:
    row = conn.execute(f"SELECT trade_date FROM {table} ORDER BY trade_date DESC LIMIT 1").fetchone()
    return row[0] if row else _today()


# ── Insert ────────────────────────────────────────────────────────────────────

def insert_cls_news(title, content, pub_time, source="财联社", link="") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cls_news (source, title, content, pub_time, link) VALUES (?, ?, ?, ?, ?)",
            (source, title, content, pub_time, link),
        )


def insert_policy_news(title, link, pub_time, source) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO policy_news (title, link, pub_time, source) VALUES (?, ?, ?, ?)",
            (title, link, pub_time, source),
        )


def insert_sector_flow(fetch_time, sector_name, change_pct, main_inflow, main_inflow_pct, source_type="industry") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sector_flow (fetch_time, source_type, sector_name, change_pct, main_inflow, main_inflow_pct) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fetch_time, source_type, sector_name, change_pct, main_inflow, main_inflow_pct),
        )


def insert_lhb_data(trade_date, stock_code, stock_name, reason, net_buy,
                    change_pct=None, interpret="", net_buy_ratio=None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lhb_data "
            "(trade_date, stock_code, stock_name, reason, net_buy, change_pct, interpret, net_buy_ratio) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, reason, net_buy, change_pct, interpret, net_buy_ratio),
        )


def insert_lhb_seat(trade_date, stock_code, seat_name, buy_amount, sell_amount,
                    net_amount, buy_ratio, sell_ratio, seat_type, reason, rank) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lhb_seat "
            "(trade_date,stock_code,seat_name,buy_amount,sell_amount,net_amount,"
            " buy_ratio,sell_ratio,seat_type,reason,rank) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, seat_name, buy_amount, sell_amount,
             net_amount, buy_ratio, sell_ratio, seat_type, reason, rank),
        )


def get_lhb_seat(trade_date=None, stock_code=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "lhb_seat")
        if not date:
            return []
        if stock_code:
            cur = conn.execute(
                "SELECT * FROM lhb_seat WHERE trade_date=? AND stock_code=? ORDER BY net_amount DESC",
                (date, stock_code),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM lhb_seat WHERE trade_date=? ORDER BY net_amount DESC",
                (date,),
            )
        return _rows_to_dicts(cur)


def insert_zt_pool(trade_date, stock_code, stock_name, zt_count, first_zt_time, sector,
                   last_zt_time="", seal_amount=None, zb_count=0,
                   turnover_rate=None, circ_mv=None) -> None:
    # 使用 INSERT OR REPLACE 而非 IGNORE，以便重复拉取时更新新增字段值
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO zt_pool "
            "(trade_date, stock_code, stock_name, zt_count, first_zt_time, sector, "
            " last_zt_time, seal_amount, zb_count, turnover_rate, circ_mv) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, zt_count, first_zt_time, sector,
             last_zt_time, seal_amount, zb_count, turnover_rate, circ_mv),
        )


def clear_zt_pool(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM zt_pool WHERE trade_date = ?", (trade_date,))


def insert_dt_pool(trade_date, stock_code, stock_name, first_dt_time, sector) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO dt_pool "
            "(trade_date, stock_code, stock_name, first_dt_time, sector) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, first_dt_time, sector),
        )


def clear_dt_pool(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM dt_pool WHERE trade_date = ?", (trade_date,))


def insert_dt_pool_v2(trade_date, stock_code, stock_name, first_dt_time, sector) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dt_pool_v2 "
            "(trade_date, stock_code, stock_name, first_dt_time, sector) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, first_dt_time, sector),
        )


def clear_dt_pool_v2(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM dt_pool_v2 WHERE trade_date = ?", (trade_date,))


def insert_dt_pool_v3(trade_date, stock_code, stock_name, last_price, last_close, down_limit, sector) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dt_pool_v3 "
            "(trade_date, stock_code, stock_name, last_price, last_close, down_limit, sector) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, last_price, last_close, down_limit, sector),
        )


def clear_dt_pool_v3(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM dt_pool_v3 WHERE trade_date = ?", (trade_date,))


def replace_dt_pool_v3(trade_date: str, rows: list[dict]) -> None:
    with _write_lock:
        with _conn() as conn:
            conn.execute("DELETE FROM dt_pool_v3 WHERE trade_date = ?", (trade_date,))
            if not rows:
                # 写一行 sentinel 标识今天 scheduler 扫过 (0 candidates)
                # 防止 get_dt_pool_v3(None) fallback 到昨天数据
                conn.execute(
                    "INSERT OR IGNORE INTO dt_pool_v3 "
                    "(trade_date, stock_code, stock_name, last_price, last_close, down_limit, sector) "
                    "VALUES (?, '__SCANNED__', '', 0, 0, 0, '')",
                    (trade_date,),
                )
                return
            conn.executemany(
                "INSERT OR REPLACE INTO dt_pool_v3 "
                "(trade_date, stock_code, stock_name, last_price, last_close, down_limit, sector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        trade_date,
                        str(row.get("stock_code") or "").strip(),
                        str(row.get("stock_name") or "").strip(),
                        row.get("last_price"),
                        row.get("last_close"),
                        row.get("down_limit"),
                        str(row.get("sector") or "").strip(),
                    )
                    for row in rows
                ],
            )


def insert_quant_signal(signal_date, stock_code, signal_type, signal_value, extra_json="") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO quant_signals (signal_date, stock_code, signal_type, signal_value, extra_json) "
            "VALUES (?, ?, ?, ?, ?)",
        )


def insert_agent_summary(content, data_snapshot_json, run_type: str = "", report_html: str = "") -> int:
    summary_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        # 确保列存在（兼容旧 DB）
        for col_def in [
            "ALTER TABLE agent_summary ADD COLUMN run_type TEXT DEFAULT ''",
            "ALTER TABLE agent_summary ADD COLUMN report_html TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(col_def)
            except Exception:
                pass
        cur = conn.execute(
            "INSERT INTO agent_summary (summary_time, run_type, content, data_snapshot_json, report_html) VALUES (?, ?, ?, ?, ?)",
            (summary_time, run_type, content, data_snapshot_json, report_html),
        )
        return cur.lastrowid



def insert_research_report(title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype=0) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_report "
            "(title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype),
        )


# ── Read ──────────────────────────────────────────────────────────────────────

def get_cls_news(limit=50) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM cls_news ORDER BY pub_time DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


def get_cls_news_by_source(source: str, limit: int = 50, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM cls_news WHERE source = ? ORDER BY pub_time DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
        return _rows_to_dicts(cur)


def count_cls_news_by_source(source: str) -> int:
    with _conn() as conn:
        return conn.execute(
            "SELECT count(*) FROM cls_news WHERE source = ?", (source,)
        ).fetchone()[0]


def get_policy_news(limit=20, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news ORDER BY pub_time DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return _rows_to_dicts(cur)


def get_policy_news_by_source(source: str, limit: int = 50, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news WHERE source = ? ORDER BY pub_time DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
        return _rows_to_dicts(cur)


def count_policy_news_by_source(source: str) -> int:
    with _conn() as conn:
        return conn.execute(
            "SELECT count(*) FROM policy_news WHERE source = ?", (source,)
        ).fetchone()[0]


def get_sector_flow_latest(source_type="industry") -> list[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetch_time FROM sector_flow WHERE source_type = ? ORDER BY fetch_time DESC LIMIT 1",
            (source_type,),
        ).fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM sector_flow WHERE fetch_time = ? AND source_type = ? ORDER BY main_inflow DESC",
            (row[0], source_type),
        )
        return _rows_to_dicts(cur)


def get_lhb_data(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "lhb_data")
        cur = conn.execute(
            "SELECT * FROM lhb_data WHERE trade_date = ?", (date,)
        )
        return _rows_to_dicts(cur)


def get_zt_pool(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "zt_pool")
        cur = conn.execute(
            "SELECT * FROM zt_pool WHERE trade_date = ? ORDER BY zt_count DESC",
            (date,),
        )
        return _rows_to_dicts(cur)


def get_dt_pool(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "dt_pool")
        cur = conn.execute(
            "SELECT * FROM dt_pool WHERE trade_date = ?", (date,)
        )
        return _rows_to_dicts(cur)


def get_dt_pool_v2(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "dt_pool_v2")
        cur = conn.execute(
            "SELECT * FROM dt_pool_v2 WHERE trade_date = ?", (date,)
        )
        return _rows_to_dicts(cur)


def get_dt_pool_v3(trade_date=None) -> list[dict]:
    with _conn() as conn:
        if trade_date:
            date = trade_date
        else:
            # None → 优先 today (今天 scheduler 至少写一行 __SCANNED__ sentinel 证明扫过)
            # today 没任何 row → fallback 最新非空 trade_date (scheduler 启动延迟窗口)
            today = _today()
            any_row = conn.execute(
                "SELECT 1 FROM dt_pool_v3 WHERE trade_date = ? LIMIT 1", (today,)
            ).fetchone()
            if any_row is not None:
                date = today
            else:
                date = _latest_trade_date(conn, "dt_pool_v3")
        # 统一过滤 sentinel — 0 candidates 时的占位行, 不算真跌停
        cur = conn.execute(
            "SELECT * FROM dt_pool_v3 WHERE trade_date = ? AND stock_code != '__SCANNED__'",
            (date,),
        )
        return _rows_to_dicts(cur)


def get_quant_signals(signal_date=None) -> list[dict]:
    signal_date = signal_date or _today()
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM quant_signals WHERE signal_date = ? ORDER BY signal_type",
            (signal_date,),
        )
        return _rows_to_dicts(cur)


def get_agent_summary_latest() -> dict | None:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM agent_summary ORDER BY created_at DESC LIMIT 1"
        )
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else None


def get_agent_summary_by_id(row_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM agent_summary WHERE id = ?", (row_id,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else None


def get_agent_summary_history(limit: int = 20, today_only: bool = False, run_type: str = "") -> list[dict]:
    today = _today()
    with _conn() as conn:
        where_parts = []
        params = []
        if today_only:
            where_parts.append("summary_time >= ?")
            params.append(today)
        if run_type:
            where_parts.append("run_type = ?")
            params.append(run_type)
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        params.append(limit)
        cur = conn.execute(
            f"SELECT id, summary_time, run_type, content, "
            f"CASE WHEN report_html IS NOT NULL AND report_html != '' THEN 1 ELSE 0 END AS has_html "
            f"FROM agent_summary {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return _rows_to_dicts(cur)


def get_research_reports(qtype: int = None, limit: int = 50, today_only: bool = False) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as conn:
        conditions = []
        params: list = []
        if qtype is not None:
            conditions.append("qtype = ?")
            params.append(qtype)
        if today_only:
            conditions.append("publish_date = ?")
            params.append(today)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        cur = conn.execute(
            f"SELECT * FROM research_report {where} ORDER BY publish_date DESC, id DESC LIMIT ?",
            params,
        )
        return _rows_to_dicts(cur)


# ── Agent context ─────────────────────────────────────────────────────────────

def get_agent_context() -> dict:
    now = datetime.now()
    two_hours_ago = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    today = _today()

    with _conn() as conn:
        cur = conn.execute(
            "SELECT title, content, pub_time FROM cls_news "
            "WHERE pub_time >= ? ORDER BY pub_time DESC",
            (two_hours_ago,),
        )
        recent_cls = _rows_to_dicts(cur)

        cur = conn.execute(
            "SELECT title, pub_time, source FROM policy_news "
            "WHERE pub_time >= ? ORDER BY pub_time DESC",
            (today,),
        )
        policy_titles = _rows_to_dicts(cur)

        row = conn.execute(
            "SELECT fetch_time FROM sector_flow WHERE source_type = 'industry' ORDER BY fetch_time DESC LIMIT 1"
        ).fetchone()
        sector_top10 = []
        if row:
            cur = conn.execute(
                "SELECT sector_name, change_pct, main_inflow, main_inflow_pct "
                "FROM sector_flow WHERE fetch_time = ? AND source_type = 'industry' ORDER BY main_inflow DESC LIMIT 10",
                (row[0],),
            )
            sector_top10 = _rows_to_dicts(cur)

        cur = conn.execute(
            "SELECT stock_code, stock_name, zt_count, first_zt_time, sector, "
            "       last_zt_time, seal_amount, zb_count, turnover_rate, circ_mv "
            "FROM zt_pool WHERE trade_date = ? ORDER BY zt_count DESC",
            (today,),
        )
        zt_today = _rows_to_dicts(cur)

        # market_emotion 今日记录
        cur = conn.execute(
            "SELECT * FROM market_emotion WHERE trade_date = ?", (today,)
        )
        rows = _rows_to_dicts(cur)
        market_emotion_today = rows[0] if rows else None

        # sector_zt_density 今日 top10
        cur = conn.execute(
            "SELECT * FROM sector_zt_density WHERE trade_date = ? ORDER BY zt_density DESC LIMIT 10",
            (today,),
        )
        sector_zt_top10 = _rows_to_dicts(cur)

        # sector_flow_accel 今日
        cur = conn.execute(
            "SELECT * FROM sector_flow_accel WHERE trade_date = ? ORDER BY acceleration DESC LIMIT 10",
            (today,),
        )
        sector_flow_accel_today = _rows_to_dicts(cur)

        # volume_breakout 今日
        cur = conn.execute(
            "SELECT * FROM volume_breakout WHERE trade_date = ? ORDER BY ratio_5_20 DESC LIMIT 30",
            (today,),
        )
        volume_breakout_today = _rows_to_dicts(cur)

        # lianzban_chain 今日（2板+）
        cur = conn.execute(
            "SELECT * FROM lianzban_chain WHERE trade_date = ? AND lianzban_cnt >= 2 "
            "ORDER BY lianzban_cnt DESC",
            (today,),
        )
        lianzban_chain_today = _rows_to_dicts(cur)

        # lhb_data 今日（盘后才有，可能为空）
        cur = conn.execute(
            "SELECT stock_code, stock_name, reason, net_buy, net_buy_ratio "
            "FROM lhb_data WHERE trade_date = ? ORDER BY net_buy DESC",
            (today,),
        )
        lhb_today = _rows_to_dicts(cur)

        # research_report 近3日评级上调
        three_days_ago = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        cur = conn.execute(
            "SELECT title, stock_code, stock_name, org_name, rating, publish_date "
            "FROM research_report WHERE publish_date >= ? AND rating IS NOT NULL "
            "ORDER BY publish_date DESC LIMIT 30",
            (three_days_ago,),
        )
        recent_research = _rows_to_dicts(cur)

    return {
        "recent_cls_news": recent_cls,
        "policy_news_today": policy_titles,
        "sector_flow_top10": sector_top10,
        "zt_pool_today": zt_today,
        "market_emotion_today": market_emotion_today,
        "sector_zt_density_top10": sector_zt_top10,
        "sector_flow_accel_today": sector_flow_accel_today,
        "volume_breakout_today": volume_breakout_today,
        "lianzban_chain_today": lianzban_chain_today,
        "lhb_today": lhb_today,
        "recent_research": recent_research,
    }


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup_old_data() -> None:
    now = datetime.now()
    cutoff_7d  = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_60d = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_90d  = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_365d = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    with _conn() as conn:
        # 7 days
        conn.execute("DELETE FROM cls_news WHERE created_at < ?", (cutoff_7d,))
        # 30 days
        conn.execute("DELETE FROM sector_flow WHERE fetch_time < ?", (cutoff_30d,))
        # 60 days
        conn.execute("DELETE FROM agent_summary WHERE created_at < ?", (cutoff_60d,))
        conn.execute("DELETE FROM fundamental_coverage WHERE created_at < ?", (cutoff_60d,))
        # 365 days (长期历史日线，保留一年)
        conn.execute("DELETE FROM market_emotion WHERE trade_date < ?", (cutoff_365d,))
        conn.execute("DELETE FROM advance_decline WHERE trade_date < ?", (cutoff_365d,))
        conn.execute("DELETE FROM turnover_stats WHERE trade_date < ?", (cutoff_365d,))
        conn.execute("DELETE FROM market_cap_dist WHERE trade_date < ?", (cutoff_365d,))
        # 90 days (trade_date columns, stored as TEXT 'YYYY-MM-DD')
        conn.execute("DELETE FROM sector_zt_density WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM volume_breakout WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM chip_status WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM sector_chip_pressure WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM sector_auction_sentiment WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM lianzban_chain WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM research_activity WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM sector_flow_accel WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM lianzban_stats WHERE trade_date < ?", (cutoff_90d,))
        cutoff_30d_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        conn.execute("DELETE FROM concept_zt_density WHERE trade_date < ?", (cutoff_30d_date,))
        conn.execute("DELETE FROM call_auction_stats WHERE trade_date < ?", (cutoff_30d_date,))
        conn.execute("DELETE FROM concept_flow WHERE fetch_time < ?", (cutoff_30d,))
        cutoff_7d_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        conn.execute("DELETE FROM zbgc_pool WHERE trade_date < ?", (cutoff_7d_date,))
        conn.execute("DELETE FROM strong_pool WHERE trade_date < ?", (cutoff_7d_date,))
        conn.execute("DELETE FROM hot_rank_up WHERE fetch_time < ?", (cutoff_7d,))
        conn.execute("DELETE FROM northbound_flow WHERE fetch_time < ?", (cutoff_30d,))
        conn.execute("DELETE FROM xq_hot WHERE fetch_time < ?", (cutoff_7d,))
        conn.execute("DELETE FROM big_deal WHERE fetch_time < ?", (cutoff_7d,))
        # margin / block_trade / holder_count: 保留 30 天
        conn.execute("DELETE FROM margin WHERE trade_date < ?", (cutoff_30d_date,))
        conn.execute("DELETE FROM block_trade WHERE trade_date < ?", (cutoff_30d_date,))
        conn.execute("DELETE FROM holder_count WHERE end_date < ?", (cutoff_30d_date,))
        # lockup_expiry / dividend: 保留 90 天；industry_ranking: 保留 30 天；ths_hot_stocks: 保留 7 天
        conn.execute("DELETE FROM lockup_expiry WHERE free_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM dividend WHERE ex_dividend_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM industry_ranking WHERE fetch_time < ?", (cutoff_30d,))
        conn.execute("DELETE FROM ths_hot_stocks WHERE fetch_time < ?", (cutoff_7d,))
        # 之前遗漏的表，补齐 90 天清理
        conn.execute("DELETE FROM lhb_data WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM lhb_seat WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM zt_pool WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM dt_pool WHERE trade_date < ?", (cutoff_90d,))
        conn.execute("DELETE FROM policy_news WHERE created_at < ?", (cutoff_30d,))
        conn.execute("DELETE FROM research_report WHERE created_at < ?", (cutoff_90d,))
        conn.execute("DELETE FROM quant_signals WHERE created_at < ?", (cutoff_90d,))


# ── market_emotion ────────────────────────────────────────────────────────────

def upsert_market_emotion(trade_date: str, zt_total: int, dt_total: int, zb_total: int,
                          max_lianzban: int, zt_yesterday_premium: float, zb_rate: float,
                          real_zt: int = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_emotion "
            "(trade_date, zt_total, dt_total, zb_total, max_lianzban, zt_yesterday_premium, zb_rate, real_zt) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, zt_total, dt_total, zb_total, max_lianzban, zt_yesterday_premium, zb_rate, real_zt),
        )


def get_market_emotion(days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM market_emotion WHERE trade_date >= ? ORDER BY trade_date DESC",
            (cutoff,),
        )
        return _rows_to_dicts(cur)


# ── sector_zt_density ─────────────────────────────────────────────────────────

def insert_sector_zt_density(trade_date: str, industry: str, zt_count: int,
                              zt_density: float, max_lianzban: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sector_zt_density "
            "(trade_date, industry, zt_count, zt_density, max_lianzban) VALUES (?,?,?,?,?)",
            (trade_date, industry, zt_count, zt_density, max_lianzban),
        )


def get_sector_zt_density(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM sector_zt_density WHERE trade_date = ? ORDER BY zt_density DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── volume_breakout ───────────────────────────────────────────────────────────

def insert_volume_breakout(trade_date: str, stock_code: str, stock_name: str,
                            industry: str, ratio_5_20: float, amount_5d: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO volume_breakout "
            "(trade_date, stock_code, stock_name, industry, ratio_5_20, amount_5d) VALUES (?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, industry, ratio_5_20, amount_5d),
        )


def get_volume_breakout(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM volume_breakout WHERE trade_date = ? ORDER BY ratio_5_20 DESC LIMIT 50",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── chip_status ───────────────────────────────────────────────────────────────

def insert_chip_status(trade_date: str, stock_code: str, cost_50: float,
                        win_rate: float, overhead_ratio: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chip_status "
            "(trade_date, stock_code, cost_50, win_rate, overhead_ratio) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, cost_50, win_rate, overhead_ratio),
        )


def get_chip_status(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM chip_status WHERE trade_date = ? ORDER BY win_rate DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── lianzban_chain ────────────────────────────────────────────────────────────

def insert_lianzban_chain(trade_date: str, stock_code: str, stock_name: str,
                           industry: str, lianzban_cnt: int, is_zb: bool) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lianzban_chain "
            "(trade_date, stock_code, stock_name, industry, lianzban_cnt, is_zb) VALUES (?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, industry, lianzban_cnt, is_zb),
        )


def get_lianzban_chain(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM lianzban_chain WHERE trade_date = ? AND lianzban_cnt <= 30 ORDER BY lianzban_cnt DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── research_activity ─────────────────────────────────────────────────────────

def insert_research_activity(trade_date: str, stock_code: str, stock_name: str,
                               org_count_5d: int, last_visit_date: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_activity "
            "(trade_date, stock_code, stock_name, org_count_5d, last_visit_date) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, stock_name, org_count_5d, last_visit_date),
        )


def get_research_activity(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM research_activity WHERE trade_date = ? ORDER BY org_count_5d DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── sector_flow_accel ─────────────────────────────────────────────────────────

def insert_sector_flow_accel(trade_date: str, industry: str, inst_inflow_3d: float,
                              inst_inflow_20d: float, acceleration: float,
                              amount_ma5: float = None, amount_ma20: float = None,
                              ma5_slope: float = None,
                              amount_share_3d: float = None,
                              amount_share_30d: float = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_flow_accel "
            "(trade_date, industry, inst_inflow_3d, inst_inflow_20d, acceleration, "
            "amount_ma5, amount_ma20, ma5_slope, amount_share_3d, amount_share_30d) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (trade_date, industry, inst_inflow_3d, inst_inflow_20d, acceleration,
             amount_ma5, amount_ma20, ma5_slope, amount_share_3d, amount_share_30d),
        )


def get_sector_flow_accel(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM sector_flow_accel WHERE trade_date = ? ORDER BY acceleration DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── lianzban_stats ────────────────────────────────────────────────────────────

def upsert_lianzban_stats(trade_date: str, tier_1: int, tier_2: int, tier_3: int,
                           tier_4plus: int, advance_1to2: float, advance_2to3: float,
                           advance_3to4: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lianzban_stats "
            "(trade_date, tier_1, tier_2, tier_3, tier_4plus, advance_1to2, advance_2to3, advance_3to4) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, tier_1, tier_2, tier_3, tier_4plus, advance_1to2, advance_2to3, advance_3to4),
        )


def get_lianzban_stats(days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM lianzban_stats WHERE trade_date >= ? ORDER BY trade_date ASC",
            (cutoff,),
        )
        return _rows_to_dicts(cur)


# ── concept_zt_density ────────────────────────────────────────────────────────

def insert_concept_zt_density(trade_date: str, concept: str, zt_count: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO concept_zt_density (trade_date, concept, zt_count) VALUES (?,?,?)",
            (trade_date, concept, zt_count),
        )


def get_concept_zt_density(trade_date: str, top_n: int = 15) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM concept_zt_density WHERE trade_date = ? ORDER BY zt_count DESC LIMIT ?",
            (trade_date, top_n),
        )
        return _rows_to_dicts(cur)


# ── call_auction_stats ────────────────────────────────────────────────────────

def insert_call_auction_stats(trade_date: str, stock_code: str, stock_name: str,
                               auction_ratio: float, auction_amount: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO call_auction_stats "
            "(trade_date, stock_code, stock_name, auction_ratio, auction_amount) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, stock_name, auction_ratio, auction_amount),
        )


def get_call_auction_stats(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM call_auction_stats WHERE trade_date = ? ORDER BY auction_ratio DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── market_emotion_summary ────────────────────────────────────────────────────

def get_market_emotion_summary(trade_date: str = None) -> dict:
    """
    返回指定日期的市场情绪综合摘要，供 UI ticker-bar 和情绪面板使用。
    包含：market_emotion + lianzban_stats（当日）的合并字典。
    trade_date 为 None 时取最新一条。
    """
    with _conn() as conn:
        if trade_date is None:
            row = conn.execute(
                "SELECT * FROM market_emotion ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM market_emotion WHERE trade_date = ?", (trade_date,)
            ).fetchone()
        if not row:
            return {}
        cols = [d[0] for d in conn.execute("SELECT * FROM market_emotion LIMIT 0").description]
        result = dict(zip(cols, row))

        ls = conn.execute(
            "SELECT * FROM lianzban_stats WHERE trade_date = ?", (result["trade_date"],)
        ).fetchone()
        if ls:
            ls_cols = [d[0] for d in conn.execute("SELECT * FROM lianzban_stats LIMIT 0").description]
            result.update(dict(zip(ls_cols, ls)))

        return result


def get_latest_emotion_date() -> str:
    """返回 market_emotion 表中最新的 trade_date，没有数据时返回空字符串"""
    with _conn() as conn:
        row = conn.execute("SELECT trade_date FROM market_emotion ORDER BY trade_date DESC LIMIT 1").fetchone()
        return row[0] if row else ""


# ── review_daily ──────────────────────────────────────────────────────────────

def insert_review_daily(trade_date: str, payload_json: str) -> None:
    with _write_lock:
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO review_daily (trade_date, payload) VALUES (?, ?)",
                (trade_date, payload_json),
            )


def get_review_daily(trade_date: str | None = None) -> dict | None:
    with _conn() as conn:
        if trade_date:
            row = conn.execute(
                "SELECT * FROM review_daily WHERE trade_date = ?", (trade_date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM review_daily ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {"trade_date": row[1], "payload": row[2], "created_at": row[3]}


def get_review_dates() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT trade_date FROM review_daily ORDER BY trade_date DESC LIMIT 60"
        ).fetchall()
        return [r[0] for r in rows]


# ── review_v2_daily (9 维度复盘, 跟 review_daily 独立, 不冲突旧 ReviewPage) ─────

def _ensure_review_v2_table() -> None:
    """review_v2_daily 表 (9 维度编排, 跟 review_daily 独立)"""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_v2_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                report_html TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def insert_review_v2_daily(trade_date: str, payload_json: str, report_html: str = "") -> int:
    _ensure_review_v2_table()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR REPLACE INTO review_v2_daily (trade_date, payload, report_html) VALUES (?, ?, ?)",
            (trade_date, payload_json, report_html),
        )
        return cur.lastrowid


def get_review_v2_daily(trade_date: str | None = None) -> dict | None:
    _ensure_review_v2_table()
    with _conn() as conn:
        if trade_date:
            row = conn.execute(
                "SELECT id, trade_date, payload, report_html, created_at FROM review_v2_daily WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, trade_date, payload, report_html, created_at FROM review_v2_daily ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "trade_date": row[1], "payload": row[2], "report_html": row[3], "created_at": row[4]}


def get_review_v2_dates() -> list[str]:
    _ensure_review_v2_table()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT trade_date FROM review_v2_daily ORDER BY trade_date DESC LIMIT 60"
        ).fetchall()
        return [r[0] for r in rows]


# ── turnover_stats ────────────────────────────────────────────────────────────

def upsert_turnover_stats(trade_date: str, low_count: int, mid_count: int,
                           high_count: int, median_to: float, avg_to: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO turnover_stats "
            "(trade_date, low_count, mid_count, high_count, median_to, avg_to) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, low_count, mid_count, high_count, median_to, avg_to),
        )


def get_turnover_stats(trade_date: str) -> dict:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM turnover_stats WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── market_cap_dist ───────────────────────────────────────────────────────────

def upsert_market_cap_dist(trade_date: str, small_count: int, mid_count: int,
                            large_count: int, small_pct: float, mid_pct: float,
                            large_pct: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_cap_dist "
            "(trade_date, small_count, mid_count, large_count, small_pct, mid_pct, large_pct) "
            "VALUES (?,?,?,?,?,?,?)",
            (trade_date, small_count, mid_count, large_count, small_pct, mid_pct, large_pct),
        )


def get_market_cap_dist(trade_date: str) -> dict:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM market_cap_dist WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── advance_decline ───────────────────────────────────────────────────────────

def upsert_advance_decline(trade_date: str, advance_count: int, decline_count: int,
                            flat_count: int, ad_ratio: float, total_amount: float,
                            amount_ma20: float, amount_ratio: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO advance_decline "
            "(trade_date, advance_count, decline_count, flat_count, ad_ratio, "
            " total_amount, amount_ma20, amount_ratio) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, advance_count, decline_count, flat_count, ad_ratio,
             total_amount, amount_ma20, amount_ratio),
        )


def get_advance_decline(trade_date: str) -> dict:
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM advance_decline WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── concept_flow ──────────────────────────────────────────────────────────────

def insert_concept_flow(fetch_time, concept, change_pct, net_amount,
                        in_amount, out_amount, lead_stock, lead_pct, stock_count) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO concept_flow (fetch_time, concept, change_pct, net_amount, "
            "in_amount, out_amount, lead_stock, lead_pct, stock_count) VALUES (?,?,?,?,?,?,?,?,?)",
            (fetch_time, concept, change_pct, net_amount,
             in_amount, out_amount, lead_stock, lead_pct, stock_count),
        )


def get_concept_flow_latest(top_n=30) -> list[dict]:
    """返回最新一批概念资金流，按 net_amount 降序取 top_n"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetch_time FROM concept_flow ORDER BY fetch_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM concept_flow WHERE fetch_time = ? ORDER BY net_amount DESC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── zbgc_pool ─────────────────────────────────────────────────────────────────

def insert_zbgc_pool(trade_date: str, stock_code: str, stock_name: str,
                     first_zt_time: str, zb_count: int, amplitude, sector: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO zbgc_pool "
            "(trade_date, stock_code, stock_name, first_zt_time, zb_count, amplitude, sector) "
            "VALUES (?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, first_zt_time, zb_count, amplitude, sector),
        )


def clear_zbgc_pool(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM zbgc_pool WHERE trade_date = ?", (trade_date,))


def get_zbgc_pool(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "zbgc_pool")
        cur = conn.execute(
            "SELECT * FROM zbgc_pool WHERE trade_date = ? ORDER BY zb_count DESC",
            (date,),
        )
        return _rows_to_dicts(cur)


# ── strong_pool ───────────────────────────────────────────────────────────────

def insert_strong_pool(trade_date: str, stock_code: str, stock_name: str,
                       change_pct, is_new_high: str, volume_ratio,
                       reason: str, sector: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strong_pool "
            "(trade_date, stock_code, stock_name, change_pct, is_new_high, volume_ratio, reason, sector) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, change_pct, is_new_high, volume_ratio, reason, sector),
        )


def clear_strong_pool(trade_date: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM strong_pool WHERE trade_date = ?", (trade_date,))


def get_strong_pool(trade_date=None) -> list[dict]:
    with _conn() as conn:
        date = trade_date or _latest_trade_date(conn, "strong_pool")
        cur = conn.execute(
            "SELECT * FROM strong_pool WHERE trade_date = ? ORDER BY change_pct DESC",
            (date,),
        )
        return _rows_to_dicts(cur)


# ── hot_rank_up ───────────────────────────────────────────────────────────────

def insert_hot_rank_up(fetch_time, rank_change, current_rank, stock_code, stock_name, price, change_pct):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO hot_rank_up (fetch_time,rank_change,current_rank,stock_code,stock_name,price,change_pct) VALUES (?,?,?,?,?,?,?)",
            (fetch_time, rank_change, current_rank, stock_code, stock_name, price, change_pct),
        )


def get_hot_rank_up_latest(top_n=20) -> list[dict]:
    """返回最新一批，按 rank_change 降序"""
    with _conn() as conn:
        row = conn.execute("SELECT fetch_time FROM hot_rank_up ORDER BY fetch_time DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM hot_rank_up WHERE fetch_time=? ORDER BY rank_change DESC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── northbound_flow ───────────────────────────────────────────────────────────

def insert_northbound_flow(fetch_time, trade_date, channel, direction, net_buy, net_inflow):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO northbound_flow (fetch_time,trade_date,channel,direction,net_buy,net_inflow) VALUES (?,?,?,?,?,?)",
            (fetch_time, trade_date, channel, direction, net_buy, net_inflow),
        )


def get_northbound_flow_latest() -> list[dict]:
    """返回最新一批所有渠道"""
    with _conn() as conn:
        row = conn.execute("SELECT fetch_time FROM northbound_flow ORDER BY fetch_time DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM northbound_flow WHERE fetch_time=? ORDER BY direction,channel",
            (row[0],),
        )
        return _rows_to_dicts(cur)


# ── xq_hot ────────────────────────────────────────────────────────────────────

def insert_xq_hot(fetch_time, rank, stock_code, stock_name, follow_cnt, price):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO xq_hot (fetch_time,rank,stock_code,stock_name,follow_cnt,price) VALUES (?,?,?,?,?,?)",
            (fetch_time, rank, stock_code, stock_name, follow_cnt, price),
        )


def get_xq_hot_latest(top_n=30) -> list[dict]:
    """返回最新一批，按 rank 升序"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetch_time FROM xq_hot ORDER BY fetch_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM xq_hot WHERE fetch_time=? ORDER BY rank ASC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── big_deal ──────────────────────────────────────────────────────────────────

def insert_big_deal(fetch_time: str, deal_time: str, stock_code: str, stock_name: str,
                    price: float, volume: int, amount: float, deal_type: str,
                    change_pct: float = None, change_amt: float = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO big_deal "
            "(fetch_time, deal_time, stock_code, stock_name, price, volume, amount, deal_type, change_pct, change_amt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, deal_time, stock_code, stock_name, price, volume, amount, deal_type, change_pct, change_amt),
        )

def get_big_deal_latest(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM big_deal ORDER BY deal_time DESC, id DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


# ── margin ────────────────────────────────────────────────────────────────────

def insert_margin(fetch_time, trade_date, stock_code, stock_name,
                  rzye, rzmre, rzche, rqye, rqmcl, rzrqye) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO margin "
            "(fetch_time,trade_date,stock_code,stock_name,rzye,rzmre,rzche,rqye,rqmcl,rzrqye) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, trade_date, stock_code, stock_name, rzye, rzmre, rzche, rqye, rqmcl, rzrqye),
        )


def get_margin_latest(top_n: int = 50) -> list[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT trade_date FROM margin ORDER BY trade_date DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM margin WHERE trade_date = ? ORDER BY rzye DESC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── block_trade ───────────────────────────────────────────────────────────────

def insert_block_trade(trade_date, stock_code, stock_name,
                       deal_price, close_price, deal_volume, deal_amt,
                       buyer_name, seller_name) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO block_trade "
            "(trade_date,stock_code,stock_name,deal_price,close_price,deal_volume,deal_amt,buyer_name,seller_name) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, deal_price, close_price,
             deal_volume, deal_amt, buyer_name, seller_name),
        )


def get_block_trade_latest(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT trade_date FROM block_trade ORDER BY trade_date DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM block_trade WHERE trade_date = ? ORDER BY deal_amt DESC LIMIT ?",
            (row[0], limit),
        )
        return _rows_to_dicts(cur)


# ── holder_count ──────────────────────────────────────────────────────────────

def insert_holder_count(end_date, stock_code, stock_name,
                        holder_num, holder_num_change, holder_num_ratio, avg_free_shares) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holder_count "
            "(end_date,stock_code,stock_name,holder_num,holder_num_change,holder_num_ratio,avg_free_shares) "
            "VALUES (?,?,?,?,?,?,?)",
            (end_date, stock_code, stock_name, holder_num, holder_num_change,
             holder_num_ratio, avg_free_shares),
        )


def get_holder_count_latest(top_n: int = 50) -> list[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT end_date FROM holder_count ORDER BY end_date DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM holder_count WHERE end_date = ? ORDER BY holder_num_change ASC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── lockup_expiry ─────────────────────────────────────────────────────────────

def insert_lockup_expiry(free_date, stock_code, stock_name,
                         lift_shares, lift_market_cap, lift_ratio,
                         hold_num, lift_type) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lockup_expiry "
            "(free_date,stock_code,stock_name,lift_shares,lift_market_cap,lift_ratio,hold_num,lift_type) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (free_date, stock_code, stock_name, lift_shares, lift_market_cap,
             lift_ratio, hold_num, lift_type),
        )


def get_lockup_expiry(days: int = 30) -> list[dict]:
    today = _today()
    end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM lockup_expiry WHERE free_date >= ? AND free_date <= ? ORDER BY free_date ASC",
            (today, end),
        )
        return _rows_to_dicts(cur)


def get_lockup_expiry_by_code(stock_code: str, days: int = 30) -> list[dict]:
    today = _today()
    end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM lockup_expiry WHERE stock_code=? AND free_date >= ? AND free_date <= ? ORDER BY free_date ASC",
            (stock_code, today, end),
        )
        return _rows_to_dicts(cur)


# ── dividend ──────────────────────────────────────────────────────────────────

def insert_dividend(ex_dividend_date, stock_code, stock_name,
                    pretax_bonus_rmb, transfer_ratio, bonus_ratio, assign_progress) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend "
            "(ex_dividend_date,stock_code,stock_name,pretax_bonus_rmb,transfer_ratio,bonus_ratio,assign_progress) "
            "VALUES (?,?,?,?,?,?,?)",
            (ex_dividend_date, stock_code, stock_name, pretax_bonus_rmb,
             transfer_ratio, bonus_ratio, assign_progress),
        )


def get_dividend_latest(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM dividend ORDER BY ex_dividend_date DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


# ── industry_ranking ──────────────────────────────────────────────────────────

def insert_industry_ranking(fetch_time, sector_code, sector_name,
                             change_pct, price, up_count, down_count,
                             lead_stock, lead_pct) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO industry_ranking "
            "(fetch_time,sector_code,sector_name,change_pct,price,up_count,down_count,lead_stock,lead_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (fetch_time, sector_code, sector_name, change_pct, price,
             up_count, down_count, lead_stock, lead_pct),
        )


def get_industry_ranking_latest() -> list[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetch_time FROM industry_ranking ORDER BY fetch_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM industry_ranking WHERE fetch_time=? ORDER BY change_pct DESC",
            (row[0],),
        )
        return _rows_to_dicts(cur)


# ── ths_hot_stocks ────────────────────────────────────────────────────────────

def insert_ths_hot_stock(fetch_time, stock_code, stock_name, reason, industry, change_pct) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ths_hot_stocks (fetch_time,stock_code,stock_name,reason,industry,change_pct) "
            "VALUES (?,?,?,?,?,?)",
            (fetch_time, stock_code, stock_name, reason, industry, change_pct),
        )


def get_ths_hot_stocks_latest(top_n: int = 50) -> list[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetch_time FROM ths_hot_stocks ORDER BY fetch_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM ths_hot_stocks WHERE fetch_time=? ORDER BY change_pct DESC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── sector_chip_pressure ──────────────────────────────────────────────────────

def insert_sector_chip_pressure(trade_date: str, industry: str, stock_count: int,
                                 avg_overhead: float, avg_win_rate: float,
                                 high_overhead_cnt: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_chip_pressure "
            "(trade_date, industry, stock_count, avg_overhead, avg_win_rate, high_overhead_cnt) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, industry, stock_count, avg_overhead, avg_win_rate, high_overhead_cnt),
        )


def get_sector_chip_pressure(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM sector_chip_pressure WHERE trade_date = ? ORDER BY avg_overhead DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── sector_auction_sentiment ──────────────────────────────────────────────────

def insert_sector_auction_sentiment(trade_date: str, industry: str, stock_count: int,
                                     avg_auction_ratio: float, strong_cnt: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_auction_sentiment "
            "(trade_date, industry, stock_count, avg_auction_ratio, strong_cnt) "
            "VALUES (?,?,?,?,?)",
            (trade_date, industry, stock_count, avg_auction_ratio, strong_cnt),
        )


def get_sector_auction_sentiment(trade_date: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM sector_auction_sentiment WHERE trade_date = ? ORDER BY avg_auction_ratio DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── market_breadth ────────────────────────────────────────────────────────────

def insert_market_breadth(fetch_time, source, market, up_count, down_count,
                          flat_count=None, ad_ratio=None, index_amount=None, index_price=None,
                          total_amount=None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO market_breadth "
            "(fetch_time, source, market, up_count, down_count, flat_count, ad_ratio, index_amount, index_price, total_amount) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, source, market, up_count, down_count, flat_count, ad_ratio, index_amount, index_price, total_amount),
        )


def get_market_breadth_latest(n: int = 120) -> list[dict]:
    """返回最近 n 条 market_breadth 记录，按 fetch_time 升序（最旧在前）便于趋势分析。"""
    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM market_breadth ORDER BY created_at DESC LIMIT ?", (n,)
        )
        rows = _rows_to_dicts(cur)
    return list(reversed(rows))


# ── Watchlist（关注股池，多池分组） ───────────────────────────────────────────

DEFAULT_POOL = "默认"


def get_watchlist(pool: str | None = None) -> list[dict]:
    """返回股池股票。pool=None 返回全部池（每项带 pool 字段）。"""
    with _conn() as conn:
        if pool:
            cur = conn.execute(
                "SELECT id, pool, code, name, note, added_at, sort FROM watchlist "
                "WHERE pool = ? ORDER BY sort ASC, id ASC",
                (pool,),
            )
        else:
            cur = conn.execute(
                "SELECT id, pool, code, name, note, added_at, sort FROM watchlist "
                "ORDER BY pool ASC, sort ASC, id ASC"
            )
        return _rows_to_dicts(cur)


def pool_exists(name: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM watchlist_pools WHERE name = ?", (name,)).fetchone()
        return row is not None


def create_pool(name: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO watchlist_pools (name) VALUES (?)", (name,))


def list_pools() -> list[dict]:
    """池列表含股票数量：[{"name", "count", "created_at"}]"""
    with _conn() as conn:
        cur = conn.execute(
            "SELECT p.name, p.created_at, COUNT(w.id) AS count "
            "FROM watchlist_pools p LEFT JOIN watchlist w ON w.pool = p.name "
            "GROUP BY p.name ORDER BY p.rowid ASC"
        )
        return _rows_to_dicts(cur)


def rename_pool(old: str, new: str) -> bool:
    """池改名（池内股票跟随）。'默认'池不可改名。成功返回 True。"""
    if old == DEFAULT_POOL or old == new:
        return False
    with _conn() as conn:
        exists = conn.execute("SELECT 1 FROM watchlist_pools WHERE name = ?", (old,)).fetchone()
        dup = conn.execute("SELECT 1 FROM watchlist_pools WHERE name = ?", (new,)).fetchone()
        if not exists or dup:
            return False
        conn.execute("UPDATE watchlist_pools SET name = ? WHERE name = ?", (new, old))
        conn.execute("UPDATE watchlist SET pool = ? WHERE pool = ?", (new, old))
        return True


def delete_pool(name: str) -> int | None:
    """删除池并连带删除池内股票，返回删除条数。'默认'池拒绝删除返回 None。"""
    if name == DEFAULT_POOL:
        return None
    with _conn() as conn:
        exists = conn.execute("SELECT 1 FROM watchlist_pools WHERE name = ?", (name,)).fetchone()
        if not exists:
            return None
        cur = conn.execute("DELETE FROM watchlist WHERE pool = ?", (name,))
        conn.execute("DELETE FROM watchlist_pools WHERE name = ?", (name,))
        return cur.rowcount


def add_watchlist(code: str, name: str = "", note: str = "", pool: str = DEFAULT_POOL) -> dict:
    """新增关注股票；(pool, code) 已存在时更新 name，note 仅在传入非空时覆盖。"""
    added_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO watchlist (pool, code, name, note, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(pool, code) DO UPDATE SET name = excluded.name, "
            "note = CASE WHEN excluded.note != '' THEN excluded.note ELSE watchlist.note END",
            (pool, code, name, note, added_at),
        )
        row = conn.execute(
            "SELECT id, pool, code, name, note, added_at, sort FROM watchlist WHERE pool = ? AND code = ?",
            (pool, code),
        ).fetchone()
    return {"id": row[0], "pool": row[1], "code": row[2], "name": row[3], "note": row[4], "added_at": row[5], "sort": row[6]}


def remove_watchlist(code: str, pool: str = DEFAULT_POOL) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE pool = ? AND code = ?", (pool, code))
        return cur.rowcount > 0


def update_watchlist_note(code: str, note: str, pool: str = DEFAULT_POOL) -> bool:
    with _conn() as conn:
        cur = conn.execute("UPDATE watchlist SET note = ? WHERE pool = ? AND code = ?", (note, pool, code))
        return cur.rowcount > 0


# ── Module init ───────────────────────────────────────────────────────────────

with _conn() as _init_conn:
    init_db()
    _migrate(_init_conn)
