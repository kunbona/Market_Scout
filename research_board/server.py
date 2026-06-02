"""
research_board — 独立运行模式
用法：python -m research_board.server
或：  cd market-radar && python research_board/server.py

端口默认 20027，通过 RB_PORT 环境变量覆盖。
"""

import os
import sys
import signal

# 确保可以 import market-radar 根目录的模块（db/ fetcher/ 等）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 加载 .env.local
_env_path = os.path.join(_ROOT, ".env.local")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from flask import Flask
from flask_cors import CORS
from research_board.blueprint import rb_bp

app = Flask(__name__, static_folder=None)
CORS(app)
app.register_blueprint(rb_bp)


@app.route("/")
def index():
    return {"service": "research_board", "status": "ok"}


def _serve(port: int) -> None:
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    port = int(os.environ.get("RB_PORT", 20027))
    print(f"[research_board] 启动独立服务 → http://0.0.0.0:{port}")

    def _shutdown(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _serve(port)
