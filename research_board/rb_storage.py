"""
research_board — 独立 SQLite 存储层
数据库文件：market-radar 根目录下的 research_board.db（与 market.db 并列）
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "research_board.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS rb_project (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            keywords     TEXT NOT NULL,          -- JSON list，搜索关键词
            dimensions   TEXT NOT NULL,          -- JSON list，分析维度名称
            days_back    INTEGER DEFAULT 180,    -- 研报时间范围（天）
            qtype_filter TEXT DEFAULT '[0,1,2,3]', -- JSON list，东财 qtype
            status       TEXT DEFAULT 'idle',    -- idle/fetching/analyzing/done/error
            report_count INTEGER DEFAULT 0,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rb_report (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   INTEGER NOT NULL REFERENCES rb_project(id),
            title        TEXT NOT NULL,
            stock_code   TEXT,
            stock_name   TEXT,
            org_name     TEXT,
            researcher   TEXT,
            publish_date TEXT,
            rating       TEXT,
            aim_price    TEXT,
            report_url   TEXT,
            qtype        INTEGER DEFAULT 0,
            pdf_status   TEXT DEFAULT 'pending',  -- pending/downloading/done/failed
            full_text    TEXT,                    -- pdfplumber 提取的纯文字
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, report_url)
        );

        CREATE TABLE IF NOT EXISTS rb_analysis (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   INTEGER NOT NULL REFERENCES rb_project(id),
            dimension    TEXT NOT NULL,           -- 分析维度名称
            batch_index  INTEGER DEFAULT 0,       -- 第几批（每批N篇研报）
            prompt_hash  TEXT,                    -- 提示词哈希，防重复
            result_json  TEXT,                    -- Kimi 输出的 JSON 字符串
            status       TEXT DEFAULT 'pending',  -- pending/running/done/failed
            error_msg    TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, dimension, batch_index)
        );

        CREATE TABLE IF NOT EXISTS rb_result (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   INTEGER NOT NULL UNIQUE REFERENCES rb_project(id),
            summary_json TEXT NOT NULL,           -- 汇总后的完整看板 JSON
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)


# ── rb_project ─────────────────────────────────────────────────────────────

def create_project(name: str, keywords: list, dimensions: list,
                   days_back: int = 180, qtype_filter: list = None) -> int:
    qtype_filter = qtype_filter or [0, 1, 2, 3]
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO rb_project (name, keywords, dimensions, days_back, qtype_filter) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, json.dumps(keywords, ensure_ascii=False),
             json.dumps(dimensions, ensure_ascii=False),
             days_back, json.dumps(qtype_filter))
        )
        return cur.lastrowid


def get_project(project_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM rb_project WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["keywords"] = json.loads(d["keywords"])
        d["dimensions"] = json.loads(d["dimensions"])
        d["qtype_filter"] = json.loads(d["qtype_filter"])
        return d


def list_projects() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM rb_project ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["keywords"] = json.loads(d["keywords"])
        d["dimensions"] = json.loads(d["dimensions"])
        d["qtype_filter"] = json.loads(d["qtype_filter"])
        result.append(d)
    return result


def update_project_status(project_id: int, status: str, report_count: int = None) -> None:
    with _conn() as conn:
        if report_count is not None:
            conn.execute(
                "UPDATE rb_project SET status=?, report_count=?, updated_at=? WHERE id=?",
                (status, report_count, datetime.now().isoformat(), project_id)
            )
        else:
            conn.execute(
                "UPDATE rb_project SET status=?, updated_at=? WHERE id=?",
                (status, datetime.now().isoformat(), project_id)
            )


def delete_project(project_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM rb_analysis WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM rb_report WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM rb_result WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM rb_project WHERE id=?", (project_id,))


# ── rb_report ─────────────────────────────────────────────────────────────

def insert_rb_report(project_id: int, title: str, stock_code: str, stock_name: str,
                     org_name: str, researcher: str, publish_date: str, rating: str,
                     aim_price: str, report_url: str, qtype: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO rb_report "
            "(project_id, title, stock_code, stock_name, org_name, researcher, "
            "publish_date, rating, aim_price, report_url, qtype) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, title, stock_code, stock_name, org_name, researcher,
             publish_date, rating, aim_price, report_url, qtype)
        )


def get_rb_reports(project_id: int, pdf_status: str = None) -> list[dict]:
    with _conn() as conn:
        if pdf_status:
            rows = conn.execute(
                "SELECT * FROM rb_report WHERE project_id=? AND pdf_status=? ORDER BY publish_date DESC",
                (project_id, pdf_status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rb_report WHERE project_id=? ORDER BY publish_date DESC",
                (project_id,)
            ).fetchall()
    return _rows_to_dicts(rows)


def update_rb_report_text(report_id: int, full_text: str, status: str = "done") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE rb_report SET full_text=?, pdf_status=? WHERE id=?",
            (full_text, status, report_id)
        )


def update_rb_report_pdf_status(report_id: int, status: str, error: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE rb_report SET pdf_status=? WHERE id=?",
            (status, report_id)
        )


# ── rb_analysis ──────────────────────────────────────────────────────────

def upsert_rb_analysis(project_id: int, dimension: str, batch_index: int,
                        result_json: str, status: str, error_msg: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO rb_analysis (project_id, dimension, batch_index, result_json, status, error_msg) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, dimension, batch_index) DO UPDATE SET "
            "result_json=excluded.result_json, status=excluded.status, error_msg=excluded.error_msg",
            (project_id, dimension, batch_index, result_json, status, error_msg)
        )


def get_rb_analyses(project_id: int, status: str = None) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM rb_analysis WHERE project_id=? AND status=? ORDER BY dimension, batch_index",
                (project_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rb_analysis WHERE project_id=? ORDER BY dimension, batch_index",
                (project_id,)
            ).fetchall()
    return _rows_to_dicts(rows)


# ── rb_result ─────────────────────────────────────────────────────────────

def upsert_rb_result(project_id: int, summary_json: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO rb_result (project_id, summary_json, generated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "summary_json=excluded.summary_json, generated_at=excluded.generated_at",
            (project_id, summary_json, datetime.now().isoformat())
        )


def get_rb_result(project_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM rb_result WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["summary"] = json.loads(d["summary_json"])
    except Exception:
        d["summary"] = {}
    return d
