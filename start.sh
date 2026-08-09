#!/bin/bash
# Market Radar 一键启动脚本
# 用法: bash start.sh        （终端）
# 或直接双击 "启动 Market Radar.command"
#
# 做的事情：
#   1. 已在运行则直接报告，不重复启动
#   2. 启动 colima 虚拟机 + RSSHub 容器（没装则自动跳过）
#   3. 清理抢占 20026 端口的残留 dev server（vite/npm）
#   4. 后台启动 server.py 并等待自检通过

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env.local"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs)
fi

FLASK_PORT=${FLASK_PORT:-20026}
LOG_FILE="${MR_LOG:-/tmp/mr_server.log}"

# 虚拟环境（依赖都装在这里）
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PY="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

echo "=============================="
echo " Market Radar 一键启动"
echo "=============================="

# ---------- 1. 已在运行？ ----------
# 注意：必须以 Flask 的 /api/config 返回 JSON 为准。
# 只看首页 200 会被抢占端口的 Vite dev server 误判成"已在运行"。
api_ok() {
    curl -s --max-time 3 "http://localhost:$FLASK_PORT/api/config" 2>/dev/null | grep -q '"success":true'
}

if pgrep -f "server\.py" >/dev/null 2>&1 && api_ok; then
    echo "[✓] 服务已在运行 → http://localhost:$FLASK_PORT"
    exit 0
fi

# ---------- 2. colima + RSSHub ----------
COLIMA_OK=""
if command -v colima >/dev/null 2>&1; then
    if ! colima status >/dev/null 2>&1; then
        echo "[…] 启动 colima 虚拟机（约 30-90 秒）"
        # 失败后自动重试一次，错误输出可见（不再吞掉）
        if colima start 2>&1 | tail -2 || colima start 2>&1 | tail -5; then
            colima status >/dev/null 2>&1 && COLIMA_OK=1
        fi
        if [ -n "$COLIMA_OK" ]; then
            echo "[✓] colima 已启动"
        else
            echo "[!] colima 启动失败，RSS 新闻源将降级（不影响主服务）"
        fi
    else
        echo "[✓] colima 已在运行"
        COLIMA_OK=1
    fi

    if [ -n "$COLIMA_OK" ] && command -v docker >/dev/null 2>&1; then
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx rsshub; then
            docker ps --format '{{.Names}}' 2>/dev/null | grep -qx rsshub \
                || docker start rsshub >/dev/null 2>&1
        fi
        # 以 HTTP 实际可用为准（容器可能已启动但服务未就绪）
        echo "[…] 等待 RSSHub 就绪..."
        RSS_OK=""
        for i in $(seq 1 15); do
            if curl -s -o /dev/null --max-time 3 http://localhost:1200/; then RSS_OK=1; break; fi
            sleep 2
        done
        if [ -n "$RSS_OK" ]; then
            echo "[✓] RSSHub 已就绪 → http://localhost:1200"
        else
            echo "[!] RSSHub 未就绪，部分新闻源将降级（可稍后手动: docker start rsshub）"
        fi
    fi
else
    echo "[-] 未安装 colima，跳过 RSSHub（部分新闻源不可用）"
fi

# ---------- 3. 清理端口占用 ----------
PIDS=$(lsof -nP -tiTCP:$FLASK_PORT -sTCP:LISTEN 2>/dev/null)
for pid in $PIDS; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null)
    case "$cmd" in
        *server.py*) ;;  # 我们自己的服务，保留
        *vite*|*"npm run dev"*)
            echo "[!] 发现残留的 dev server (PID $pid) 抢占端口，已清理"
            kill "$pid" 2>/dev/null ;;
        *)
            echo "[✗] 端口 $FLASK_PORT 被未知进程占用: PID $pid ($cmd)"
            echo "    请先执行: kill $pid"
            exit 1 ;;
    esac
done
sleep 1

# ---------- 4. 后台启动 ----------
PLIST="$HOME/Library/LaunchAgents/com.kun.marketradar.plist"
AGENT_DOMAIN="gui/$(id -u)"

if [ -f "$PLIST" ]; then
    # 优先走 launchd 系统服务：权限稳定、崩溃自动拉起、开机自启
    if launchctl print "$AGENT_DOMAIN/com.kun.marketradar" >/dev/null 2>&1; then
        echo "[…] 系统服务已注册，触发重启..."
        launchctl kickstart -k "$AGENT_DOMAIN/com.kun.marketradar" 2>/dev/null
    else
        echo "[…] 注册并启动系统服务（launchd）..."
        launchctl bootstrap "$AGENT_DOMAIN" "$PLIST" 2>/dev/null \
            || launchctl kickstart -k "$AGENT_DOMAIN/com.kun.marketradar" 2>/dev/null
    fi
    NEW_PID="(launchd 托管)"
else
    nohup "$PY" server.py >> "$LOG_FILE" 2>&1 &
    NEW_PID=$!
    disown 2>/dev/null || true
fi

echo "[…] 等待服务就绪..."
ok=""
for i in $(seq 1 30); do
    if api_ok; then ok=1; break; fi
    sleep 2
done

echo "=============================="
if [ -n "$ok" ]; then
    echo "[✓] 启动成功"
    echo "    访问地址: http://localhost:$FLASK_PORT"
    echo "    进程 PID: $NEW_PID"
    echo "    日志文件: $LOG_FILE"
    echo "    停止方式: bash stop.sh"
    # macOS 下自动打开浏览器
    [ "$(uname)" = "Darwin" ] && open "http://localhost:$FLASK_PORT" 2>/dev/null || true
else
    echo "[✗] 启动失败，最近日志："
    tail -20 "$LOG_FILE"
    exit 1
fi
echo "=============================="
