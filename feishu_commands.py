# -*- coding: utf-8 -*-
"""飞书文本指令处理器：命令注册表，替代 feishu_bot 里的巨型 if/elif 链"""
from llm_summary import (
    extract_task_list,
    ai_simplify_filename,
    format_msg,
    generate_study_plan,
    split_plan_to_daily_tasks,
    generate_test_questions,
    generate_memory_cards,
    polish_text,
)
from archive_db import get_archive_by_id, archive_file, delete_archive_by_id, delete_archive_file


class CommandContext:
    """指令上下文：消息接收者、内容、发送函数、共享状态与依赖注入"""

    def __init__(self, receive_id, content, send_msg, send_long_msg, pdf_state, fns=None):
        self.receive_id = receive_id
        self.content = content
        self.send_msg = send_msg
        self.send_long_msg = send_long_msg
        self.pdf_state = pdf_state
        self.fns = fns or {}

    def fn(self, name):
        return self.fns.get(name)


# ===================== 指令处理 =====================

def handle_test(ctx):
    clean = ctx.fn("clean_expired_test_records")
    if clean:
        clean()
    parts = ctx.content.split(maxsplit=2)
    archive_id = 0
    if len(parts) >= 3 and parts[1].lower() == "id":
        try:
            archive_id = int(parts[2])
        except ValueError:
            ctx.send_msg(ctx.receive_id, "用法：/test id 归档ID")
            return
        row = get_archive_by_id(archive_id)
        if not row:
            ctx.send_msg(ctx.receive_id, f"❌找不到归档ID {archive_id}")
            return
        subject = row["subject"]
        text_content = row["file_text"]
        ctx.send_msg(ctx.receive_id, f"📖加载归档ID:{archive_id}【{subject}】生成习题")
    else:
        if len(parts) < 3:
            ctx.send_msg(ctx.receive_id, "📝出题方式：\n1) /test 科目 文本\n2) /test id 归档ID\n/list_test 查看试题记录\n")
            return
        subject = parts[1]
        text_content = parts[2]
    q, a = generate_test_questions(text_content, subject, 10)
    cq = format_msg(q)
    ca = format_msg(a)
    add_test_record = ctx.fn("add_test_record")
    if add_test_record:
        add_test_record(ctx.receive_id, archive_id, cq, ca)
    ctx.send_long_msg(ctx.receive_id, f"{cq}\n💡发送 /answer 查看答案")


def handle_answer(ctx):
    args = ctx.content.split(maxsplit=2)
    target_aid = None
    if len(args) >= 3 and args[1].lower() == "id":
        try:
            target_aid = int(args[2])
        except ValueError:
            ctx.send_msg(ctx.receive_id, "格式：/answer id 归档ID")
            return
    if target_aid is not None:
        rec = ctx.fn("get_latest_by_archive_id")(ctx.receive_id, target_aid)
    else:
        rec = ctx.fn("get_latest_all_record")(ctx.receive_id)
    if not rec:
        ctx.send_msg(ctx.receive_id, "暂无试题记录，请先执行/test")
        return
    ctx.send_long_msg(ctx.receive_id, f"【参考答案】\n{rec['answer']}")


def handle_list_test(ctx):
    records = ctx.fn("list_all_user_test_records")(ctx.receive_id)
    if not records:
        ctx.send_msg(ctx.receive_id, "📋暂无试题记录")
        return
    lines = ["📋试题清单（30分钟有效期）："]
    for idx, r in enumerate(records, 1):
        aid = r["archive_id"]
        desc = f"归档ID={aid}" if aid != 0 else "临时文本出题"
        lines.append(f"{idx}. {desc}")
    lines.append("\n/answer id 数字 查询对应习题答案")
    ctx.send_msg(ctx.receive_id, "\n".join(lines))


def _parse_plan_args(content):
    """解析 /plan id 3 [days 7] 与 /daily id 3 [days 7]，返回 (aid, days) 或 (None, None)"""
    parts = content.split()
    try:
        aid = int(parts[2])
        days = 5
        if len(parts) >= 5 and parts[3].lower() == "days":
            days = max(2, min(14, int(parts[4])))
        return aid, days
    except Exception:
        return None, None


def handle_plan(ctx):
    aid, target_days = _parse_plan_args(ctx.content)
    if aid is None:
        ctx.send_msg(ctx.receive_id, "📖用法：\n/plan id 归档ID\n/plan id 归档ID days 7\n示例：/plan id 5 days 7")
        return
    row = get_archive_by_id(aid)
    if not row:
        ctx.send_msg(ctx.receive_id, f"❌未找到归档ID={aid}")
        return
    ctx.send_msg(ctx.receive_id, f"🤖正在生成【{target_days}天】学习方案，请稍候...")
    full_plan_text = generate_study_plan(row["file_text"], row["subject"])
    daily_task_text = split_plan_to_daily_tasks(full_plan_text, row["subject"], target_days)
    output = f"""📅【{row['subject']}学习总方案】
归档ID：{aid}
文档名称：{row['filename']}
周期：{target_days}天

====整体规划====
{full_plan_text}

====📆每日细化任务清单====
{daily_task_text}
"""
    ctx.send_long_msg(ctx.receive_id, output)


