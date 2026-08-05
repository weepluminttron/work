import sqlite3
import threading
import atexit
import os
import time
from datetime import datetime
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from config import MEMORY_DB_PATH, REVIEW_PUSH_HOUR, FEISHU_WEBHOOK

DB_LOCK = threading.Lock()

# 模块级复用连接，避免每次操作都新建连接
_db_conn = None

def get_db_conn():
    """获取数据库连接（进程内复用一条连接），开启WAL + 超时，多线程安全"""
    global _db_conn
    if _db_conn is None:
        conn = sqlite3.connect(MEMORY_DB_PATH, timeout=20.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA wal_autocheckpoint = 1000;")
        _db_conn = conn
    return _db_conn

def init_memory_db():
    """初始化学习进度数据表"""
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()

        # 学习进度打卡表
        cur.execute("""
        CREATE TABLE IF NOT EXISTS study_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_id INTEGER,
            subject TEXT,
            day_no INTEGER,
            task_content TEXT,
            complete_date TEXT,
            finished INTEGER DEFAULT 0,
            UNIQUE(archive_id, day_no)
        )
        """)

        conn.commit()

# ===================== 打卡系统新增函数 =====================
def save_daily_tasks(archive_id: int, subject: str, task_list: list):
    """保存拆分后的每日任务到进度表"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        for idx, task in enumerate(task_list, start=1):
            cur.execute("""
            INSERT OR IGNORE INTO study_progress
            (archive_id, subject, day_no, task_content)
            VALUES (?, ?, ?, ?)
            """, (archive_id, subject, idx, task))
        conn.commit()

def get_archive_progress(archive_id: int):
    """获取归档文档全部学习进度"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT day_no, task_content, finished, complete_date
        FROM study_progress
        WHERE archive_id = ?
        ORDER BY day_no ASC
        """, (archive_id,))
        rows = cur.fetchall()
    return rows

def mark_task_finished(archive_id: int, day_num: int):
    """标记第N天任务完成"""
    init_memory_db()
    today = datetime.now().strftime("%Y-%m-%d")
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
        UPDATE study_progress
        SET finished = 1, complete_date = ?
        WHERE archive_id = ? AND day_no = ?
        """, (today, archive_id, day_num))
        conn.commit()
        affected = cur.rowcount
    return affected > 0

def get_today_learning_tasks():
    """获取今天该做的任务：每个归档只显示当前最靠前未完成的那一天"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT archive_id, subject, day_no, task_content
        FROM study_progress
        WHERE finished = 0
        ORDER BY archive_id ASC, day_no ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return "🎉今日任务已全部完成！可以看看「额外任务」拓展学习"

    # 每个归档只保留当前最早未完成的那一天
    current = {}
    for r in rows:
        key = r["archive_id"]
        if key not in current or r["day_no"] < current[key]["day_no"]:
            current[key] = {"subject": r["subject"], "day_no": r["day_no"], "tasks": [r["task_content"]]}
        elif r["day_no"] == current[key]["day_no"]:
            current[key]["tasks"].append(r["task_content"])

    output = "📌今日待办：\n"
    for key in sorted(current):
        item = current[key]
        output += f"【归档ID{key}】{item['subject']} Day{item['day_no']}：\n"
        for idx, t in enumerate(item["tasks"], 1):
            output += f"{idx}. {t}\n"
    output += "\n💡做完后发送 /done id 归档ID day 天数 打卡，或用「额外任务」拓展学习"
    return output

def get_extra_learning_tasks():
    """额外任务：今日任务之后的可拓展学习内容"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT archive_id, subject, day_no, task_content
        FROM study_progress
        WHERE finished = 0
        ORDER BY archive_id ASC, day_no ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return "🎉所有任务都已完成，暂无额外任务"

    current_days = {}
    for r in rows:
        key = r["archive_id"]
        current_days[key] = min(current_days.get(key, r["day_no"]), r["day_no"])

    extra = [r for r in rows if r["day_no"] > current_days[r["archive_id"]]]
    if not extra:
        return "📌今日任务还没完成，完成后再来看「额外任务」拓展学习"

    output = "🚀额外任务（今日任务完成后拓展学习）：\n"
    current_archive = None
    for r in extra:
        if r["archive_id"] != current_archive:
            output += f"\n【归档ID{r['archive_id']}】{r['subject']}\n"
            current_archive = r["archive_id"]
        output += f"Day{r['day_no']}：{r['task_content']}\n"
    output += "\n💡这些是今天之后的内容，学有余力时提前预习"
    return output

# ===================== 晨间推送：今日学习任务 =====================
def push_daily_tasks_to_feishu():
    """定时任务：每日早上推送今日待办学习任务"""
    try:
        task_text = get_today_learning_tasks()
        webhook = FEISHU_WEBHOOK

        if not webhook or webhook.startswith("在此处填入") or len(webhook) < 10:
            print("⚠️未配置FEISHU_WEBHOOK，跳过定时推送")
            return

        full_msg = f"""⏰【晨间学习提醒】
{task_text}

💡指令提示：
/daily id 归档ID → 生成每日学习清单
/done id 归档ID day N → 打卡完成任务
/progress id 归档ID → 查看学习进度
"""
        # 超长截断保护
        if len(full_msg) > 4500:
            full_msg = full_msg[:4500] + "\n……内容过长，请在机器人内发送指令查看完整信息"

        payload = {
            "msg_type": "text",
            "content": {"text": full_msg}
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        print("✅晨间任务推送完成", resp.json())
    except Exception as e:
        import traceback
        print("❌定时推送异常：", str(e))
        print(traceback.format_exc())

# 全局调度器单例
_scheduler = None

def start_review_scheduler():
    global _scheduler
    init_memory_db()
    # 解决Flask debug模式重复启动调度器
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print("ℹ️检测到werkzeug重载进程，跳过调度器启动")
        return _scheduler

    if _scheduler and _scheduler.running:
        print("定时任务已运行，无需重复启动")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        push_daily_tasks_to_feishu,
        "cron",
        hour=REVIEW_PUSH_HOUR,
        minute=0,
        coalesce=True,
        misfire_grace_time=300
    )
    _scheduler.start()
    print(f"⏰ 每日晨间任务推送调度器启动【Asia/Shanghai】，每日{REVIEW_PUSH_HOUR}:00推送")
    return _scheduler

def shutdown_scheduler():
    """安全关闭调度器，程序退出自动调用"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("✅定时调度器正常关闭")

atexit.register(shutdown_scheduler)
init_memory_db()

if __name__ == "__main__":
    start_review_scheduler()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        shutdown_scheduler()
