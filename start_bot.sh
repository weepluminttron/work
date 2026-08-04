#!/bin/bash
cd /data/study_agent
source /home/ubuntu/bot_env
# 每100请求自动重启worker，限制线程，超时3分钟
exec gunicorn \
    --bind 0.0.0.0 \
    --workers 1 \
    --threads 3 \
    --timeout 180 \
    --max-requests 100 \
    --log-level info \
    feishu_bot:app
