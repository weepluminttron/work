import sqlite3
import threading
import atexit
import os
import time
from datetime import datetime, timedelta
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from config import MEMORY_DB_PATH, REVIEW_PUSH_HOUR, FEISHU_WEBHOOK
import user_context

DB_LOCK = threading.Lock()

# 每个用户复用一条连接
_db_conns = {}

def get_db_conn():
    """获取当前用户的数据库连接（进程内按用户复用），开启WAL + 超时，多线程安全"""
    path = user_context.scope("memory_spaced_review.db")
    conn = _db_conns.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=20.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA wal_autocheckpoint = 1000;")
        _db_conns[path] = conn
        _init_schema(conn)
    return conn

def _init_schema(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        archive_id INTEGER,
        subject TEXT,
        day_no INTEGER,
        task_content TEXT,
        start_date TEXT,
        complete_date TEXT,
        finished INTEGER DEFAULT 0,
        UNIQUE(archive_id, day_no)
    )
    """)
    cur.execute("PRAGMA table_info(study_progress);")
    columns = [row[1] for row in cur.fetchall()]
    if "start_date" not in columns:
        cur.execute("ALTER TABLE study_progress ADD COLUMN start_date TEXT")
    cur.execute("UPDATE study_progress SET start_date = date('now') WHERE start_date IS NULL")
    conn.commit()

def init_memory_db():
    """初始化学习进度数据表"""
    with DB_LOCK:
        conn = get_db_conn()
        _init_schema(conn)
        conn.commit()

# ===================== 打卡系统新增函数 =====================
def save_daily_tasks(archive_id: int, subject: str, task_list: list):
    """保存拆分后的每日任务到进度表"""
    init_memory_db()
    today = datetime.now().strftime("%Y-%m-%d")
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        for idx, task in enumerate(task_list, start=1):
            cur.execute("""
            INSERT OR IGNORE INTO study_progress
            (archive_id, subject, day_no, task_content, start_date)
            VALUES (?, ?, ?, ?, ?)
            """, (archive_id, subject, idx, task, today))
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
    """获取今天该做的任务：按日历天数计算，未完成的天自动累计到下一天"""
    init_memory_db()
    today_dt = datetime.now().date()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT archive_id, subject, day_no, task_content, start_date
        FROM study_progress
        WHERE finished = 0
        ORDER BY archive_id ASC, day_no ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return "🎉今日任务已全部完成！可以看看「额外任务」拓展学习"

    # 每个归档的“今天是第几天” = 计划开始日期 到 今天的自然日差 + 1
    current_day = {}
    for r in rows:
        key = r["archive_id"]
        if key not in current_day:
            try:
                start = datetime.strptime(r["start_date"], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                start = today_dt
            current_day[key] = max(1, (today_dt - start).days + 1)

    # 按 归档ID+天 分组，未完成且已到期的天都计入今日（自动累计）
    groups = {}
    for r in rows:
        key = r["archive_id"]
        if r["day_no"] > current_day[key]:
            continue
        gkey = (r["archive_id"], r["day_no"])
        groups.setdefault(gkey, {"subject": r["subject"], "tasks": []})
        groups[gkey]["tasks"].append(r["task_content"])

    output = "📌今日待办：\n"
    for (aid, day_no) in sorted(groups):
        item = groups[(aid, day_no)]
        day_label = f"⏳补 Day{day_no}" if day_no < current_day[aid] else f"Day{day_no}"
        output += f"【归档ID{aid}】{item['subject']} {day_label}：\n"
        for t in item["tasks"]:
            output += f"- {t}\n"
    if not groups:
        # 今天的任务都做完了，但后面还有未完成任务（属于额外任务）
        return "🎉今日任务已完成！可以看看「额外任务」拓展学习"
    output += "\n💡做完后发送 /done id 归档ID day 天数 打卡，或用「额外任务」拓展学习"
    return output

def get_extra_learning_tasks():
    """额外任务：今天之后的可拓展学习内容（未完成且未到期的天）"""
    init_memory_db()
    today_dt = datetime.now().date()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT archive_id, subject, day_no, task_content, start_date
        FROM study_progress
        WHERE finished = 0
        ORDER BY archive_id ASC, day_no ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return "🎉所有任务都已完成，暂无额外任务"

    current_day = {}
    for r in rows:
        key = r["archive_id"]
        if key not in current_day:
            try:
                start = datetime.strptime(r["start_date"], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                start = today_dt
            current_day[key] = max(1, (today_dt - start).days + 1)

    extra = [r for r in rows if r["day_no"] > current_day[r["archive_id"]]]
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

# ===================== 激励与诊断：连续打卡 + 学情报告 =====================

def get_study_streak() -> int:
    """连续打卡天数：今天打过卡则从今天算，否则从昨天开始算"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
        SELECT DISTINCT complete_date FROM study_progress
        WHERE finished = 1 AND complete_date IS NOT NULL AND complete_date != ''
        """)
        dates = [r[0] for r in cur.fetchall()]
    if not dates:
        return 0
    date_set = set(dates)
    today = datetime.now().date()
    anchor = today if today.isoformat() in date_set else today - timedelta(days=1)
    streak = 0
    d = anchor
    while d.isoformat() in date_set:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _encourage_text(rate: float) -> str:
    if rate >= 0.9:
        return "🎉 状态极佳！保持这个节奏，你正在形成自己的学习惯性。"
    if rate >= 0.6:
        return "💪 进度不错！把薄弱科目补上，就能更上一层楼。"
    if rate >= 0.3:
        return "🌱 已经迈出第一步了，每天完成一点点，累积就是质变。"
    return "🌟 开始永远不晚，先从补上最近的一天开始，学习助手陪你一起。"


def get_study_report() -> str:
    """学情诊断报告：监测 → 诊断 → 建议（干预）→ 鼓励"""
    init_memory_db()
    today_dt = datetime.now().date()
    with DB_LOCK:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
        SELECT archive_id, subject, day_no, finished, start_date, complete_date
        FROM study_progress
        """)
        rows = cur.fetchall()
    if not rows:
        return "📊 还没有学习记录。\n先去「资料」上传文档，再生成每日任务，学习助手会帮你规划、追踪和复盘。"

    total = len(rows)
    finished = sum(1 for r in rows if r["finished"])
    rate = finished / total if total else 0.0

    start_map = {}
    for r in rows:
        if r["archive_id"] not in start_map:
            try:
                start_map[r["archive_id"]] = datetime.strptime(r["start_date"], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                start_map[r["archive_id"]] = today_dt

    subjects = {}
    overdue_total = 0
    for r in rows:
        sid = r["archive_id"]
        subj = r["subject"] or "未分类"
        st = subjects.setdefault(subj, {"total": 0, "finished": 0, "overdue": 0})
        st["total"] += 1
        if r["finished"]:
            st["finished"] += 1
        else:
            current_day = max(1, (today_dt - start_map[sid]).days + 1)
            if r["day_no"] < current_day:
                st["overdue"] += 1
                overdue_total += 1

    streak = get_study_streak()
    # 最近7天打卡日历
    done_dates = {r["complete_date"] for r in rows if r["finished"] and r["complete_date"]}
    cal = ["✅" if (today_dt - timedelta(days=i)).isoformat() in done_dates else "⬜" for i in range(6, -1, -1)]
    cal_line = "🗓 最近7天：" + "".join(cal)
    # 错题本统计
    wrong_count = 0
    wrong_archives = 0
    try:
        from wrong_book import list_wrong
        wrong_list = list_wrong()
        wrong_count = sum(x["count"] for x in wrong_list)
        wrong_archives = len(wrong_list)
    except Exception:
        pass
    lines = [
        "📊【学情诊断报告】",
        f"🔥 连续打卡：{streak} 天" if streak else "🔥 连续打卡：还没有开始",
        cal_line,
        f"📚 任务总览：{len(subjects)} 个科目 / {total} 个任务，完成 {finished}（{rate * 100:.0f}%）",
        f"📕 错题本：{wrong_count} 道待复习（{wrong_archives} 个归档）" if wrong_count else "📕 错题本：已清空 🎉",
        "",
        "📈 分科情况：",
    ]
    for subj, st in sorted(
        subjects.items(),
        key=lambda kv: (kv[1]["finished"] / kv[1]["total"] if kv[1]["total"] else 1, kv[0])
    ):
        r = st["finished"] / st["total"] * 100
        mark = "✅" if st["overdue"] == 0 and st["finished"] == st["total"] else "⏳"
        overdue_txt = f"，{st['overdue']} 天欠账" if st["overdue"] else ""
        lines.append(f"{mark} {subj}：{st['finished']}/{st['total']} 完成（{r:.0f}%）{overdue_txt}")

    lines.append("")
    lines.append("💡 诊断建议：")
    if overdue_total > 0:
        lines.append(f"- 有 {overdue_total} 天任务欠账，建议先用「今日任务」把欠账补上，再进入新内容。")
    weak_subj = min(
        subjects.items(),
        key=lambda kv: kv[1]["finished"] / kv[1]["total"] if kv[1]["total"] else 1
    )
    if weak_subj[1]["finished"] < weak_subj[1]["total"]:
        lines.append(f"- 「{weak_subj[0]}」目前完成率最低，建议今天优先安排它。")
    if finished == total:
        lines.append("- 全部任务已完成！可以提前预习「额外任务」，或上传新资料继续拓展。")
    if wrong_count:
        lines.append(f"- 错题本还有 {wrong_count} 道题没复习，建议先花 10 分钟过一遍，再用「错题本」清除已掌握的。")
    lines.append("")
    lines.append(_encourage_text(rate))
    return "\n".join(lines)


def get_overall_stats() -> dict:
    """总体学习统计（侧栏积分进度条用）"""
    init_memory_db()
    with DB_LOCK:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(finished), 0) FROM study_progress")
        total, finished = cur.fetchone()
    total = total or 0
    finished = finished or 0
    return {
        "finished": int(finished),
        "total": int(total),
        "points": int(finished) * 10,
    }

