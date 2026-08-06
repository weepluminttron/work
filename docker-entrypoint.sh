#!/bin/bash
# ============================================================
# Docker 容器入口：飞书机器人(8080) + 网页版(8090) + 定时推送
# 数据目录 /app/data（已通过 docker-compose 挂载持久化）
# ============================================================
set -e
export PYTHONUNBUFFERED=1
mkdir -p /app/data/logs
cd /app

echo "🚀 启动学习助手容器..."

# 每日定时推送 + 晚间督促
nohup python review_scheduler.py > /app/data/logs/scheduler.log 2>&1 &

# 飞书机器人（8080）
nohup gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 3 --timeout 180 --max-requests 100 --capture-output feishu_bot:app > /app/data/logs/bot.log 2>&1 &

# 网页版学习助手（8090）
nohup gunicorn --bind 0.0.0.0:8090 --workers 1 --threads 4 --timeout 600 --max-requests 200 --max-requests-jitter 50 web_app:app > /app/data/logs/web.log 2>&1 &

echo "✅ 全部服务已启动，日志目录：/app/data/logs"
echo "   飞书机器人: 8080 | 网页版: 8090"

# 等待任一进程退出后由 docker 重启策略拉起
wait -n
