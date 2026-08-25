#!/bin/bash
# market-radar watchdog 守护进程（常驻版）v2
#
# 由 server.py 启动时 detached spawn（start_new_session 独立进程组），
# 不依赖 launchd bootstrap / cron（两者在当前环境不可用）。
# 定期健康检查（HTTP），连续失败 MAX_FAIL 次 → 重启 server，并**验证重启结果**。
#
# v2 修复 (2026-08-25, 源于一次真实宕机事故):
#   Bug 1  健康检查 curl 未绕过代理: 带 HTTP_PROXY 的环境(如 WorkBuddy 沙箱)里
#          请求被代理劫持恒返 502 → 健康服务被误判不健康(实测误报 2+ 小时)。
#          修复: curl 一律加 --noproxy '*'。
#   Bug 2  重启动作盲信 launchctl kickstart: com.kun.marketradar 服务实际未注册
#          (沙箱里 bootstrap 恒报 I/O error), kickstart 每次静默失败,
#          日志却照写"已重启" → 服务宕机 20+ 分钟无人救起。
#          修复: 先查服务是否注册; 未注册/kickstart 后未恢复健康,
#          回退到 watchdog 直接 nohup 拉起 server.py (detached 子进程, 不依赖会话)。
#   Bug 3  重启结果无验证。修复: 重启后最长等 150s 健康检查,
#          通过才记"重启成功", 否则记"重启失败"。
#
# 保留设计:
#   - pidfile 互斥: server 每次重启会重新 spawn, 旧 watchdog 若还活着则新进程直接退出, 防累积。
#   - 冷却期: 重启后 COOLDOWN_SEC 内不重复动作, 避免 server 启动 warm-up(45-90s) 期间误判反复重启。
#   - detached: server 假死/被杀时, 本进程独立存活, 继续守护。

PORT=20026
SERVICE="gui/$(id -u)/com.kun.marketradar"
PIDFILE="/tmp/mra-watchdog-daemon.pid"
LOG="/tmp/mra-watchdog.log"
MAX_FAIL=3        # 连续失败次数阈值（× CHECK_SEC = 3 分钟）
CHECK_SEC=60      # 检查间隔
COOLDOWN_SEC=180  # 重启冷却期（覆盖 server warmup）

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$PROJECT_ROOT/.venv/bin/python"
SERVER_LOG="${MR_LOG:-/tmp/mr_server.log}"

log() { echo "$(date '+%F %T') watchdog: $*" >> "$LOG"; }

health_ok() {
    # --noproxy '*': 防止代理劫持造成误判 (v2 修复 Bug 1)
    local code
    code=$(curl -s -o /dev/null --noproxy '*' -m 5 -w "%{http_code}" \
        "http://localhost:${PORT}/api/config" 2>/dev/null)
    [ "$code" = "200" ]
}

wait_healthy() {  # $1 = 最长等待秒数; 健康返回 0
    local deadline=$(( $(date +%s) + $1 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        health_ok && return 0
        sleep 5
    done
    health_ok
}

restart_server() {
    # 路径 1: launchd 服务确实已注册 → kickstart
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
        launchctl kickstart -k "$SERVICE" >/dev/null 2>&1
        wait_healthy 90 && return 0
        log "launchd kickstart 后未恢复健康, 回退 nohup 直启"
    fi
    # 路径 2: watchdog 直接拉起 server.py (v2 修复 Bug 2)
    # 进程活着但端口不应答 → 假死, 先杀再起
    if pgrep -f "python.*server\.py" >/dev/null 2>&1; then
        pkill -9 -f "python.*server\.py" 2>/dev/null
        sleep 2
    fi
    # < /dev/null: 控制终端/会话关闭后 stdin 变坏,
    # Python 解释器初始化会 Fatal: Bad file descriptor
    ( cd "$PROJECT_ROOT" && nohup "$PY" server.py < /dev/null >> "$SERVER_LOG" 2>&1 & )
    wait_healthy 150
}

# ── pidfile 互斥 ──
if [ -f "$PIDFILE" ]; then
    _old=$(tr -dc '0-9' < "$PIDFILE" 2>/dev/null)
    if [ -n "$_old" ] && [ "$_old" != "$$" ] && kill -0 "$_old" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$PIDFILE"

fail=0
last_kick=0
log "v2 已启动 (pid $$, 健康检查: --noproxy + /api/config, 回退: nohup 直启)"

while true; do
    # 防多实例累积 (v2 加固): 若 pidfile 已被别的存活实例改写, 本实例让位退出。
    _cur=$(tr -dc '0-9' < "$PIDFILE" 2>/dev/null)
    if [ -n "$_cur" ] && [ "$_cur" != "$$" ] && kill -0 "$_cur" 2>/dev/null; then
        log "pidfile 已被实例 ${_cur} 接管, 本实例 ($$) 退出"
        exit 0
    fi
    if health_ok; then
        fail=0
    else
        fail=$((fail + 1))
        now=$(date +%s)
        if [ "$fail" -ge "$MAX_FAIL" ] && [ $((now - last_kick)) -ge "$COOLDOWN_SEC" ]; then
            log "server 连续 ${fail} 次不健康, 开始重启..."
            if restart_server; then
                log "重启成功, 服务已恢复"
            else
                log "重启失败! 健康检查仍未通过, 下个冷却期再试"
            fi
            fail=0
            last_kick=$(date +%s)
        fi
    fi
    sleep "$CHECK_SEC"
done
