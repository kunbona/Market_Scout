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
    # research_report table (created fresh if not exists via init_db, but add migration for existing DBs)
    try:
        conn.execute("SELECT 1 FROM research_report LIMIT 1")
    except Exception:
        conn.execute("""CREATE TABLE IF NOT EXISTS research_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            stock_code TEXT, stock_name TEXT, org_name TEXT, researcher TEXT,
            publish_date TEXT, rating TEXT, aim_price TEXT,
            report_url TEXT UNIQUE, qtype INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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

CREATE TABLE IF NOT EXISTS zt_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    zt_count      INTEGER,
    first_zt_time TEXT,
    sector        TEXT,
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
        """)


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


def insert_lhb_data(trade_date, stock_code, stock_name, reason, net_buy) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO lhb_data (trade_date, stock_code, stock_name, reason, net_buy) VALUES (?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, reason, net_buy),
        )


def insert_zt_pool(trade_date, stock_code, stock_name, zt_count, first_zt_time, sector) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO zt_pool "
            "(trade_date, stock_code, stock_name, zt_count, first_zt_time, sector) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trade_date, stock_code, stock_name, zt_count, first_zt_time, sector),
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


def insert_agent_summary(content, data_snapshot_json) -> None:
    summary_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO agent_summary (summary_time, content, data_snapshot_json) VALUES (?, ?, ?)",
            (summary_time, content, data_snapshot_json),
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


def get_cls_news_by_source(source: str, limit: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM cls_news WHERE source = ? ORDER BY pub_time DESC LIMIT ?",
            (source, limit),
        )
        return _rows_to_dicts(cur)


def get_policy_news(limit=20) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news ORDER BY pub_time DESC LIMIT ?", (limit,)
        )
        return _rows_to_dicts(cur)


def get_policy_news_by_source(source: str, limit: int = 50) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT * FROM policy_news WHERE source = ? ORDER BY pub_time DESC LIMIT ?",
            (source, limit),
        )
        return _rows_to_dicts(cur)


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
            "SELECT stock_code, stock_name, zt_count, first_zt_time, sector "
            "FROM zt_pool WHERE trade_date = ? ORDER BY zt_count DESC",
            (today,),
        )
        zt_today = _rows_to_dicts(cur)

    return {
        "recent_cls_news": recent_cls,
        "policy_news_today": policy_titles,
        "sector_flow_top10": sector_top10,
        "zt_pool_today": zt_today,
    }


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup_old_data() -> None:
    now = datetime.now()
    cls_cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    sector_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM cls_news WHERE created_at < ?", (cls_cutoff,))
        conn.execute("DELETE FROM sector_flow WHERE fetch_time < ?", (sector_cutoff,))


with sqlite3.connect(DB_PATH) as _conn:
    init_db()
    _migrate(_conn)
