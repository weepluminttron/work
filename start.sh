#!/bin/bash
# ============================================================
# 一键后台启动：飞书机器人 + 每日定时推送 + 文件夹监听
# 用法: ./start.sh
# 日志目录: /data/study_agent/logs/（bot.log / scheduler.log / watcher.log）
# ============================================================
set -e

APP_DIR="/data/study_agent"
VENV="/home/ubuntu/bot_env"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
source "$VENV/bin/activate"
# 让 Python 日志实时写入文件（默认重定向后会缓冲，看不到输出）
export PYTHONUNBUFFERED=1
cd "$APP_DIR"

start_service() {
    local name="$1"
    local pidfile="$LOG_DIR/$2.pid"
    local cmd="$3"

    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "✅ $name 已在运行 (PID $(cat "$pidfile"))"
    else
        # shellcheck disable=SC2086
        nohup $cmd > "$LOG_DIR/$2.log" 2>&1 &
        echo $! > "$pidfile"
        echo "🚀 $name 已启动 (PID $(cat "$pidfile"))"
    fi
}

# 1) 飞书机器人（gunicorn，端口 8080）
start_service "飞书机器人" bot \
    "gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 3 --timeout 180 --max-requests 100 --capture-output feishu_bot:app"

# 2) 每日早 8 点复习推送
start_service "每日定时推送" scheduler "python review_scheduler.py"

# 3) 文件夹自动监听（不需要可以删掉这一段）
start_service "文件夹监听" watcher "python folder_watcher.py"

# 4) 网页版学习助手（手机/电脑浏览器访问，端口 8090）
start_service "网页版学习助手" web "gunicorn --bind 0.0.0.0:8090 --workers 1 --threads 4 --timeout 600 web_app:app"

echo ""
echo "🎉 全部服务已在后台运行，日志目录: $LOG_DIR"
echo "    查看日志: tail -f $LOG_DIR/bot.log"