# ===================== 晨间推送：今日学习任务 =====================
def push_daily_tasks_to_feishu():
    """定时任务：每日早上推送今日待办学习任务"""
    try:
        user_context.set_current_user(os.getenv("SCHEDULER_USER") or os.getenv("ADMIN_USERNAME", "default"))
        task_text = get_today_learning_tasks()
        webhook = FEISHU_WEBHOOK

        if not webhook or webhook.startswith("在此处填入") or len(webhook) < 10:
            print("⚠️未配置FEISHU_WEBHOOK，跳过定时推送")
            return

        streak = get_study_streak()
        streak_line = f"\n🔥 已连续打卡 {streak} 天" if streak else ""
        full_msg = f"""⏰【晨间学习提醒】{streak_line}
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


def push_evening_reminder_to_feishu():
    """晚间督促：今天还有任务没完成时，推送提醒+鼓励"""
    try:
        user_context.set_current_user(os.getenv("SCHEDULER_USER") or os.getenv("ADMIN_USERNAME", "default"))
        webhook = FEISHU_WEBHOOK
        if not webhook or webhook.startswith("在此处填入") or len(webhook) < 10:
            print("⚠️未配置FEISHU_WEBHOOK，跳过晚间提醒")
            return
        task_text = get_today_learning_tasks()
        if "今日待办" not in task_text and "补 Day" not in task_text:
            print("ℹ️今日任务已完成，无需晚间提醒")
            return
        streak = get_study_streak()
        streak_line = f"\n🔥 已连续打卡 {streak} 天" if streak else ""
        full_msg = f"""🌙【晚间学习督促】{streak_line}
{task_text}

💡别让任务过夜，完成一点也比拖延强：
/done id 归档ID day N → 打卡
/today → 查看今日待办
/report → 查看学情诊断
"""
        if len(full_msg) > 4500:
            full_msg = full_msg[:4500] + "\n……内容过长，请在机器人内发送指令查看完整信息"
        payload = {
            "msg_type": "text",
            "content": {"text": full_msg}
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        print("✅晚间督促推送完成", resp.json())
    except Exception as e:
        import traceback
        print("❌晚间督促推送异常：", str(e))
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
    _scheduler.add_job(
        push_evening_reminder_to_feishu,
        "cron",
        hour=20,
        minute=0,
        coalesce=True,
        misfire_grace_time=300
    )
    _scheduler.start()
    print(f"⏰ 学习督促调度器启动【Asia/Shanghai】：每日{REVIEW_PUSH_HOUR}:00晨间推送，20:00晚间督促")
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
