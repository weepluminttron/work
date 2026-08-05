# -*- coding: utf-8 -*-
"""网页版学习助手：复用学习助手核心功能，提供手机/电脑可用的聊天界面（PWA）"""
import os
import functools
from flask import Flask, request, jsonify, session, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")


def _load_local_env():
    """读取 .env 文件（密钥/密码），已存在的环境变量优先"""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_local_env()

app = Flask(__name__)
app.secret_key = os.getenv("WEB_SECRET_KEY", "study-assistant-web-secret")
# 网页版登录密码：在服务器 .env 中设置 WEB_PASSWORD；不设置则无需登录（仅建议自用）
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")

HELP_TEXT = """📚 网页版学习助手指令：
/list               查看归档清单
/today              查看今日待办任务
/cards id 3         把归档文档生成背诵卡片
/test id 3          把归档文档生成自测题
/plan id 3 [days 7]  生成完整学习计划
/daily id 3 [days 5] 生成每日任务并保存
/progress id 3      查看学习进度
/done id 3 day 2    打卡完成第2天任务
/polish 德语 文本    润色德语/英语文本（或 /polish id 3）
/rebuild            重建知识库
/help               显示本清单

直接输入问题，会自动检索你的归档资料回答。
📎 点击输入框左侧的回形针按钮，可上传 PDF/DOC/DOCX/PPTX，自动归档并加入知识库。"""


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if WEB_PASSWORD and not session.get("auth"):
            return jsonify({"ok": False, "error": "未登录"}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/")
def index():
    return send_file(os.path.join(WEB_DIR, "chat.html"))


@app.route("/manifest.json")
def manifest():
    return send_file(os.path.join(WEB_DIR, "manifest.json"))


@app.route("/sw.js")
def service_worker():
    return send_file(os.path.join(WEB_DIR, "sw.js"), mimetype="application/javascript")


@app.route("/icon.svg")
def icon():
    return send_file(os.path.join(WEB_DIR, "icon.svg"), mimetype="image/svg+xml")


@app.route("/api/status")
def status():
    authed = session.get("auth", False) or not WEB_PASSWORD
    return jsonify({"ok": True, "need_login": bool(WEB_PASSWORD), "authed": authed})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    if WEB_PASSWORD and data.get("password") != WEB_PASSWORD:
        print("📱网页版登录失败：密码错误")
        return jsonify({"ok": False, "error": "密码错误"}), 401
    session["auth"] = True
    print("📱网页版登录成功")
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    session.pop("auth", None)
    return jsonify({"ok": True})


def _parse_aid(parts):
    """解析归档ID：/cmd id 3 或 /cmd 3"""
    try:
        if len(parts) >= 3 and parts[1].lower() == "id":
            return int(parts[2])
        if len(parts) >= 2:
            return int(parts[1])
    except ValueError:
        return None
    return None


def _get_archive(aid):
    from archive_db import get_archive_by_id
    return get_archive_by_id(aid)


def _list_subjects():
    """归档科目列表（去重）"""
    from archive_db import get_all_archive_items
    items = get_all_archive_items()
    seen = set()
    subjects = []
    for it in items:
        s = (it.get("subject") or "").strip()
        if s and s not in seen:
            seen.add(s)
            subjects.append(s)
    return subjects


def _list_docs(subject: str):
    """某科目下的归档文档列表 [(id, filename), ...]"""
    from archive_db import query_by_subject
    rows = query_by_subject(subject)
    return [(r["id"], r["filename"]) for r in rows]


def handle_web_command(text: str) -> str:
    """处理网页版指令/自由提问，返回回复文本"""
    if not text.startswith("/"):
        from vector_kb import rag_answer
        return rag_answer(text)

    parts = text.split()
    cmd = parts[0].lower()

    if cmd in ("/help", "/菜单", "/指令"):
        return HELP_TEXT

    if cmd == "/list":
        from archive_db import get_all_archive_summary
        return get_all_archive_summary()

    if cmd == "/today":
        from review_scheduler import get_today_learning_tasks
        return get_today_learning_tasks()

    if cmd == "/cards":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/cards id 归档ID"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        if not row.get("file_text") or len(row["file_text"].strip()) < 20:
            return "该归档文档没有可用的文本内容"
        from llm_summary import generate_memory_cards
        return generate_memory_cards(row["file_text"], row["subject"])

    if cmd == "/test":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/test id 归档ID"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        from llm_summary import generate_test_questions
        q, a = generate_test_questions(row["file_text"], row["subject"], 10)
        return f"{q}\n\n📝【参考答案】\n{a}"

    if cmd == "/plan":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/plan id 归档ID [days 7]"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        days = 5
        if len(parts) >= 5 and parts[3].lower() == "days":
            try:
                days = max(2, min(14, int(parts[4])))
            except ValueError:
                pass
        from llm_summary import generate_study_plan
        plan = generate_study_plan(row["file_text"], row["subject"])
        return f"📅【{row['subject']}】学习计划（周期约 {days} 天）\n\n{plan}\n\n💡需要每日任务请用 /daily id {aid} days {days}"

    if cmd == "/daily":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/daily id 归档ID [days 5]"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        days = 5
        if len(parts) >= 5 and parts[3].lower() == "days":
            try:
                days = max(2, min(14, int(parts[4])))
            except ValueError:
                pass
        from llm_summary import generate_study_plan, split_plan_to_daily_tasks, extract_task_list
        from review_scheduler import save_daily_tasks
        plan = generate_study_plan(row["file_text"], row["subject"])
        daily_text = split_plan_to_daily_tasks(plan, row["subject"], days)
        task_list = extract_task_list(daily_text)
        save_daily_tasks(aid, row["subject"], task_list)
        return f"📆【{row['subject']}】每日学习任务（{days}天）\n\n{daily_text}\n\n💡完成后用 /done id {aid} day 天数 打卡"

    if cmd == "/done":
        try:
            if len(parts) >= 2 and parts[1].lower() == "id":
                aid = int(parts[2])
                day_num = int(parts[4]) if len(parts) >= 5 else int(parts[3])
            else:
                aid = int(parts[1])
                day_num = int(parts[2])
        except (ValueError, IndexError):
            return "用法：/done id 归档ID day 天数"
        from review_scheduler import mark_task_finished
        ok = mark_task_finished(aid, day_num)
        return f"✅已标记归档ID:{aid} 第{day_num}天任务完成！" if ok else "❌未找到对应任务"

    if cmd == "/progress":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/progress id 归档ID"
        from review_scheduler import get_archive_progress
        rows = get_archive_progress(aid)
        if not rows:
            return "⚠️暂无任务记录，请先执行 /daily id 归档ID 生成每日任务"
        total = len(rows)
        finished = sum(1 for r in rows if r["finished"] == 1)
        rate = f"{finished/total*100:.1f}%" if total else "0%"
        lines = [f"📊学习进度 归档ID:{aid}\n总任务：{total}天 | 已完成：{finished} | 完成率：{rate}\n"]
        for r in rows:
            status = "✅已完成" if r["finished"] else "⏳待完成"
            lines.append(f"Day{r['day_no']} {status} | {r['complete_date'] or '未打卡'}")
        return "\n".join(lines)

    if cmd == "/polish":
        body = text[len(cmd):].strip()
        if not body:
            return "用法：/polish 德语 文本 或 /polish id 归档ID"
        first, _, rest = body.partition(" ")
        lang = ""
        if first.lower() in ("德语", "德", "de", "英语", "英", "en"):
            lang = first
            content = rest.strip()
        elif first.lower() == "id":
            try:
                aid = int(rest.strip())
            except ValueError:
                return "用法：/polish id 归档ID"
            row = _get_archive(aid)
            if not row:
                return f"❌未找到归档ID {aid}"
            content = row["file_text"]
        else:
            content = body
        if not content:
            return "请把需要修改的文本发给我"
        from llm_summary import polish_text
        return f"✍️润色结果：\n{polish_text(content, lang)}"

    if cmd == "/rebuild":
        from vector_kb import rebuild_kb
        rebuild_kb()
        return "✅知识库重建完成！所有归档文档已载入向量库"

    return "未知指令，发送 /help 查看可用指令"


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    print(f"📱网页版收到消息：{text[:200]}")
    if not text:
        return jsonify({"ok": False, "error": "消息为空"})
    try:
        reply = handle_web_command(text)
        print(f"📱网页版回复完成（{len(reply)} 字）")
        return jsonify({"ok": True, "reply": reply})
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"📱网页版处理异常：{e}")
        return jsonify({"ok": False, "error": f"处理失败：{e}"})


