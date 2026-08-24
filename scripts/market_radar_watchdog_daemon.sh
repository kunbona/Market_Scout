#!/bin/bash
# market-radar watchdog 守护进程（常驻版）。
#
# 由 server.py 启动时 detached spawn（start_new_session 独立进程组），
# 不依赖 launchd bootstrap / cron（两者在当前环境不可用）。
# 定期 curl 健康检查，连续失败 MAX_FAIL 次则 launchctl kickstart 重启 server。
#
# 关键设计：
#   - pidfile 互斥：server 每次重启会重新 spawn，旧 watchdog 若还活着则新进程直接退出，防累积。
#   - 冷却期：kickstart 后 COOLDOWN_SEC 内不重复动作，避免 server 启动 warm-up(45-90s) 期间误判反复重启。
#   - detached：server 假死/被杀时，本进程独立存活，继续守护。

PORT=20026
SERVICE="gui/$(id -u)/com.kun.marketradar"
PIDFILE="/tmp/mra-watchdog-daemon.pid"
LOG="/tmp/mra-watchdog.log"
MAX_FAIL=3        # 连续失败次数阈值（× CHECK_SEC = 3 分钟）
CHECK_SEC=60      # 检查间隔
COOLDOWN_SEC=180  # kickstart 冷却期

# ── pidfile 互斥 ──
if [ -f "$PIDFILE" ]; then
    _old=$(tr -dc '0-9' < "$PIDFILE" 2>/dev/null)
    if [ -n "$_old" ] && kill -0 "$_old" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$PIDFILE"

fail=0
last_kick=0

while true; do
    code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "http://localhost:${PORT}/" 2>/dev/null)
    if [ "$code" = "200" ]; then
        fail=0
    else
        fail=$((fail + 1))
        now=$(date +%s)
        if [ "$fail" -ge "$MAX_FAIL" ] && [ $((now - last_kick)) -ge "$COOLDOWN_SEC" ]; then
            launchctl kickstart -k "$SERVICE" 2>/dev/null
            echo "$(date '+%F %T') watchdog: server 连续 ${fail} 次不健康，已 kickstart 重启" >> "$LOG"
            fail=0
            last_kick=$now
        fi
    fi
    sleep "$CHECK_SEC"
done
