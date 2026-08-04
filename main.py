import threading
import time
import signal
import sys
from folder_watcher import start_folder_watcher
from review_scheduler import start_review_scheduler, shutdown_scheduler
from feishu_bot import start_feishu_bot
# 新增导入数据库初始化
from archive_db import init_db

# 全局组件引用，用于退出时回收资源
exit_flag = threading.Event()

def signal_handler(sig, frame):
    """捕获Ctrl+C，优雅关闭所有服务"""
    print("\n\n🛑 收到退出信号，正在安全关闭所有服务...")
    exit_flag.set()

    # 关闭复习定时调度器
    shutdown_scheduler()
    print("✅ 定时复习调度器已停止")

    print("✅ 所有后台组件清理完成，程序即将退出")
    time.sleep(1.5)
    sys.exit(0)

def watcher_worker():
    """文件夹监听线程封装"""
    try:
        start_folder_watcher()
    except Exception as e:
        if not exit_flag.is_set():
            print(f"❌ 文件监听异常退出: {e}")

def background_service():
    global scheduler
    # 启动FSRS定时复习调度
    scheduler = start_review_scheduler()
    print("✅ 定时复习任务启动成功")

    # 启动文件夹监控（独立线程）
    watch_thread = threading.Thread(target=watcher_worker, daemon=False)
    watch_thread.start()
    print("✅ 本地文件夹监听启动成功")

    # 阻塞等待退出信号
    exit_flag.wait()

if __name__ == "__main__":
    # 注册 Ctrl+C 信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("===== 数字学伴 启动初始化 =====")
    # 主线程优先初始化数据库！！
    init_db()

    # 后台线程：文件监控、每日定时任务
    bg_thread = threading.Thread(target=background_service, daemon=False)
    bg_thread.start()
    time.sleep(1) # 错开初始化，避免sqlite并发锁

    # 启动飞书机器人Flask服务
    bot_thread = threading.Thread(target=start_feishu_bot, daemon=False)
    bot_thread.start()

    print("\n🚀【数字学伴】全部模块启动成功！")
    print("💡 数据流：")
    print("   1. PDF放入 ~/Downloads/课程资料 → 自动生成Markdown笔记")
    print("   2. 飞书发送PDF → 自动归档、生成习题")
    print("   3. 每日早8点自动推送复习计划；指令 /review 手动获取复习方案")
    print("⚠️  按 Ctrl+C 安全退出程序")

    # 主线程持续等待退出信号
    while not exit_flag.is_set():
        time.sleep(1)
