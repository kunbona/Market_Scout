@echo off
REM QMT Bridge 启动脚本（Windows VM 端）
REM
REM 用法：
REM   1. 第一次使用：编辑下方 QMT_BRIDGE_TOKEN，设一个长随机串
REM   2. 双击运行；或放到 Windows 启动文件夹实现开机自启
REM      启动文件夹：Win+R → shell:startup → 把本文件快捷方式丢进去
REM   3. 高级自启：用 Windows 任务计划程序（搜索"任务计划"），
REM      触发器选"登录时"，操作选"启动程序"指向本 .bat
REM
REM 防火墙首次启动会弹窗询问是否允许 5001 端口，勾选"允许"。

setlocal

REM === 必填：跟 Mac 端 .env.local 里的 QMT_BRIDGE_TOKEN 完全一致 ===
set QMT_BRIDGE_TOKEN=PLEASE_CHANGE_ME_TO_A_LONG_RANDOM_STRING

REM === 可选：监听地址（默认 0.0.0.0 接受同网段所有 IP） ===
REM set BRIDGE_HOST=0.0.0.0

REM === 可选：监听端口（跟 Mac 端 QMT_BRIDGE_URL 里的端口一致） ===
set BRIDGE_PORT=5001

REM === 可选：QMT 安装根目录（xtquant 自动从 QMT 客户端加载，可不设） ===
REM set QMT_PATH=D:\Software\东北证券NET专业版

REM 切到脚本所在目录
cd /d "%~dp0"

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not in PATH. Install Python 3.10+ and retry.
    pause
    exit /b 1
)

REM 启动服务（窗口最小化；日志会直接输出到这里）
echo Starting QMT Bridge on port %BRIDGE_PORT%...
echo Token: %QMT_BRIDGE_TOKEN:~0,8%...（已脱敏）
echo Press Ctrl+C to stop.
echo.

python server.py --port %BRIDGE_PORT%

endlocal
