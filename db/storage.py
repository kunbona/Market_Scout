import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "market.db"


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
    # market_pulse 新增乐咕字段
    mp_cols = {row[1] for row in conn.execute("PRAGMA table_info(market_pulse)")}
    for col, coldef in [
        ("real_zt",  "INTEGER"),
        ("real_dt",  "INTEGER"),
        ("activity", "REAL"),
        ("advance",  "INTEGER"),
        ("decline",  "INTEGER"),
    ]:
        if col not in mp_cols:
            conn.execute(f"ALTER TABLE market_pulse ADD COLUMN {col} {coldef}")
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


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
    link       TEXT UNIQUE,
    pub_time   TEXT,
    source     TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS market_pulse (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    zt_count    INTEGER,
    dt_count    INTEGER,
    zb_count    INTEGER,
    zt_dt_ratio REAL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        """)
        # 增量迁移：为旧版 DB 补充新增列（列已存在时忽略）
        _migrations = [
            "ALTER TABLE market_emotion ADD COLUMN real_zt INTEGER",
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO cls_news (source, title, content, pub_time, link) VALUES (?, ?, ?, ?, ?)",
            (source, title, content, pub_time, link),
        )


def insert_policy_news(title, link, pub_time, source) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO policy_news (title, link, pub_time, source) VALUES (?, ?, ?, ?)",
            (title, link, pub_time, source),
        )


def insert_sector_flow(fetch_time, sector_name, change_pct, main_inflow, main_inflow_pct, source_type="industry") -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO sector_flow (fetch_time, source_type, sector_name, change_pct, main_inflow, main_inflow_pct) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fetch_time, source_type, sector_name, change_pct, main_inflow, main_inflow_pct),
        )


def insert_lhb_data(trade_date, stock_code, stock_name, reason, net_buy,
                    change_pct=None, interpret="", net_buy_ratio=None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lhb_data "
            "(trade_date, stock_code, stock_name, reason, net_buy, change_pct, interpret, net_buy_ratio) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, reason, net_buy, change_pct, interpret, net_buy_ratio),
        )


def insert_lhb_seat(trade_date, stock_code, seat_name, buy_amount, sell_amount,
                    net_amount, buy_ratio, sell_ratio, seat_type, reason, rank) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lhb_seat "
            "(trade_date,stock_code,seat_name,buy_amount,sell_amount,net_amount,"
            " buy_ratio,sell_ratio,seat_type,reason,rank) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, seat_name, buy_amount, sell_amount,
             net_amount, buy_ratio, sell_ratio, seat_type, reason, rank),
        )


def get_lhb_seat(trade_date=None, stock_code=None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO zt_pool "
            "(trade_date, stock_code, stock_name, zt_count, first_zt_time, sector, "
            " last_zt_time, seal_amount, zb_count, turnover_rate, circ_mv) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, zt_count, first_zt_time, sector,
             last_zt_time, seal_amount, zb_count, turnover_rate, circ_mv),
        )


def insert_dt_pool(trade_date, stock_code, stock_name, first_dt_time, sector) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO dt_pool "
            "(trade_date, stock_code, stock_name, first_dt_time, sector) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, first_dt_time, sector),
        )


def insert_quant_signal(signal_date, stock_code, signal_type, signal_value, extra_json="") -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO quant_signals (signal_date, stock_code, signal_type, signal_value, extra_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (signal_date, stock_code, signal_type, signal_value, extra_json),
        )


def insert_agent_summary(content, data_snapshot_json, run_type: str = "") -> None:
    summary_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        # 确保 run_type 列存在（兼容旧 DB）
        try:
            conn.execute("ALTER TABLE agent_summary ADD COLUMN run_type TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute(
            "INSERT INTO agent_summary (summary_time, run_type, content, data_snapshot_json) VALUES (?, ?, ?, ?)",
            (summary_time, run_type, content, data_snapshot_json),
        )


def insert_research_report(title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype=0) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_report "
            "(title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, stock_code, stock_name, org_name, researcher, publish_date, rating, aim_price, report_url, qtype),
        )


# ── Read ──────────────────────────────────────────────────────────────────────

def get_cls_news(limit=50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM cls_news ORDER BY pub_time DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


def get_cls_news_by_source(source: str, limit: int = 50, offset: int = 0) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM cls_news WHERE source = ? ORDER BY pub_time DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
        return _rows_to_dicts(cur)


def count_cls_news_by_source(source: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT count(*) FROM cls_news WHERE source = ?", (source,)
        ).fetchone()[0]


def get_policy_news(limit=20, offset: int = 0) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news ORDER BY pub_time DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return _rows_to_dicts(cur)


def get_policy_news_by_source(source: str, limit: int = 50, offset: int = 0) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news WHERE source = ? ORDER BY pub_time DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
        return _rows_to_dicts(cur)


def count_policy_news_by_source(source: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT count(*) FROM policy_news WHERE source = ?", (source,)
        ).fetchone()[0]


def get_sector_flow_latest(source_type="industry") -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        date = trade_date or _latest_trade_date(conn, "lhb_data")
        cur = conn.execute(
            "SELECT * FROM lhb_data WHERE trade_date = ?", (date,)
        )
        return _rows_to_dicts(cur)


def get_zt_pool(trade_date=None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        date = trade_date or _latest_trade_date(conn, "zt_pool")
        cur = conn.execute(
            "SELECT * FROM zt_pool WHERE trade_date = ? ORDER BY zt_count DESC",
            (date,),
        )
        return _rows_to_dicts(cur)


def get_dt_pool(trade_date=None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        date = trade_date or _latest_trade_date(conn, "dt_pool")
        cur = conn.execute(
            "SELECT * FROM dt_pool WHERE trade_date = ?", (date,)
        )
        return _rows_to_dicts(cur)


def get_quant_signals(signal_date=None) -> list[dict]:
    signal_date = signal_date or _today()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM quant_signals WHERE signal_date = ? ORDER BY signal_type",
            (signal_date,),
        )
        return _rows_to_dicts(cur)


def get_agent_summary_latest() -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM agent_summary ORDER BY created_at DESC LIMIT 1"
        )
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else None


def get_agent_summary_by_id(row_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT * FROM agent_summary WHERE id = ?", (row_id,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else None


def get_agent_summary_history(limit: int = 20, today_only: bool = False) -> list[dict]:
    today = _today()
    with sqlite3.connect(DB_PATH) as conn:
        if today_only:
            cur = conn.execute(
                "SELECT id, summary_time, run_type, content FROM agent_summary "
                "WHERE summary_time >= ? ORDER BY created_at DESC LIMIT ?",
                (today, limit),
            )
        else:
            cur = conn.execute(
                "SELECT id, summary_time, run_type, content FROM agent_summary "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return _rows_to_dicts(cur)


def get_research_reports(qtype: int = None, limit: int = 50, today_only: bool = False) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
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

    with sqlite3.connect(DB_PATH) as conn:
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

        # fundamentals_f10：当日涨停股的基本面（供股票Agent判断主营业务相关性）
        zt_codes = [r["stock_code"] for r in zt_today if r.get("stock_code")]
        f10_today = []
        if zt_codes:
            placeholders = ",".join("?" * len(zt_codes))
            cur = conn.execute(
                f"SELECT stock_code, category, content FROM fundamentals_f10 "
                f"WHERE fetch_date = ? AND stock_code IN ({placeholders}) AND category = '公司概况' "
                f"ORDER BY stock_code",
                [today] + zt_codes,
            )
            f10_today = _rows_to_dicts(cur)

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
        "f10_today": f10_today,
    }


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup_old_data() -> None:
    now = datetime.now()
    cutoff_7d  = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_60d = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_90d  = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_365d = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        # 7 days
        conn.execute("DELETE FROM cls_news WHERE created_at < ?", (cutoff_7d,))
        # 30 days
        conn.execute("DELETE FROM sector_flow WHERE fetch_time < ?", (cutoff_30d,))
        conn.execute("DELETE FROM market_pulse WHERE created_at < ?", (cutoff_30d,))
        # 60 days
        conn.execute("DELETE FROM agent_summary WHERE created_at < ?", (cutoff_60d,))
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
        # fundamentals: 保留最近 7 天（每日按活跃股更新）
        conn.execute("DELETE FROM fundamentals_finance WHERE fetch_date < ?", (cutoff_7d_date,))
        conn.execute("DELETE FROM fundamentals_f10 WHERE fetch_date < ?", (cutoff_7d_date,))
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


# ── market_pulse ──────────────────────────────────────────────────────────────

def insert_market_pulse(fetch_time: str, zt_count: int, dt_count: int, zb_count: int, zt_dt_ratio: float,
                        real_zt=None, real_dt=None, activity=None,
                        advance=None, decline=None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO market_pulse "
            "(fetch_time, zt_count, dt_count, zb_count, zt_dt_ratio, "
            " real_zt, real_dt, activity, advance, decline) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, zt_count, dt_count, zb_count, zt_dt_ratio,
             real_zt, real_dt, activity, advance, decline),
        )


def get_market_pulse_latest(n: int = 60) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM market_pulse ORDER BY created_at DESC LIMIT ?", (n,)
        )
        return _rows_to_dicts(cur)


# ── market_emotion ────────────────────────────────────────────────────────────

def upsert_market_emotion(trade_date: str, zt_total: int, dt_total: int, zb_total: int,
                          max_lianzban: int, zt_yesterday_premium: float, zb_rate: float,
                          real_zt: int = None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_emotion "
            "(trade_date, zt_total, dt_total, zb_total, max_lianzban, zt_yesterday_premium, zb_rate, real_zt) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, zt_total, dt_total, zb_total, max_lianzban, zt_yesterday_premium, zb_rate, real_zt),
        )


def get_market_emotion(days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM market_emotion WHERE trade_date >= ? ORDER BY trade_date DESC",
            (cutoff,),
        )
        return _rows_to_dicts(cur)


# ── sector_zt_density ─────────────────────────────────────────────────────────

def insert_sector_zt_density(trade_date: str, industry: str, zt_count: int,
                              zt_density: float, max_lianzban: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sector_zt_density "
            "(trade_date, industry, zt_count, zt_density, max_lianzban) VALUES (?,?,?,?,?)",
            (trade_date, industry, zt_count, zt_density, max_lianzban),
        )


def get_sector_zt_density(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM sector_zt_density WHERE trade_date = ? ORDER BY zt_density DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── volume_breakout ───────────────────────────────────────────────────────────

def insert_volume_breakout(trade_date: str, stock_code: str, stock_name: str,
                            industry: str, ratio_5_20: float, amount_5d: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO volume_breakout "
            "(trade_date, stock_code, stock_name, industry, ratio_5_20, amount_5d) VALUES (?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, industry, ratio_5_20, amount_5d),
        )


def get_volume_breakout(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM volume_breakout WHERE trade_date = ? ORDER BY ratio_5_20 DESC LIMIT 50",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── chip_status ───────────────────────────────────────────────────────────────

def insert_chip_status(trade_date: str, stock_code: str, cost_50: float,
                        win_rate: float, overhead_ratio: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chip_status "
            "(trade_date, stock_code, cost_50, win_rate, overhead_ratio) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, cost_50, win_rate, overhead_ratio),
        )


def get_chip_status(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM chip_status WHERE trade_date = ? ORDER BY win_rate DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── lianzban_chain ────────────────────────────────────────────────────────────

def insert_lianzban_chain(trade_date: str, stock_code: str, stock_name: str,
                           industry: str, lianzban_cnt: int, is_zb: bool) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lianzban_chain "
            "(trade_date, stock_code, stock_name, industry, lianzban_cnt, is_zb) VALUES (?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, industry, lianzban_cnt, is_zb),
        )


def get_lianzban_chain(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM lianzban_chain WHERE trade_date = ? AND lianzban_cnt <= 30 ORDER BY lianzban_cnt DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── research_activity ─────────────────────────────────────────────────────────

def insert_research_activity(trade_date: str, stock_code: str, stock_name: str,
                               org_count_5d: int, last_visit_date: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO research_activity "
            "(trade_date, stock_code, stock_name, org_count_5d, last_visit_date) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, stock_name, org_count_5d, last_visit_date),
        )


def get_research_activity(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_flow_accel "
            "(trade_date, industry, inst_inflow_3d, inst_inflow_20d, acceleration, "
            "amount_ma5, amount_ma20, ma5_slope, amount_share_3d, amount_share_30d) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (trade_date, industry, inst_inflow_3d, inst_inflow_20d, acceleration,
             amount_ma5, amount_ma20, ma5_slope, amount_share_3d, amount_share_30d),
        )


def get_sector_flow_accel(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM sector_flow_accel WHERE trade_date = ? ORDER BY acceleration DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── lianzban_stats ────────────────────────────────────────────────────────────

def upsert_lianzban_stats(trade_date: str, tier_1: int, tier_2: int, tier_3: int,
                           tier_4plus: int, advance_1to2: float, advance_2to3: float,
                           advance_3to4: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lianzban_stats "
            "(trade_date, tier_1, tier_2, tier_3, tier_4plus, advance_1to2, advance_2to3, advance_3to4) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, tier_1, tier_2, tier_3, tier_4plus, advance_1to2, advance_2to3, advance_3to4),
        )


def get_lianzban_stats(days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM lianzban_stats WHERE trade_date >= ? ORDER BY trade_date ASC",
            (cutoff,),
        )
        return _rows_to_dicts(cur)


# ── concept_zt_density ────────────────────────────────────────────────────────

def insert_concept_zt_density(trade_date: str, concept: str, zt_count: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO concept_zt_density (trade_date, concept, zt_count) VALUES (?,?,?)",
            (trade_date, concept, zt_count),
        )


def get_concept_zt_density(trade_date: str, top_n: int = 15) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM concept_zt_density WHERE trade_date = ? ORDER BY zt_count DESC LIMIT ?",
            (trade_date, top_n),
        )
        return _rows_to_dicts(cur)


# ── call_auction_stats ────────────────────────────────────────────────────────

def insert_call_auction_stats(trade_date: str, stock_code: str, stock_name: str,
                               auction_ratio: float, auction_amount: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO call_auction_stats "
            "(trade_date, stock_code, stock_name, auction_ratio, auction_amount) VALUES (?,?,?,?,?)",
            (trade_date, stock_code, stock_name, auction_ratio, auction_amount),
        )


def get_call_auction_stats(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT trade_date FROM market_emotion ORDER BY trade_date DESC LIMIT 1").fetchone()
        return row[0] if row else ""


# ── turnover_stats ────────────────────────────────────────────────────────────

def upsert_turnover_stats(trade_date: str, low_count: int, mid_count: int,
                           high_count: int, median_to: float, avg_to: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO turnover_stats "
            "(trade_date, low_count, mid_count, high_count, median_to, avg_to) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, low_count, mid_count, high_count, median_to, avg_to),
        )


def get_turnover_stats(trade_date: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT * FROM turnover_stats WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── market_cap_dist ───────────────────────────────────────────────────────────

def upsert_market_cap_dist(trade_date: str, small_count: int, mid_count: int,
                            large_count: int, small_pct: float, mid_pct: float,
                            large_pct: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_cap_dist "
            "(trade_date, small_count, mid_count, large_count, small_pct, mid_pct, large_pct) "
            "VALUES (?,?,?,?,?,?,?)",
            (trade_date, small_count, mid_count, large_count, small_pct, mid_pct, large_pct),
        )


def get_market_cap_dist(trade_date: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT * FROM market_cap_dist WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── advance_decline ───────────────────────────────────────────────────────────

def upsert_advance_decline(trade_date: str, advance_count: int, decline_count: int,
                            flat_count: int, ad_ratio: float, total_amount: float,
                            amount_ma20: float, amount_ratio: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO advance_decline "
            "(trade_date, advance_count, decline_count, flat_count, ad_ratio, "
            " total_amount, amount_ma20, amount_ratio) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, advance_count, decline_count, flat_count, ad_ratio,
             total_amount, amount_ma20, amount_ratio),
        )


def get_advance_decline(trade_date: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT * FROM advance_decline WHERE trade_date = ?", (trade_date,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else {}


# ── concept_flow ──────────────────────────────────────────────────────────────

def insert_concept_flow(fetch_time, concept, change_pct, net_amount,
                        in_amount, out_amount, lead_stock, lead_pct, stock_count) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO concept_flow (fetch_time, concept, change_pct, net_amount, "
            "in_amount, out_amount, lead_stock, lead_pct, stock_count) VALUES (?,?,?,?,?,?,?,?,?)",
            (fetch_time, concept, change_pct, net_amount,
             in_amount, out_amount, lead_stock, lead_pct, stock_count),
        )


def get_concept_flow_latest(top_n=30) -> list[dict]:
    """返回最新一批概念资金流，按 net_amount 降序取 top_n"""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO zbgc_pool "
            "(trade_date, stock_code, stock_name, first_zt_time, zb_count, amplitude, sector) "
            "VALUES (?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, first_zt_time, zb_count, amplitude, sector),
        )


def get_zbgc_pool(trade_date=None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strong_pool "
            "(trade_date, stock_code, stock_name, change_pct, is_new_high, volume_ratio, reason, sector) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, change_pct, is_new_high, volume_ratio, reason, sector),
        )


def get_strong_pool(trade_date=None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        date = trade_date or _latest_trade_date(conn, "strong_pool")
        cur = conn.execute(
            "SELECT * FROM strong_pool WHERE trade_date = ? ORDER BY change_pct DESC",
            (date,),
        )
        return _rows_to_dicts(cur)


# ── hot_rank_up ───────────────────────────────────────────────────────────────

def insert_hot_rank_up(fetch_time, rank_change, current_rank, stock_code, stock_name, price, change_pct):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO hot_rank_up (fetch_time,rank_change,current_rank,stock_code,stock_name,price,change_pct) VALUES (?,?,?,?,?,?,?)",
            (fetch_time, rank_change, current_rank, stock_code, stock_name, price, change_pct),
        )


def get_hot_rank_up_latest(top_n=20) -> list[dict]:
    """返回最新一批，按 rank_change 降序"""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO northbound_flow (fetch_time,trade_date,channel,direction,net_buy,net_inflow) VALUES (?,?,?,?,?,?)",
            (fetch_time, trade_date, channel, direction, net_buy, net_inflow),
        )


def get_northbound_flow_latest() -> list[dict]:
    """返回最新一批所有渠道"""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO xq_hot (fetch_time,rank,stock_code,stock_name,follow_cnt,price) VALUES (?,?,?,?,?,?)",
            (fetch_time, rank, stock_code, stock_name, follow_cnt, price),
        )


def get_xq_hot_latest(top_n=30) -> list[dict]:
    """返回最新一批，按 rank 升序"""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO big_deal "
            "(fetch_time, deal_time, stock_code, stock_name, price, volume, amount, deal_type, change_pct, change_amt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, deal_time, stock_code, stock_name, price, volume, amount, deal_type, change_pct, change_amt),
        )

def get_big_deal_latest(limit: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM big_deal ORDER BY deal_time DESC, id DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


# ── margin ────────────────────────────────────────────────────────────────────

def insert_margin(fetch_time, trade_date, stock_code, stock_name,
                  rzye, rzmre, rzche, rqye, rqmcl, rzrqye) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO margin "
            "(fetch_time,trade_date,stock_code,stock_name,rzye,rzmre,rzche,rqye,rqmcl,rzrqye) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fetch_time, trade_date, stock_code, stock_name, rzye, rzmre, rzche, rqye, rqmcl, rzrqye),
        )


def get_margin_latest(top_n: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO block_trade "
            "(trade_date,stock_code,stock_name,deal_price,close_price,deal_volume,deal_amt,buyer_name,seller_name) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_date, stock_code, stock_name, deal_price, close_price,
             deal_volume, deal_amt, buyer_name, seller_name),
        )


def get_block_trade_latest(limit: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holder_count "
            "(end_date,stock_code,stock_name,holder_num,holder_num_change,holder_num_ratio,avg_free_shares) "
            "VALUES (?,?,?,?,?,?,?)",
            (end_date, stock_code, stock_name, holder_num, holder_num_change,
             holder_num_ratio, avg_free_shares),
        )


def get_holder_count_latest(top_n: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT end_date FROM holder_count ORDER BY end_date DESC LIMIT 1").fetchone()
        if not row:
            return []
        cur = conn.execute(
            "SELECT * FROM holder_count WHERE end_date = ? ORDER BY holder_num_change ASC LIMIT ?",
            (row[0], top_n),
        )
        return _rows_to_dicts(cur)


# ── fundamentals_finance ──────────────────────────────────────────────────────

def insert_fundamentals_finance(fetch_date, stock_code, data_dict) -> None:
    import json
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals_finance (fetch_date,stock_code,data_json) VALUES (?,?,?)",
            (fetch_date, stock_code, json.dumps(data_dict, ensure_ascii=False, default=str)),
        )


def get_fundamentals_finance(fetch_date: str = None) -> list[dict]:
    import json
    with sqlite3.connect(DB_PATH) as conn:
        if not fetch_date:
            row = conn.execute("SELECT fetch_date FROM fundamentals_finance ORDER BY fetch_date DESC LIMIT 1").fetchone()
            if not row:
                return []
            fetch_date = row[0]
        cur = conn.execute(
            "SELECT stock_code, data_json FROM fundamentals_finance WHERE fetch_date = ?", (fetch_date,)
        )
        return [{"stock_code": r[0], **json.loads(r[1])} for r in cur.fetchall()]


# ── fundamentals_f10 ──────────────────────────────────────────────────────────

def insert_fundamentals_f10(fetch_date, stock_code, category, content) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals_f10 (fetch_date,stock_code,category,content) VALUES (?,?,?,?)",
            (fetch_date, stock_code, category, content),
        )


def get_fundamentals_f10(stock_code: str, fetch_date: str = None) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        if not fetch_date:
            row = conn.execute(
                "SELECT fetch_date FROM fundamentals_f10 WHERE stock_code=? ORDER BY fetch_date DESC LIMIT 1",
                (stock_code,),
            ).fetchone()
            if not row:
                return []
            fetch_date = row[0]
        cur = conn.execute(
            "SELECT category, content FROM fundamentals_f10 WHERE fetch_date=? AND stock_code=?",
            (fetch_date, stock_code),
        )
        return [{"category": r[0], "content": r[1]} for r in cur.fetchall()]


# ── lockup_expiry ─────────────────────────────────────────────────────────────

def insert_lockup_expiry(free_date, stock_code, stock_name,
                         lift_shares, lift_market_cap, lift_ratio,
                         hold_num, lift_type) -> None:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM lockup_expiry WHERE free_date >= ? AND free_date <= ? ORDER BY free_date ASC",
            (today, end),
        )
        return _rows_to_dicts(cur)


def get_lockup_expiry_by_code(stock_code: str, days: int = 30) -> list[dict]:
    today = _today()
    end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM lockup_expiry WHERE stock_code=? AND free_date >= ? AND free_date <= ? ORDER BY free_date ASC",
            (stock_code, today, end),
        )
        return _rows_to_dicts(cur)


# ── dividend ──────────────────────────────────────────────────────────────────

def insert_dividend(ex_dividend_date, stock_code, stock_name,
                    pretax_bonus_rmb, transfer_ratio, bonus_ratio, assign_progress) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend "
            "(ex_dividend_date,stock_code,stock_name,pretax_bonus_rmb,transfer_ratio,bonus_ratio,assign_progress) "
            "VALUES (?,?,?,?,?,?,?)",
            (ex_dividend_date, stock_code, stock_name, pretax_bonus_rmb,
             transfer_ratio, bonus_ratio, assign_progress),
        )


def get_dividend_latest(limit: int = 100) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM dividend ORDER BY ex_dividend_date DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


# ── industry_ranking ──────────────────────────────────────────────────────────

def insert_industry_ranking(fetch_time, sector_code, sector_name,
                             change_pct, price, up_count, down_count,
                             lead_stock, lead_pct) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO industry_ranking "
            "(fetch_time,sector_code,sector_name,change_pct,price,up_count,down_count,lead_stock,lead_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (fetch_time, sector_code, sector_name, change_pct, price,
             up_count, down_count, lead_stock, lead_pct),
        )


def get_industry_ranking_latest() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ths_hot_stocks (fetch_time,stock_code,stock_name,reason,industry,change_pct) "
            "VALUES (?,?,?,?,?,?)",
            (fetch_time, stock_code, stock_name, reason, industry, change_pct),
        )


def get_ths_hot_stocks_latest(top_n: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_chip_pressure "
            "(trade_date, industry, stock_count, avg_overhead, avg_win_rate, high_overhead_cnt) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, industry, stock_count, avg_overhead, avg_win_rate, high_overhead_cnt),
        )


def get_sector_chip_pressure(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM sector_chip_pressure WHERE trade_date = ? ORDER BY avg_overhead DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── sector_auction_sentiment ──────────────────────────────────────────────────

def insert_sector_auction_sentiment(trade_date: str, industry: str, stock_count: int,
                                     avg_auction_ratio: float, strong_cnt: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sector_auction_sentiment "
            "(trade_date, industry, stock_count, avg_auction_ratio, strong_cnt) "
            "VALUES (?,?,?,?,?)",
            (trade_date, industry, stock_count, avg_auction_ratio, strong_cnt),
        )


def get_sector_auction_sentiment(trade_date: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM sector_auction_sentiment WHERE trade_date = ? ORDER BY avg_auction_ratio DESC",
            (trade_date,),
        )
        return _rows_to_dicts(cur)


# ── Module init ───────────────────────────────────────────────────────────────

with sqlite3.connect(DB_PATH) as _conn:
    init_db()
    _migrate(_conn)
