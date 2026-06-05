#!/bin/bash
# Market Radar 启动脚本
# 用法: bash start.sh

ENV_FILE="$(dirname "$0")/.env.local"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs)
fi

FLASK_PORT=${FLASK_PORT:-20026}

# playwright/chromium 依赖 nss/nspr，在 conda 环境中这些库不在系统路径
# 自动从 conda pkgs 里找并加入 LD_LIBRARY_PATH
_conda_lib_dirs=$(find "$CONDA_PREFIX" "$HOME/miniconda3/pkgs" "$HOME/anaconda3/pkgs" \
    -maxdepth 4 -name "libnspr4.so" 2>/dev/null \
    | xargs -I{} dirname {} | sort -u | tr '\n' ':' 2>/dev/null)
if [ -n "$_conda_lib_dirs" ]; then
    export LD_LIBRARY_PATH="${_conda_lib_dirs}${LD_LIBRARY_PATH}"
fi

echo "=============================="
echo " Market Radar 启动"
echo " 访问地址: http://0.0.0.0:$FLASK_PORT"
echo "=============================="

python server.py
