#!/bin/bash
# Market Radar 启动脚本
# 用法: bash start.sh

ENV_FILE="$(dirname "$0")/.env.local"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs)
fi

FLASK_PORT=${FLASK_PORT:-20026}

echo "=============================="
echo " Market Radar 启动"
echo " 访问地址: http://0.0.0.0:$FLASK_PORT"
echo "=============================="

python server.py
