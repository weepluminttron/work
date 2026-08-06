# -*- coding: utf-8 -*-
"""网页版与飞书共用的指令逻辑：一个命令只写一份，两个渠道各自只做“收发”"""
from archive_db import get_archive_by_id, delete_archive_by_id, delete_archive_file
from llm_summary import (
    generate_study_plan,
    split_plan_to_daily_tasks,
    extract_task_list,
    generate_memory_cards,
    polish_text,
    format_msg,
)
from review_scheduler import (
    save_daily_tasks,
    mark_task_finished,
    get_study_streak,
    get_archive_progress,
    get_study_report,
)


def _archive_or_error(aid):
    row = get_archive_by_id(aid)
    if not row:
        return None, f"❌未找到归档ID {aid}"
    return row, None


def plan_text(aid: int, days: int = 5, include_daily: bool = True) -> str:
    """生成学习计划；include_daily=True 时（飞书）附带每日任务拆解"""
    row, err = _archive_or_error(aid)
    if err:
        return err
    full_plan = generate_study_plan(row["file_text"], row["subject"])
    if not include_daily:
        return f"📅【{row['subject']}】学习计划（周期约 {days} 天）\n\n{full_plan}\n\n💡需要每日任务请用 /daily id {aid} days {days}"
    daily = split_plan_to_daily_tasks(full_plan, row["subject"], days)
    return f"""📅【{row['subject']}学习总方案】
归档ID：{aid}
文档名称：{row['filename']}
周期：{days}天

====整体规划====
{full_plan}

====📆每日细化任务清单====
{daily}
"""


def daily_text(aid: int, days: int = 5, daily_minutes: int = None) -> str:
    """生成每日任务并保存到进度表"""
    row, err = _archive_or_error(aid)
    if err:
        return err
    plan = generate_study_plan(row["file_text"], row["subject"])
    daily = split_plan_to_daily_tasks(plan, row["subject"], days, daily_minutes)
    task_list = extract_task_list(daily)
    save_daily_tasks(aid, row["subject"], task_list)
    time_note = f"，每天约 {daily_minutes} 分钟" if daily_minutes else ""
    return f"""📆【{row['subject']}】每日学习任务（归档ID {aid}｜{days}天{time_note}）
{daily}
💡打卡：/done id {aid} day 天数
📊进度：/progress id {aid}
"""


def cards_text(aid: int) -> str:
    """生成知识点与背诵卡片"""
    row, err = _archive_or_error(aid)
    if err:
        return err
    if not row["file_text"] or len(row["file_text"].strip()) < 20:
        return "该归档文档没有可用的文本内容"
    cards = generate_memory_cards(row["file_text"], row["subject"])
    return f"📚【{row['subject']}】知识点与背诵卡片\n{format_msg(cards)}"


def done_text(aid: int, day_num: int) -> str:
    """打卡并返回带连续天数的结果"""
    ok = mark_task_finished(aid, day_num)
    if not ok:
        return "❌未找到对应任务，检查归档ID或天数是否正确"
    streak = get_study_streak()
    encourage = f"🔥 已连续打卡 {streak} 天！" if streak > 1 else "🎉 打卡成功，继续保持！"
    return f"✅已标记归档ID:{aid} 第{day_num}天任务完成！{encourage}"


def progress_text(aid: int) -> str:
    """查看单个归档的学习进度"""
    rows = get_archive_progress(aid)
    if not rows:
        return "⚠️暂无任务记录，请先执行 /daily id xxx 生成每日任务"
    total = len(rows)
    finished = sum(1 for r in rows if r["finished"] == 1)
    rate = f"{finished / total * 100:.1f}%" if total > 0 else "0%"
    msg_lines = [f"📊学习进度 归档ID:{aid}\n总任务：{total}天 | 已完成：{finished} | 完成率：{rate}\n"]
    for r in rows:
        status = "✅已完成" if r["finished"] else "⏳待完成"
        complete_day = r["complete_date"] if r["complete_date"] else "未打卡"
        msg_lines.append(f"Day{r['day_no']} {status} | {complete_day}")
    return "\n".join(msg_lines)


def report_text() -> str:
    """学情诊断报告"""
    return get_study_report()


def polish_cmd_text(raw_cmd: str) -> str:
    """润色指令：/polish 德语 文本 或 /polish id 归档ID"""
    body = raw_cmd.removeprefix("/polish").removeprefix("/改写").strip()
    if not body:
        return ("📝用法：\n/polish 德语 你的文本\n/polish 英语 你的文本\n"
                "/polish 你的文本（自动识别语言）\n/polish id 归档ID（润色归档文档）\n"
                "可以在文本前附上修改要求，例如：/polish 德语 改得更口语化一点：原文内容")
    first, _, rest = body.partition(" ")
    if first == "id":
        try:
            aid = int(rest.strip())
        except ValueError:
            return "用法：/polish id 归档ID"
        row, err = _archive_or_error(aid)
        if err:
            return err
        lang = ""
        text = row["file_text"]
        if not text or len(text.strip()) < 20:
            return "该归档文档没有可用的文本内容"
    elif first in ("德语", "德", "de", "英语", "英", "en"):
        lang = first
        text = rest.strip()
    else:
        lang = ""
        text = body
    if not text:
        return "请把需要修改的文本一起发给我"
    result = polish_text(text, lang)
    return f"✍️润色结果：\n{result}"


def delete_text(raw_cmd: str) -> str:
    """删除指令：/del id 3 或 /del 科目|文件名"""
    body = raw_cmd.removeprefix("/del").strip()
    parts = body.split(maxsplit=2)
    if len(parts) >= 1 and (parts[0].lower() == "id" or parts[0].isdigit()):
        try:
            target_id = int(parts[1] if parts[0].lower() == "id" else parts[0])
        except (ValueError, IndexError):
            return "用法 /del id 数字"
        deleted_name = delete_archive_by_id(target_id)
        if not deleted_name:
            return "找不到该归档ID"
        from vector_kb import remove_archive_from_kb
        remove_archive_from_kb(target_id)
        return f"✅删除归档ID:{target_id} {deleted_name}"
    if "|" not in body:
        return "/del id 数字 推荐使用"
    subj, fname = [x.strip() for x in body.split("|", maxsplit=1)]
    return delete_archive_file(subj, "", fname)


def rebuild_text() -> str:
    """重建知识库"""
    from vector_kb import rebuild_kb
    try:
        rebuild_kb()
        return "✅知识库重建完成！所有归档文档已载入向量库"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌重建知识库失败：{str(e)}"
