import sqlite3
import threading
import atexit
import os
import time
from datetime import datetime, timedelta
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from llm_summary import generate_daily_review_plan
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
    """初始化数据表 + 自动迁移字段（解决旧表缺少stability/difficulty报错）"""
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()

        # 记忆卡片表
        cur.execute("""
        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            content_summary TEXT,
            stability REAL DEFAULT 1.0,
            difficulty REAL DEFAULT 2.5,
            last_review TEXT,
            next_review TEXT,
            interval_days INTEGER
        )
        """)

        # 学习进度打卡表【新增】
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

        # 自动迁移旧表字段
        cur.execute("PRAGMA table_info(memory_items);")
        columns = [row[1] for row in cur.fetchall()]
        if "stability" not in columns:
            cur.execute("ALTER TABLE memory_items ADD COLUMN stability REAL DEFAULT 1.0")
        if "difficulty" not in columns:
            cur.execute("ALTER TABLE memory_items ADD COLUMN difficulty REAL DEFAULT 2.5")

        conn.commit()

def calc_fsrs_interval(stability: float, difficulty: float, rating: int):
    """
    简易FSRS间隔计算
    rating: 1=遗忘(Again) 2=困难(Hard) 3=良好(Good) 4=轻松(Easy)
    返回 new_stability, new_difficulty, next_interval_days
    """
    if rating <= 1:
        new_stability = stability * 0.4
        next_interval = 1
    elif rating == 2:
        new_stability = stability * 1.1
        next_interval = max(1, int(stability * 1.1))
    elif rating == 3:
        new_stability = stability * 1.4
        next_interval = max(1, int(stability * 1.4))
    else:  # 4 easy
        new_stability = stability * 1.8
        next_interval = max(1, int(stability * 1.8))

    difficulty_delta = (rating - 2.5) * 0.12
    new_difficulty = max(1.0, min(5.0, difficulty + difficulty_delta))
    next_interval = min(next_interval, 60)
    return round(new_stability, 2), round(new_difficulty, 2), next_interval

def add_memory_item(subject: str, content_summary: str):
    """新增记忆卡片，供feishu机器人 /add 指令调用"""
    init_memory_db()
    now = datetime.now()
    first_interval = 1
    next_review_dt = now + timedelta(days=first_interval)
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO memory_items
        (subject, content_summary, stability, difficulty, last_review, next_review, interval_days)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            subject,
            content_summary,
            1.0,
            2.5,
            now.strftime("%Y-%m-%d"),
            next_review_dt.strftime("%Y-%m-%d"),
            first_interval
        ))
        conn.commit()
    return True

def review_item(item_id: int, rating: int):
    """复习卡片后打分，自动更新下次复习时间"""
    if not (1 <= rating <= 4):
        print("❌评分非法，rating必须是1~4")
        return False
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
        SELECT stability, difficulty FROM memory_items WHERE id = ?
        """, (item_id,))
        row = cur.fetchone()
        if not row:
            return False
        s, d = row
        new_s, new_d, ivl = calc_fsrs_interval(s, d, rating)
        now = datetime.now()
        next_dt = now + timedelta(days=ivl)
        cur.execute("""
        UPDATE memory_items
        SET stability=?, difficulty=?, last_review=?, next_review=?, interval_days=?
        WHERE id = ?
        """, (
            new_s, new_d,
            now.strftime("%Y-%m-%d"),
            next_dt.strftime("%Y-%m-%d"),
            ivl,
            item_id
        ))
        conn.commit()
    return True

def get_all_review_records():
    """获取所有到期卡片（供LLM生成复习计划）"""
    init_memory_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
        SELECT id, subject, content_summary, next_review FROM memory_items
        WHERE next_review <= ?
        ORDER BY next_review ASC, subject ASC
        """, (today_str,))
        rows = cur.fetchall()

    if not rows:
        return "暂无到期复习卡片"

    text = ""
    for row in rows:
        cid, sub, content, next_dt = row
        text += f"卡片ID:{cid} | 科目：{sub}，内容摘要：{content}，下次复习时间：{next_dt}\n"
    return text

def daily_review_task():
    """对外统一入口，feishu_bot直接调用无需改动"""
    records = get_all_review_records()
    plan = generate_daily_review_plan(records)
    print("===== 今日复习推送 =====")
    print(plan)
    return plan

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
    """
    获取今日待完成学习任务
    逻辑：未打卡的所有任务，作为当日待办
    """
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT DISTINCT archive_id, subject, day_no, task_content
        FROM study_progress
        WHERE finished = 0
        ORDER BY archive_id, day_no ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return "📚今日暂无待学习任务\n使用 /daily id xxx 生成学习计划"
    output = "📚今日待学习任务清单：\n"
    for r in rows:
        output += f"【归档ID{r['archive_id']}】{r['subject']} Day{r['day_no']}：{r['task_content']}\n"
    return output

# ===================== 记忆卡片查询 =====================
def get_all_cards():
    """
    获取全部记忆卡片，按复习日期升序
    返回列表 [{"id":xx, "subject":"xx", "content":"xx", "next_review":"yyyy-MM-dd"}]
    """
    init_memory_db()
    result = []
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, subject, content_summary, next_review
            FROM memory_items
            ORDER BY next_review ASC
        """)
        rows = cur.fetchall()

    for r in rows:
        result.append({
            "id": r["id"],
            "subject": r["subject"],
            "content": r["content_summary"],
            "next_review": r["next_review"]
        })
    return result

# =====================【改造】晨间推送：合并复习+学习任务 =====================
def push_daily_review_to_feishu():
    """定时任务：每日早上推送【复习计划 + 今日学习任务】合并消息"""
    try:
        review_text = daily_review_task()
        task_text = get_today_learning_tasks()
        webhook = FEISHU_WEBHOOK

        if not webhook or webhook.startswith("在此处填入") or len(webhook) < 10:
            print("⚠️未配置FEISHU_WEBHOOK，跳过定时推送")
            return

        full_msg = f"""⏰【晨间学习提醒】
🧠今日复习任务：
{review_text}

{task_text}

💡指令提示：
/done id 归档ID day N → 打卡完成任务
/review → 查看完整复习方案
/daily id xxx → 生成每日学习清单
"""
        # 超长截断保护
        if len(full_msg) > 4500:
            full_msg = full_msg[:4500] + "\n……内容过长，请在机器人内发送指令查看完整信息"

        payload = {
            "msg_type": "text",
            "content": {"text": full_msg}
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        print("✅晨间复习&任务推送完成", resp.json())
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
        push_daily_review_to_feishu,
        "cron",
        hour=REVIEW_PUSH_HOUR,
        minute=0,
        coalesce=True,
        misfire_grace_time=300
    )
    _scheduler.start()
    print(f"⏰ 每日晨间推送调度器启动【Asia/Shanghai】，每日{REVIEW_PUSH_HOUR}:00推送")
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
