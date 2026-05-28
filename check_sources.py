"""快速诊断：检查 RSSHub 连通性 + 各路由返回条数 + 数据库现有 source 分布"""
import os, sys
from pathlib import Path

# 加载 .env.local
_env = Path(__file__).parent / ".env.local"
if _env.exists():
    for line in _env.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RSSHUB = os.getenv("RSSHUB_BASE_URL", "http://localhost:1200")
print(f"RSSHUB_BASE_URL = {RSSHUB}\n")

import requests, feedparser, sqlite3
from db.storage import DB_PATH

# 1. RSSHub 连通性
print("=== RSSHub 连通性 ===")
try:
    r = requests.get(f"{RSSHUB}/", timeout=5)
    print(f"✓ {RSSHUB}  →  HTTP {r.status_code}")
except Exception as e:
    print(f"✗ 无法连接 RSSHub: {e}")
    sys.exit(1)

# 2. 各路由条数
print("\n=== 各路由返回条数 ===")
routes = [
    ("/cls/telegraph/red", "财联社重点"),
    ("/gov/ndrc/xwdt/xwfb", "发改委新闻"),
    ("/sse/inquire", "上交所问询"),
    ("/szse/inquire", "深交所问询"),
    ("/wallstreetcn/live/a-stock/2", "华尔街见闻A股"),
]
for path, name in routes:
    try:
        feed = feedparser.parse(f"{RSSHUB}{path}")
        n = len(feed.entries)
        status = "✓" if n > 0 else "⚠ 0条"
        print(f"  {status}  {name:16s}  {n} 条  ({RSSHUB}{path})")
    except Exception as e:
        print(f"  ✗  {name:16s}  失败: {e}")

# 3. 数据库现有 source 分布
print("\n=== 数据库 source 分布 ===")
with sqlite3.connect(DB_PATH) as conn:
    print("cls_news:")
    for row in conn.execute("SELECT source, count(*) FROM cls_news GROUP BY source ORDER BY count(*) DESC"):
        print(f"  {row[0] or '(null)':16s}  {row[1]} 条")
    print("policy_news:")
    for row in conn.execute("SELECT source, count(*) FROM policy_news GROUP BY source ORDER BY count(*) DESC"):
        print(f"  {row[0] or '(null)':16s}  {row[1]} 条")
