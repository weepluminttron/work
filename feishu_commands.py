# -*- coding: utf-8 -*-
"""飞书文本指令处理器：命令注册表，替代 feishu_bot 里的巨型 if/elif 链"""
from llm_summary import (
    ai_simplify_filename,
    generate_test_questions,
)
from archive_db import get_archive_by_id, archive_file


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
    ctx.send_msg(ctx.receive_id, f"🤖正在生成【{target_days}天】学习方案，请稍候...")
    from study_service import plan_text
    ctx.send_long_msg(ctx.receive_id, plan_text(aid, target_days, include_daily=True))


def handle_daily(ctx):
    aid, target_days = _parse_plan_args(ctx.content)
    if aid is None:
        ctx.send_msg(ctx.receive_id, "📖用法：\n/daily id 归档ID\n/daily id 归档ID days 6\n")
        return
    ctx.send_msg(ctx.receive_id, f"🤖正在拆分{target_days}天每日学习任务...")
    from study_service import daily_text
    ctx.send_long_msg(ctx.receive_id, daily_text(aid, target_days))


def handle_cards(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/cards id 归档ID\n示例：/cards id 3")
        return
    ctx.send_msg(ctx.receive_id, f"🤖正在把归档ID:{aid} 提炼成知识点和背诵卡片，请稍候...")
    try:
        from study_service import cards_text
        result = cards_text(aid)
    except Exception as e:
        ctx.send_msg(ctx.receive_id, f"❌生成失败：{str(e)}")
        return
    ctx.send_long_msg(ctx.receive_id, result)


def handle_done(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
        day_num = int(parts[4])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/done id 归档ID day 天数\n示例：/done id 6 day 2")
        return
    from study_service import done_text
    ctx.send_msg(ctx.receive_id, done_text(aid, day_num))


def handle_progress(ctx):
    parts = ctx.content.split()
    try:
        aid = int(parts[2])
    except Exception:
        ctx.send_msg(ctx.receive_id, "📝用法：/progress id 归档ID")
        return
    from study_service import progress_text
    ctx.send_long_msg(ctx.receive_id, progress_text(aid))


def handle_report(ctx):
    from study_service import report_text
    ctx.send_long_msg(ctx.receive_id, report_text())


def handle_goals(ctx):
    from study_service import goals_text
    ctx.send_long_msg(ctx.receive_id, goals_text())


def handle_video(ctx):
    from study_service import video_cmd_text
    ctx.send_long_msg(ctx.receive_id, video_cmd_text(ctx.content))


def handle_goal(ctx):
    from study_service import goal_cmd_text
    ctx.send_long_msg(ctx.receive_id, goal_cmd_text(ctx.content))


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
    from study_service import delete_text
    ctx.send_msg(ctx.receive_id, delete_text(ctx.content))


def handle_polish(ctx):
    ctx.send_msg(ctx.receive_id, "🔄正在润色，请稍候...")
    try:
        from study_service import polish_cmd_text
        result = polish_cmd_text(ctx.content)
    except Exception as e:
        ctx.send_msg(ctx.receive_id, f"❌润色失败：{str(e)}")
        return
    ctx.send_long_msg(ctx.receive_id, result)


def handle_rebuild_kb(ctx):
    from config import check_admin_password
    pwd = ctx.content.removeprefix("/rebuild_kb").strip()
    if not pwd:
        ctx.send_msg(ctx.receive_id, "🔐 该操作需要二级密码：请发送 /rebuild_kb 二级密码")
        return
    if not check_admin_password(pwd):
        ctx.send_msg(ctx.receive_id, "❌ 二级密码错误，重建已取消")
        return
    ctx.send_msg(ctx.receive_id, "🔄开始重建全部向量知识库，耗时较长，请耐心等待...")
    from study_service import rebuild_text
    ctx.send_msg(ctx.receive_id, rebuild_text())


def handle_tip(ctx):
    send_card = ctx.fn("send_interactive_card")
    build_card = ctx.fn("build_menu_card")
    if send_card and build_card:
        send_card(ctx.receive_id, build_card())


def handle_default_rag(ctx):
    ctx.send_msg(ctx.receive_id, "🤖正在检索本地归档资料，请稍候...")
    try:
        from vector_kb import rag_answer
        from chat_memory import add_turn, get_history
        history = get_history(ctx.receive_id)
        reply = rag_answer(ctx.content, history)
        add_turn(ctx.receive_id, "user", ctx.content)
        add_turn(ctx.receive_id, "assistant", reply)
        ctx.send_long_msg(ctx.receive_id, reply)
    except Exception as e:
        import traceback
        traceback.print_exc()
        ctx.send_msg(ctx.receive_id, f"问答服务出错：{str(e)}")


def handle_clear(ctx):
    from chat_memory import clear_history
    removed = clear_history(ctx.receive_id)
    ctx.send_msg(ctx.receive_id, "✅ 已清空对话记忆，开始新话题" + (f"（清理 {removed} 条消息）" if removed else ""))


# ===================== 命令注册表 =====================

EXACT_HANDLERS = {
    "/list_test": handle_list_test,
    "/rebuild_kb": handle_rebuild_kb,
    "/tip": handle_tip,
    "/clear": handle_clear,
    "/清空记忆": handle_clear,
    "/goals": handle_goals,
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
    ("/goal", handle_goal),
    ("/视频", handle_video),
    ("/video", handle_video),
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
