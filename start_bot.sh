#!/bin/bash
# 前台启动飞书机器人（后台运行请使用 start.sh / stop.sh）
cd /data/study_agent
source /home/ubuntu/bot_env/bin/activate
# 每100请求自动重启worker，限制线程，超时3分钟
exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 1 \
    --threads 3 \
    --timeout 180 \
    --max-requests 100 \
    --log-level info \
    feishu_bot:app
