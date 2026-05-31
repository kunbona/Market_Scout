@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ============================================================
::  Market Radar — Windows 启动脚本
::  用途：在 WSL2 中启动 RSSHub（Docker），同时在 Windows 侧
::        启动 Flask 后端（Python）。
::
::  前提条件：
::    1. 已安装 WSL2（Ubuntu 或其他发行版）
::    2. WSL2 内已安装 Docker Engine，或已安装 Docker Desktop
::       并在设置中启用了 WSL2 后端
::    3. Windows 侧已安装 Python 3.10+，且 pip install 已完成
::    4. 已执行过 "cd dashboard && npm install && npm run build"
::
::  用法：双击运行，或在 cmd / PowerShell 中执行：
::    start.bat
:: ============================================================

echo ==============================
echo  Market Radar Windows 启动脚本
echo ==============================
echo.

:: ── 读取 .env.local ─────────────────────────────────────────
set FLASK_PORT=20026
set RSSHUB_PORT=1200
set QUANT_DATA_ROOT=
set QUANT_WORKERS=

if exist "%~dp0.env.local" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env.local") do (
        set "_key=%%A"
        set "_val=%%B"
        :: 跳过注释行和空行
        if not "!_key:~0,1!"=="#" (
            if defined _key (
                if "!_key!"=="FLASK_PORT"      set FLASK_PORT=!_val!
                if "!_key!"=="RSSHUB_PORT"     set RSSHUB_PORT=!_val!
                if "!_key!"=="QUANT_DATA_ROOT" set QUANT_DATA_ROOT=!_val!
                if "!_key!"=="QUANT_WORKERS"   set QUANT_WORKERS=!_val!
            )
        )
    )
    echo [OK] 已读取 .env.local
) else (
    echo [!] 未找到 .env.local，使用默认配置
    echo     参考 .env.example 创建 .env.local 以自定义路径和端口
)
echo.

:: ── 检查 WSL2 是否可用 ───────────────────────────────────────
wsl --status >nul 2>&1
if errorlevel 1 (
    echo [!] 未检测到 WSL2，跳过 RSSHub 启动
    echo     RSS 相关数据源将自动降级，不影响其他功能
    echo.
    goto :start_flask
)

:: ── 检查 WSL2 内 Docker 是否可用 ────────────────────────────
wsl docker info >nul 2>&1
if errorlevel 1 (
    echo [!] WSL2 内未检测到 Docker，跳过 RSSHub 启动
    echo     请在 WSL2 中安装 Docker Engine，或启动 Docker Desktop
    echo.
    goto :start_flask
)

:: ── 启动 RSSHub（若容器不存在则创建，若已停止则启动）────────
echo [*] 正在检查 RSSHub 容器状态...
wsl docker inspect rsshub >nul 2>&1
if errorlevel 1 (
    echo [*] RSSHub 容器不存在，正在创建并启动...
    wsl docker run -d --name rsshub --restart unless-stopped ^
        -p %RSSHUB_PORT%:%RSSHUB_PORT% ^
        -e NODE_ENV=production ^
        -e CACHE_TYPE=memory ^
        diygod/rsshub
    if errorlevel 1 (
        echo [!] RSSHub 启动失败，RSS 数据源将降级
        echo.
        goto :start_flask
    )
    echo [OK] RSSHub 容器已创建并启动
) else (
    :: 检查容器是否在运行
    for /f %%S in ('wsl docker inspect --format "{{.State.Running}}" rsshub 2^>nul') do set RSSHUB_RUNNING=%%S
    if "!RSSHUB_RUNNING!"=="true" (
        echo [OK] RSSHub 已在运行
    ) else (
        echo [*] 正在启动已有的 RSSHub 容器...
        wsl docker start rsshub
        if errorlevel 1 (
            echo [!] RSSHub 启动失败，RSS 数据源将降级
            goto :start_flask
        )
        echo [OK] RSSHub 已启动
    )
)

:: 等待 RSSHub 就绪（最多 15 秒）
echo [*] 等待 RSSHub 就绪...
set RSSHUB_READY=0
for /l %%i in (1,1,15) do (
    if !RSSHUB_READY!==0 (
        wsl curl -sf http://localhost:%RSSHUB_PORT%/ >nul 2>&1
        if not errorlevel 1 (
            set RSSHUB_READY=1
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if "!RSSHUB_READY!"=="1" (
    echo [OK] RSSHub 可用：http://localhost:%RSSHUB_PORT%
) else (
    echo [!] RSSHub 15 秒内未响应，继续启动（RSS 数据源可能暂时不可用）
)
echo.

:start_flask
:: ── 检查 Python ──────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

:: ── 检查前端构建产物 ─────────────────────────────────────────
if not exist "%~dp0dashboard\dist\index.html" (
    echo [!] 未找到前端构建产物 dashboard/dist/index.html
    echo     请先执行：cd dashboard ^&^& npm install ^&^& npm run build
    echo.
)

:: ── 设置环境变量供 Python 读取 ────────────────────────────────
if defined QUANT_WORKERS (
    set QUANT_WORKERS=%QUANT_WORKERS%
)

:: ── 启动 Flask ───────────────────────────────────────────────
echo [*] 正在启动 Market Radar...
echo     端口: http://localhost:%FLASK_PORT%
echo     按 Ctrl+C 停止服务
echo.

cd /d "%~dp0"
python server.py

pause