@app.route("/api/options", methods=["POST"])
@login_required
def options():
    """多级选项：科目 → 文档 → （天数）→ 执行"""
    data = request.get_json(silent=True) or {}
    step = data.get("step") or "subject"
    next_cmd = data.get("next", "")
    subject = data.get("subject", "")
    aid = data.get("aid")

    if step == "subject":
        subjects = _list_subjects()
        if not subjects:
            return jsonify({"ok": True, "prompt": "📂 暂无归档文档，请先点 📎 上传文件", "options": []})
        options_list = [
            {"label": s, "payload": {"step": "docs", "next": next_cmd, "subject": s}}
            for s in subjects
        ]
        print(f"📱网页版选项：选择科目（下一步 {next_cmd}）")
        return jsonify({"ok": True, "prompt": "① 选择科目：", "options": options_list})

    if step == "docs":
        docs = _list_docs(subject)
        options_list = []
        for did, fname in docs:
            if next_cmd in ("plan", "daily", "done"):
                payload = {"step": "days", "next": next_cmd, "subject": subject, "aid": did}
            else:
                payload = {"step": "run", "cmd": f"/{next_cmd} id {did}"}
            options_list.append({"label": f"ID{did} {fname}", "payload": payload})
        print(f"📱网页版选项：选择文档（{subject}，共{len(docs)}个）")
        return jsonify({"ok": True, "prompt": f"② 选择文档（{subject}）：", "options": options_list})

    if step == "days":
        days = data.get("days")
        if days is None:
            options_list = [
                {"label": f"{d} 天", "payload": {"step": "days", "next": next_cmd, "subject": subject, "aid": aid, "days": d}}
                for d in (3, 5, 7, 10, 14)
            ]
            return jsonify({"ok": True, "prompt": "③ 选择学习天数：", "options": options_list})
        if next_cmd == "done":
            cmd = f"/done id {aid} day {days}"
        else:
            cmd = f"/{next_cmd} id {aid} days {days}"
        print(f"📱网页版选项完成，执行：{cmd}")
        return jsonify({"ok": True, "prompt": "", "options": [], "run": cmd})

    return jsonify({"ok": False, "error": "无效操作"})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "没有收到文件"})
    filename = f.filename
    file_bytes = f.read()
    if not file_bytes:
        return jsonify({"ok": False, "error": "文件内容为空"})
    print(f"📱网页版收到文件：{filename}（{len(file_bytes)} 字节）")
    try:
        from file_parser import extract_file_text
        supported, doc_text = extract_file_text(filename, file_bytes)
        if not supported:
            return jsonify({"ok": False, "error": "不支持的文件格式，请上传 PDF/DOC/DOCX/PPTX"})
        if len(doc_text.strip()) < 20:
            return jsonify({"ok": False, "error": "文档文字过少，无法处理（扫描版需要安装 OCR）"})

        from llm_summary import auto_extract_archive_info, ai_simplify_filename
        from archive_db import archive_file
        from vector_kb import add_archive_to_kb

        auto_info = auto_extract_archive_info(doc_text)
        subj = auto_info["subject"]
        short_name = ai_simplify_filename(filename, subj)
        save_path, new_aid = archive_file(subj, short_name, file_bytes, filename, doc_text)
        add_archive_to_kb(new_aid)
        print(f"📱网页版归档完成：ID={new_aid} 科目={subj} 文件名={short_name}")
        return jsonify({
            "ok": True,
            "reply": f"✅归档成功！\n归档ID：{new_aid}\n科目：{subj}\n文件名：{short_name}\n\n继续学习：\n/cards id {new_aid} 生成背诵卡片\n/test id {new_aid} 生成自测题\n/plan id {new_aid} 生成学习计划"
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"📱网页版文件处理异常：{e}")
        return jsonify({"ok": False, "error": f"处理失败：{e}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
