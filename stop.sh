#!/bin/bash
# ============================================================
# 一键关闭：飞书机器人 + 每日定时推送 + 文件夹监听
# 用法: ./stop.sh
# ============================================================

APP_DIR="/data/study_agent"
LOG_DIR="$APP_DIR/logs"

stop_service() {
    local name="$1"
    local pidfile="$LOG_DIR/$2.pid"
    local pattern="$3"

    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "🛑 已停止 $name (PID $pid)"
        else
            echo "ℹ️  $name 未在运行"
        fi
        rm -f "$pidfile"
    else
        if pkill -f "$pattern" 2>/dev/null; then
            echo "🛑 已停止 $name"
        else
            echo "ℹ️  $name 未在运行"
        fi
    fi
}

stop_service "飞书机器人" bot "gunicorn.*feishu_bot"
stop_service "每日定时推送" scheduler "review_scheduler.py"
stop_service "文件夹监听" watcher "folder_watcher.py"

echo ""
echo "🎉 全部服务已关闭"