def handle_daily(ctx):
    aid, target_days = _parse_plan_args(ctx.content)
    if aid is None:
        ctx.send_msg(ctx.receive_id, "📖用法：\n/daily id 归档ID\n/daily id 归档ID days 6\n")
        return
    row = get_archive_by_id(aid)
    if not row:
        ctx.send_msg(ctx.receive_id, f"❌未找到归档ID={aid}")
        return
    ctx.send_msg(ctx.receive_id, f"🤖正在拆分{target_days}天每日学习任务...")
    full_plan_text = generate_study_plan(row["file_text"], row["subject"])
    daily_task_text = split_plan_to_daily_tasks(full_plan_text, row["subject"], target_days)
    task_list = extract_task_list(daily_task_text)

    from review_scheduler import save_daily_tasks
    save_daily_tasks(aid, row["subject"], task_list)

    output = f"""📆【{row['subject']}每日学习任务】
归档ID：{aid} ｜ {target_days}天周期
{daily_task_text}
💡打卡用法：/done id {aid} day 1 标记第1天完成
📊进度查询：/progress id {aid}
                """
    ctx.send_long_msg(ctx.receive_id, output)


def handle_cards(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/cards id 归档ID\n示例：/cards id 3")
        return
    row = get_archive_by_id(aid)
    if not row:
        ctx.send_msg(ctx.receive_id, f"❌未找到归档ID={aid}")
        return
    if not row["file_text"] or len(row["file_text"].strip()) < 20:
        ctx.send_msg(ctx.receive_id, "该归档文档没有可用的文本内容")
        return
    ctx.send_msg(ctx.receive_id, f"🤖正在把归档ID:{aid}【{row['subject']}】提炼成知识点和背诵卡片，请稍候...")
    try:
        cards = generate_memory_cards(row["file_text"], row["subject"])
    except Exception as e:
        ctx.send_msg(ctx.receive_id, f"❌生成失败：{str(e)}")
        return
    ctx.send_long_msg(ctx.receive_id, f"📚【{row['subject']}】知识点与背诵卡片\n{format_msg(cards)}")


def handle_done(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
        day_num = int(parts[4])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/done id 归档ID day 天数\n示例：/done id 6 day 2")
        return
    from review_scheduler import mark_task_finished, get_study_streak
    ok = mark_task_finished(aid, day_num)
    if ok:
        streak = get_study_streak()
        encourage = f"🔥 已连续打卡 {streak} 天！" if streak > 1 else "🎉 打卡成功，继续保持！"
        ctx.send_msg(ctx.receive_id, f"✅已标记归档ID:{aid} 第{day_num}天任务完成！{encourage}")
    else:
        ctx.send_msg(ctx.receive_id, "❌未找到对应任务，检查归档ID或天数是否正确")


def handle_progress(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/progress id 归档ID")
        return
    from review_scheduler import get_archive_progress
    rows = get_archive_progress(aid)
    if not rows:
        ctx.send_msg(ctx.receive_id, "⚠️暂无任务记录，请先执行 /daily id xxx 生成每日任务")
        return
    total = len(rows)
    finished = sum(1 for r in rows if r["finished"] == 1)
    rate = f"{finished / total * 100:.1f}%" if total > 0 else "0%"
    msg_lines = [f"📊学习进度 归档ID:{aid}\n总任务：{total}天 | 已完成：{finished} | 完成率：{rate}\n"]
    for r in rows:
        status = "✅已完成" if r["finished"] else "⏳待完成"
        complete_day = r["complete_date"] if r["complete_date"] else "未打卡"
        msg_lines.append(f"Day{r['day_no']} {status} | {complete_day}")
    ctx.send_long_msg(ctx.receive_id, "\n".join(msg_lines))


def handle_report(ctx):
    from review_scheduler import get_study_report
    ctx.send_long_msg(ctx.receive_id, get_study_report())


def handle_save(ctx):
    parts = ctx.content.split(maxsplit=2)
    if len(parts) < 3:
        ctx.send_msg(ctx.receive_id, "/save 科目 知识点")
        return
    subject = parts[1]
    kname = parts[2]
    clean_name = ai_simplify_filename(kname, subject)
    with ctx.pdf_state.lock:
        if not ctx.pdf_state.last_pdf_bytes:
            ctx.send_msg(ctx.receive_id, "⚠️先上传文件再执行/save")
            return
        save_path, new_id = archive_file(subject, clean_name, ctx.pdf_state.last_pdf_bytes, ctx.pdf_state.last_pdf_name, "")
        ctx.send_msg(ctx.receive_id, f"✅归档成功！ID={new_id}")


def handle_del(ctx):
    body = ctx.content.removeprefix("/del").strip()
    parts = body.split(maxsplit=2)
    if len(parts) >= 2 and parts[0].lower() == "id":
        try:
            target_id = int(parts[1])
            deleted_name = delete_archive_by_id(target_id)
            if not deleted_name:
                ctx.send_msg(ctx.receive_id, "找不到该归档ID")
                return
            remove_kb = ctx.fn("remove_archive_from_kb")
            if remove_kb:
                remove_kb(target_id)
            ctx.send_msg(ctx.receive_id, f"✅删除归档ID:{target_id} {deleted_name}")
        except ValueError:
            ctx.send_msg(ctx.receive_id, "用法 /del id 数字")
        return
    if "|" not in body:
        ctx.send_msg(ctx.receive_id, "/del id 数字 推荐使用")
        return
    subj, fname = [x.strip() for x in body.split("|", maxsplit=1)]
    res = delete_archive_file(subj, "", fname)
    ctx.send_msg(ctx.receive_id, res)


def handle_polish(ctx):
    body = ctx.content.removeprefix("/polish").removeprefix("/改写").strip()
    if not body:
        ctx.send_msg(ctx.receive_id, "📝用法：\n/polish 德语 你的文本\n/polish 英语 你的文本\n/polish 你的文本（自动识别语言）\n/polish id 归档ID（润色归档文档）\n可以在文本前附上修改要求，例如：\n/polish 德语 改得更口语化一点：原文内容\n")
        return
    first, _, rest = body.partition(" ")
    if first == "id":
        try:
            aid = int(rest.strip())
        except ValueError:
            ctx.send_msg(ctx.receive_id, "用法：/polish id 归档ID")
            return
        row = get_archive_by_id(aid)
        if not row:
            ctx.send_msg(ctx.receive_id, f"❌找不到归档ID {aid}")
            return
        lang = ""
        text = row["file_text"]
        if not text or len(text.strip()) < 20:
            ctx.send_msg(ctx.receive_id, "该归档文档没有可用的文本内容")
            return
    elif first in ("德语", "德", "de", "英语", "英", "en"):
        lang = first
        text = rest.strip()
    else:
        lang = ""
        text = body
    if not text:
        ctx.send_msg(ctx.receive_id, "请把需要修改的文本一起发给我")
        return
    ctx.send_msg(ctx.receive_id, "🔄正在润色，请稍候...")
    try:
        result = polish_text(text, lang)
    except Exception as e:
        ctx.send_msg(ctx.receive_id, f"❌润色失败：{str(e)}")
        return
    ctx.send_long_msg(ctx.receive_id, f"✍️润色结果：\n{result}")


def handle_rebuild_kb(ctx):
    ctx.send_msg(ctx.receive_id, "🔄开始重建全部向量知识库，耗时较长，请耐心等待...")
    try:
        rebuild = ctx.fn("rebuild_kb")
        if not rebuild:
            from vector_kb import rebuild_kb as rebuild
        rebuild()
        ctx.send_msg(ctx.receive_id, "✅知识库重建完成！所有归档文档已载入向量库")
    except Exception as e:
        import traceback
        traceback.print_exc()
        ctx.send_msg(ctx.receive_id, f"❌重建知识库失败：{str(e)}")


def handle_tip(ctx):
    send_card = ctx.fn("send_interactive_card")
    build_card = ctx.fn("build_menu_card")
    if send_card and build_card:
        send_card(ctx.receive_id, build_card())


def handle_default_rag(ctx):
    ctx.send_msg(ctx.receive_id, "🤖正在检索本地归档资料，请稍候...")
    try:
        from vector_kb import rag_answer
        reply = rag_answer(ctx.content)
        ctx.send_long_msg(ctx.receive_id, reply)
    except Exception as e:
        import traceback
        traceback.print_exc()
        ctx.send_msg(ctx.receive_id, f"问答服务出错：{str(e)}")


# ===================== 命令注册表 =====================

EXACT_HANDLERS = {
    "/list_test": handle_list_test,
    "/rebuild_kb": handle_rebuild_kb,
    "/tip": handle_tip,
}

PREFIX_HANDLERS = [
    ("/test", handle_test),
    ("/answer", handle_answer),
    ("/plan id", handle_plan),
    ("/daily id", handle_daily),
    ("/cards id", handle_cards),
    ("/done id", handle_done),
    ("/progress id", handle_progress),
    ("/report", handle_report),
    ("/save", handle_save),
    ("/del", handle_del),
    ("/polish", handle_polish),
    ("/改写", handle_polish),
]


def dispatch_text_command(ctx):
    """按命令注册表分发文本指令，未匹配时走 RAG 自由问答"""
    handler = EXACT_HANDLERS.get(ctx.content)
    if handler:
        return handler(ctx)
    for prefix, h in PREFIX_HANDLERS:
        if ctx.content.startswith(prefix):
            return h(ctx)
    return handle_default_rag(ctx)
