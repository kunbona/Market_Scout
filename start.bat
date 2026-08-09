@echo off
setlocal EnableDelayedExpansion

echo ==============================
echo  Market Radar  start.bat
echo ==============================
echo.

:: read .env.local
set FLASK_PORT=20026
set RSSHUB_PORT=1200
set QUANT_DATA_ROOT=
set QUANT_WORKERS=
set CONDA_ENV_NAME=
set CONDA_BAT=
set PYTHON_EXECUTABLE=
set PYTHON_EXE=python

if exist "%~dp0.env.local" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env.local") do (
        set "_key=%%A"
        set "_val=%%B"
        if "!_key!"=="FLASK_PORT"      set FLASK_PORT=!_val!
        if "!_key!"=="RSSHUB_PORT"     set RSSHUB_PORT=!_val!
        if "!_key!"=="QUANT_DATA_ROOT" set QUANT_DATA_ROOT=!_val!
        if "!_key!"=="QUANT_WORKERS"   set QUANT_WORKERS=!_val!
        if "!_key!"=="CONDA_ENV_NAME"  set CONDA_ENV_NAME=!_val!
        if "!_key!"=="CONDA_BAT"       set CONDA_BAT=!_val!
        if "!_key!"=="PYTHON_EXECUTABLE" set PYTHON_EXECUTABLE=!_val!
    )
    echo [OK] .env.local loaded
) else (
    echo [!] .env.local not found, using defaults
    echo     Copy .env.example to .env.local to customize settings
)
echo.

:: check WSL2
wsl --status >nul 2>&1
if errorlevel 1 (
    echo [!] WSL2 not found, skipping RSSHub startup
    echo.
    goto :start_flask
)

:: check Docker in WSL2
wsl docker info >nul 2>&1
if errorlevel 1 (
    echo [!] Docker not found in WSL2, skipping RSSHub startup
    echo.
    goto :start_flask
)

:: start RSSHub
echo [*] Checking RSSHub container...
wsl docker inspect rsshub >nul 2>&1
if errorlevel 1 (
    echo [*] Creating RSSHub container...
    wsl docker run -d --name rsshub --restart unless-stopped ^
        -p %RSSHUB_PORT%:%RSSHUB_PORT% ^
        -e NODE_ENV=production ^
        -e CACHE_TYPE=memory ^
        diygod/rsshub
    if errorlevel 1 (
        echo [!] RSSHub failed to start
        goto :start_flask
    )
    echo [OK] RSSHub container created
) else (
    for /f %%S in ('wsl docker inspect --format "{{.State.Running}}" rsshub 2^>nul') do set RSSHUB_RUNNING=%%S
    if "!RSSHUB_RUNNING!"=="true" (
        echo [OK] RSSHub already running
    ) else (
        echo [*] Starting RSSHub container...
        wsl docker start rsshub
        echo [OK] RSSHub started
    )
)

:: get WSL IP for direct access (bypass WSL localhost relay)
for /f %%I in ('wsl hostname -I 2^>nul') do set WSL_IP=%%I
if defined WSL_IP (
    set RSSHUB_BASE_URL=http://!WSL_IP!:%RSSHUB_PORT%
    echo [*] WSL IP: !WSL_IP!
) else (
    set RSSHUB_BASE_URL=http://localhost:%RSSHUB_PORT%
)

:: wait for RSSHub
echo [*] Waiting for RSSHub...
set RSSHUB_READY=0
for /l %%i in (1,1,15) do (
    if !RSSHUB_READY!==0 (
        powershell -Command "try { Invoke-WebRequest !RSSHUB_BASE_URL!/ -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
        if not errorlevel 1 set RSSHUB_READY=1
        if !RSSHUB_READY!==0 timeout /t 1 /nobreak >nul
    )
)
if "!RSSHUB_READY!"=="1" (
    echo [OK] RSSHub ready: !RSSHUB_BASE_URL!
) else (
    echo [!] RSSHub not ready after 15s, continuing anyway
)
echo.

:start_flask
if defined PYTHON_EXECUTABLE (
    set "PYTHON_EXE=!PYTHON_EXECUTABLE!"
    echo [OK] Using configured Python executable: !PYTHON_EXE!
) else (
    if defined CONDA_ENV_NAME (
        call :resolve_conda_bat
        if not defined CONDA_BAT_FOUND (
            echo [ERROR] Unable to locate conda.bat. Set CONDA_BAT in .env.local.
            pause
            exit /b 1
        )
        echo [*] Activating conda env: !CONDA_ENV_NAME!
        call "!CONDA_BAT_FOUND!" activate "!CONDA_ENV_NAME!"
        if errorlevel 1 (
            echo [ERROR] Failed to activate conda env: !CONDA_ENV_NAME!
            pause
            exit /b 1
        )
        echo [OK] Conda env activated: !CONDA_ENV_NAME!
        echo.
    )
)

:: check Python
call "!PYTHON_EXE!" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: check frontend build
if not exist "%~dp0dashboard\dist\index.html" (
    echo [!] Frontend build not found
    echo     Run: cd dashboard ^&^& npm install ^&^& npm run build
    echo.
)

:: bypass proxy for domestic requests
set NO_PROXY=localhost,127.*,*.eastmoney.com,*.akshare.xyz,push2.eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com

:: QMT hint
echo [*] QMT: if QMT_ENABLED=true, ensure miniQMT client is running.
echo     Market breadth data will be skipped if QMT is unavailable.
echo.

:: start Flask
echo [*] Starting Market Radar...
echo     URL: http://localhost:%FLASK_PORT%
echo     Press Ctrl+C to stop
echo.

cd /d "%~dp0"
call "!PYTHON_EXE!" server.py

pause
goto :eof

:resolve_conda_bat
set CONDA_BAT_FOUND=
if defined CONDA_BAT if exist "!CONDA_BAT!" set "CONDA_BAT_FOUND=!CONDA_BAT!"
if defined CONDA_BAT_FOUND goto :eof

for /f "delims=" %%I in ('where conda.bat 2^>nul') do (
    set "CONDA_BAT_FOUND=%%I"
    goto :eof
)

if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BAT_FOUND=%USERPROFILE%\miniconda3\condabin\conda.bat"
if defined CONDA_BAT_FOUND goto :eof

if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BAT_FOUND=%USERPROFILE%\anaconda3\condabin\conda.bat"
goto :eof
