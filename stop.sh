#!/bin/bash
# Market Radar 一键停止脚本
# 用法: bash stop.sh        （终端）
# 或直接双击 "停止 Market Radar.command"
#
# 只停止 Market Radar 主服务（server.py）。
# colima / RSSHub 默认保留运行（它们还服务其他容器，且留着下次启动更快）；
# 加 --all 参数可连 colima 一起关: bash stop.sh --all

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLASK_PORT=${FLASK_PORT:-20026}

echo "=============================="
echo " Market Radar 一键停止"
echo "=============================="

stopped=""
PLIST="$HOME/Library/LaunchAgents/com.kun.marketradar.plist"
AGENT_LABEL="com.kun.marketradar"
AGENT_DOMAIN="gui/$(id -u)"

# 优先卸载 launchd 系统服务（否则 KeepAlive 会立刻把进程拉回来）
if [ -f "$PLIST" ] && launchctl print "$AGENT_DOMAIN/$AGENT_LABEL" >/dev/null 2>&1; then
    launchctl bootout "$AGENT_DOMAIN/$AGENT_LABEL" 2>/dev/null \
        || launchctl bootout "$AGENT_DOMAIN" "$PLIST" 2>/dev/null
    echo "[✓] 已停止系统服务（本次会话内不再自动拉起；重启 Mac 后仍会自启）"
    stopped=1
fi

# 按命令行匹配 server.py（只杀本项目的进程）
for pid in $(pgrep -f "server\.py" 2>/dev/null); do
    # 确认这个进程确实在本项目目录下运行
    if lsof -p "$pid" 2>/dev/null | grep -q "$SCRIPT_DIR"; then
        kill "$pid" 2>/dev/null && stopped=1
        echo "[✓] 已停止 server.py (PID $pid)"
    fi
done

# 兜底：端口仍被本项目进程占用则再清一次
sleep 1
for pid in $(lsof -nP -tiTCP:$FLASK_PORT -sTCP:LISTEN 2>/dev/null); do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null)
    case "$cmd" in
        *server.py*)
            kill "$pid" 2>/dev/null && stopped=1
            echo "[✓] 已停止端口占用进程 (PID $pid)" ;;
    esac
done

if [ -z "$stopped" ]; then
    echo "[-] 服务本来就没在运行"
fi

# 可选：连 colima 一起关
if [ "$1" = "--all" ]; then
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx rsshub; then
        docker stop rsshub >/dev/null 2>&1 && echo "[✓] RSSHub 容器已停止"
    fi
    if command -v colima >/dev/null 2>&1 && colima status >/dev/null 2>&1; then
        colima stop >/dev/null 2>&1 && echo "[✓] colima 虚拟机已停止"
    fi
else
    echo "[-] colima / RSSHub 保持运行（想一起关: bash stop.sh --all）"
fi

echo "=============================="
