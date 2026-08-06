# -*- coding: utf-8 -*-
"""网页版学习助手：复用学习助手核心功能，提供手机/电脑可用的聊天界面（PWA）"""
import os
import functools
import threading
import uuid
import time
from flask import Flask, request, jsonify, session, send_file, g
from quiz_logic import extract_answer_keys as _extract_answer_keys
from quiz_logic import parse_quiz_options as _parse_quiz_options
from quiz_logic import grade_paper, clean_question_text

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
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

import user_context
from user_auth import ensure_admin

# 启动时确保管理员账号存在，并把旧版全局数据迁移到管理员名下
admin_pwd = os.getenv("ADMIN_PASSWORD", "") or WEB_PASSWORD
if admin_pwd:
    ensure_admin(ADMIN_USERNAME, admin_pwd)
user_context.migrate_legacy(ADMIN_USERNAME)


@app.before_request
def _set_request_ctx():
    """每个请求生成日志ID并记录开始时间（对齐 Coze 的访问日志中间件）"""
    g.log_id = uuid.uuid4().hex[:12]
    g.start_time = time.time()
    username = session.get("username") if session.get("auth") else None
    user_context.set_current_user(username or "guest")


@app.after_request
def _log_request(resp):
    """统一请求日志：方法/路径/状态码/耗时/日志ID"""
    duration_ms = (time.time() - getattr(g, "start_time", time.time())) * 1000
    log_id = getattr(g, "log_id", "-")
    print(f"📡 {request.method} {request.path} -> {resp.status_code} ({duration_ms:.0f}ms) log_id={log_id}")
    resp.headers["X-Log-ID"] = log_id
    return resp


@app.errorhandler(404)
def _not_found(e):
    return jsonify({"ok": False, "error": "接口不存在"}), 404


@app.errorhandler(405)
def _method_not_allowed(e):
    return jsonify({"ok": False, "error": "方法不允许"}), 405


@app.errorhandler(500)
def _internal_error(e):
    return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# 自测题答案暂存：{归档ID: 答案文本}，点“查看答案”按钮时返回
_web_answers = {}
# 自测题整卷暂存：{归档ID: {q, a, subject}}，供错题本记录使用
_web_papers = {}
# 待提交答案状态：点「提交答案」后，下一条非指令消息自动当作答案批改
_web_pending_submit = {}
# 待讲解状态：点「请教AI讲题」后，下一条消息为题号或追问
_web_pending_explain = {}
# 待二级密码状态：重建知识库等危险操作需要确认
_web_pending_admin = {}


def _user_answers():
    """当前用户的答案暂存"""
    return _web_answers.setdefault(user_context.current_user(), {})


def _user_papers():
    """当前用户的试卷暂存"""
    return _web_papers.setdefault(user_context.current_user(), {})


# 后台任务：{task_id: {"status": "running"/"done"/"error", "reply": ..., "options": ..., "error": ..., "ts": ...}}
_task_results = {}
_task_lock = threading.Lock()
_TASK_TTL = 3600


def _prune_tasks():
    """清理过期任务结果"""
    now = time.time()
    with _task_lock:
        expired = [tid for tid, t in _task_results.items() if now - t.get("ts", now) > _TASK_TTL]
        for tid in expired:
            _task_results.pop(tid, None)


def _finish_task(task_id: str, status: str, **kwargs):
    with _task_lock:
        _task_results[task_id] = {"status": status, "ts": time.time(), **kwargs}


def _auto_title_conversation(cid: str, conv: dict, user: str = None):
    """根据对话内容生成简短标题（后台异步，不影响回复速度）"""
    try:
        user_context.set_current_user(user)
        from conversation_store import update_title
        from llm_summary import llm_request
        messages = conv.get("messages") or []
        lines = []
        for m in messages[-6:]:
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{role}：{(m.get('text') or '')[:200]}")
        prompt = (
            "你是对话命名助手。请根据下面的对话内容，给这个学习对话起一个简短、贴切的标题。\n"
            "要求：15 字以内，只输出标题本身，不要引号、不要解释、不要多余文字。\n\n"
            "对话内容：\n" + "\n".join(lines)
        )
        resp = llm_request(prompt, timeout=30)
        title = resp.strip().strip('"').strip("'").replace("\n", " ").strip()
        if title and not title.startswith("❌"):
            update_title(cid, title)
    except Exception as e:
        print(f"⚠️自动生成对话标题失败：{e}")


def _run_task(task_id: str, text: str, conversation_id: str = None, user: str = None):
    """后台执行聊天指令；有会话ID时自动保存对话"""
    try:
        user_context.set_current_user(user)
        u = user_context.current_user()
        if text.startswith("/"):
            # 任何新指令都会取消“待提交答案”状态
            _web_pending_submit.pop(u, None)
            _web_pending_explain.pop(u, None)
            _web_pending_admin.pop(u, None)
            reply = handle_web_command(text)
        elif _web_pending_submit.get(u):
            pending = _web_pending_submit.get(u)
            aid = pending.pop("aid", None)
            if not pending:
                _web_pending_submit.pop(u, None)
            reply = _do_submit(aid, text) if aid is not None else handle_web_command(text)
        elif _web_pending_admin.get(u):
            _web_pending_admin.pop(u, None)
            from config import check_admin_password
            from study_service import rebuild_text
            if check_admin_password(text):
                reply = rebuild_text()
            else:
                reply = "❌ 二级密码错误，重建已取消"
        elif _web_pending_explain.get(u):
            import re as _re
            st = _web_pending_explain.get(u)
            if st.get("mode") == "pick":
                nums = [int(x) for x in _re.findall(r"\d+", text)]
                if not nums:
                    reply = "请输入题号，例如：1 或 1,3,5"
                else:
                    parts_out = [_explain_question(st["aid"], n) for n in nums[:3]]
                    reply = "\n\n---\n\n".join(parts_out)
                    first_ok = not parts_out[0].startswith("❌") and "没有找到" not in parts_out[0]
                    if len(nums) == 1 and first_ok:
                        st["mode"] = "followup"
                        st["qno"] = nums[0]
                        st["history"] = [("讲解", parts_out[0])]
                    else:
                        _web_pending_explain.pop(u, None)
            elif st.get("mode") == "followup":
                reply = _followup_explain(st, text)
            else:
                _web_pending_explain.pop(u, None)
                reply = handle_web_command(text)
        else:
            reply = handle_web_command(text)
        if isinstance(reply, tuple):
            reply_text, reply_options = reply
        else:
            reply_text, reply_options = reply, None
        if conversation_id and not text.startswith("/"):
            try:
                from conversation_store import append_messages, get_conversation
                append_messages(conversation_id, text, reply_text)
                conv = get_conversation(conversation_id)
                if conv and len(conv.get("messages") or []) >= 4 and not conv.get("auto_titled"):
                    threading.Thread(target=_auto_title_conversation, args=(conversation_id, conv, u), daemon=True).start()
            except Exception as e:
                print(f"⚠️保存对话失败：{e}")
        print(f"📱网页版回复完成（{len(reply_text)} 字）")
        _finish_task(task_id, "done", reply=reply_text, options=reply_options)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"📱网页版处理异常：{e}")
        _finish_task(task_id, "error", error=f"处理失败：{e}")


def _run_upload_task(task_id: str, filename: str, file_bytes: bytes, user: str = None):
    """后台处理文件上传归档"""
    try:
        user_context.set_current_user(user)
        import file_parser
        supported, doc_text = file_parser.extract_file_text(filename, file_bytes)
        if not supported:
            _finish_task(task_id, "error", error="不支持的文件格式，请上传 PDF/DOC/DOCX/PPTX 或图片（JPG/PNG 等）")
            return
        if len(doc_text.strip()) < 20:
            suffix = filename.lower()
            if suffix.endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                reason = file_parser.last_image_error or "模型未返回文字内容，请换一张更清晰的图片"
                _finish_task(task_id, "error", error=f"图片识别未获得文字：{reason}")
            elif suffix.endswith(".pdf"):
                _finish_task(task_id, "error", error="PDF 文字过少：扫描版需要安装 OCR（pip install paddleocr paddlepaddle），普通 PDF 请确认内容完整")
            else:
                _finish_task(task_id, "error", error="文档文字过少，无法处理")
            return

        from llm_summary import auto_extract_archive_info, ai_simplify_filename
        from archive_db import archive_file
        from vector_kb import add_archive_to_kb

        auto_info = auto_extract_archive_info(doc_text)
        subj = auto_info["subject"]
        short_name = ai_simplify_filename(filename, subj)
        save_path, new_aid = archive_file(subj, short_name, file_bytes, filename, doc_text)
        kb_note = ""
        try:
            add_archive_to_kb(new_aid)
        except Exception as e:
            print(f"⚠️归档ID {new_aid} 知识库入库失败（归档记录已保存）：{e}")
            kb_note = f"\n⚠️知识库入库暂未完成：{e}\n可稍后在输入框发送 /rebuild 重建知识库"
        print(f"📱网页版归档完成：ID={new_aid} 科目={subj} 文件名={short_name}")
        reply = (f"✅归档成功！\n归档ID：{new_aid}\n科目：{subj}\n文件名：{short_name}\n\n"
                 f"继续学习：\n/cards id {new_aid} 生成背诵卡片\n/test id {new_aid} 生成自测题\n/plan id {new_aid} 生成学习计划"
                 f"{kb_note}")
        _finish_task(task_id, "done", reply=reply)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"📱网页版文件处理异常：{e}")
        _finish_task(task_id, "error", error=f"处理失败：{e}")

HELP_TEXT = """📚 网页版学习助手指令：
/list               查看归档清单
/today              查看今日待办任务
/extra              查看额外任务（今日完成后的拓展学习）
/report             学情诊断报告（连续打卡/分科进度/欠账建议）
/wrong              错题本（查看/记录/清除做错的题）
/clear              清空对话记忆（开始新话题）
/goals              长期学习目标列表
/goal 3年 德语 B2    创建长期目标（AI生成阶段规划）
/视频 关键词          搜索B站视频链接（示例：/视频 德语语法）
/cards id 3         把归档文档生成背诵卡片
/test id 3          把归档文档生成自测题
/plan id 3 [days 7]  生成完整学习计划
/daily id 3 [days 5] [每天60分钟] 生成每日任务并保存（可指定每天学习时长）
/progress id 3      查看学习进度
/done id 3 day 2    打卡完成第2天任务
/submit id 3 提交答案：点「提交答案」后直接输入如 B,A,C,D，自动批改并记入错题本
/explain id 3 [题号] 让AI老师讲题：检索归档资料，输出结构化解析，可继续追问
/del id 3           删除归档文档（同步清理知识库）
/polish 德语 文本    润色德语/英语文本（或 /polish id 3）
/merge 科目名        把同科目的所有文档合并为一条归档
/mergeinfo id 3      查看合并归档包含哪些原文档
/rebuild            重建知识库（需二级密码确认）
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


@app.route("/api/health")
def health():
    """健康检查接口（Docker HEALTHCHECK 使用）"""
    return jsonify({"ok": True, "service": "study-agent", "time": int(time.time())})


@app.route("/api/files", methods=["GET"])
@login_required
def files():
    """归档文件列表（管理后台表格视图用）"""
    from archive_db import get_all_archive_items
    items = get_all_archive_items()
    rows = []
    total = 0
    for it in items:
        sp = it.get("save_path") or ""
        size = 0
        if sp and os.path.exists(sp):
            try:
                size = os.path.getsize(sp)
            except OSError:
                size = 0
        total += size
        rows.append({
            "id": it.get("id"),
            "filename": it.get("filename") or "",
            "subject": it.get("subject") or "未分类",
            "size": size,
            "create_ts": it.get("create_ts"),
        })
    rows.sort(key=lambda r: r["create_ts"] or 0, reverse=True)
    return jsonify({"ok": True, "files": rows, "total_size": total, "count": len(rows)})


MARKET_PACKAGES = [
    {
        "id": "pkg_german", "type": "package", "title": "德语入门冲刺",
        "icon": "🇩🇪", "desc": "从零开始掌握德语基础，一个月搞定入门词汇与日常会话。",
        "tags": ["语言", "入门"], "subject": "德语",
        "skills": ["基础发音与字母", "高频词汇 800", "日常对话场景", "简单语法结构", "阅读理解入门"],
    },
    {
        "id": "pkg_writing", "type": "package", "title": "英语学术写作",
        "icon": "🇬🇧", "desc": "提升英语写作的句式、结构与论证逻辑，适合论文和考试作文。",
        "tags": ["语言", "学术"], "subject": "英语",
        "skills": ["句式升级技巧", "段落结构搭建", "论证逻辑训练", "润色与改写", "高频表达库"],
    },
    {
        "id": "pkg_dsa", "type": "package", "title": "数据结构与算法",
        "icon": "💻", "desc": "覆盖面试与考试高频考点，配合刷题与复盘形成完整训练闭环。",
        "tags": ["编程", "考试"], "subject": "编程",
        "skills": ["数组与链表", "栈与队列", "树与图", "排序与搜索", "动态规划", "面试题演练"],
    },
    {
        "id": "pkg_cert", "type": "package", "title": "职业考证速成",
        "icon": "📜", "desc": "高频考点 + 真题演练 + 错题复盘，为各类职业资格考试冲刺。",
        "tags": ["职业", "考试"], "subject": "考证",
        "skills": ["考纲拆解", "高频考点精讲", "真题演练", "错题复盘", "冲刺复习计划"],
    },
    {
        "id": "pkg_prompt", "type": "package", "title": "AI 提示词工程",
        "icon": "🤖", "desc": "学会用大模型高效解决问题：角色设定、结构化提示、迭代优化。",
        "tags": ["AI", "职业"], "subject": "AI",
        "skills": ["角色设定", "结构化提示词", "思维链引导", "结果评估", "提示词迭代"],
    },
    {
        "id": "pkg_psych", "type": "package", "title": "心理学与高效学习",
        "icon": "🧠", "desc": "理解认知原理与记忆曲线，把学习方法建立在科学基础上。",
        "tags": ["人文", "入门"], "subject": "心理学",
        "skills": ["认知原理", "学习动机", "记忆曲线", "情绪调节", "专注力训练"],
    },
    {
        "id": "pkg_kaoyan_en", "type": "package", "title": "考研英语一冲刺",
        "icon": "🎯", "desc": "真题精读 + 长难句拆解 + 作文模板，考前高效提分。",
        "tags": ["考试", "语言"], "subject": "英语",
        "skills": ["真题精读", "长难句拆解", "作文模板", "完形填空技巧", "翻译策略"],
    },
    {
        "id": "pkg_gaokao_math", "type": "package", "title": "高考数学一轮复习",
        "icon": "🧮", "desc": "按知识模块系统过一遍高考数学，配合典型题训练。",
        "tags": ["考试", "数学"], "subject": "数学",
        "skills": ["函数与导数", "三角函数", "数列", "立体几何", "概率统计", "解析几何"],
    },
    {
        "id": "pkg_python_office", "type": "package", "title": "Python 自动化办公",
        "icon": "🐍", "desc": "用 Python 批量处理文件、表格和邮件，告别重复劳动。",
        "tags": ["编程", "职业"], "subject": "编程",
        "skills": ["文件批量处理", "Excel 操作", "邮件自动化", "网页数据抓取", "定时任务"],
    },
    {
        "id": "pkg_xingce", "type": "package", "title": "公务员行测专项",
        "icon": "🏛", "desc": "五大模块系统训练，掌握行测解题节奏与技巧。",
        "tags": ["考试", "职业"], "subject": "考证",
        "skills": ["言语理解", "判断推理", "数量关系", "资料分析", "常识判断"],
    },
    {
        "id": "pkg_japanese", "type": "package", "title": "日语五十音入门",
        "icon": "🌸", "desc": "从五十音开始，掌握假名、发音和基础问候。",
        "tags": ["语言", "入门"], "subject": "日语",
        "skills": ["平假名", "片假名", "浊音与拗音", "日常问候", "基础句法"],
    },
    {
        "id": "pkg_paper", "type": "package", "title": "学术论文写作",
        "icon": "📚", "desc": "从选题到润色，完成一篇规范、有说服力的学术论文。",
        "tags": ["学术", "职业"], "subject": "学术",
        "skills": ["选题与文献", "大纲搭建", "论证展开", "引用规范", "降重润色"],
    },
    {
        "id": "pkg_econ", "type": "package", "title": "经济学原理基础",
        "icon": "📈", "desc": "理解供需、成本与市场结构，建立经济学分析框架。",
        "tags": ["人文", "入门"], "subject": "经济学",
        "skills": ["供需模型", "弹性理论", "成本分析", "市场结构", "宏观经济指标"],
    },
    {
        "id": "pkg_french", "type": "package", "title": "法语日常会话",
        "icon": "🥐", "desc": "掌握法语发音和旅行日常会话，开口说简单法语。",
        "tags": ["语言", "入门"], "subject": "法语",
        "skills": ["发音规则", "基础问候", "数字与时间", "餐厅购物", "旅行会话"],
    },
]

MARKET_DATASETS = [
    {"id": "ds_vocab", "type": "dataset", "title": "德语高频词汇 500", "icon": "🔤", "price": "免费", "subject": "德语"},
    {"id": "ds_essay", "type": "dataset", "title": "英语作文模板库", "icon": "📝", "price": "免费", "subject": "英语"},
    {"id": "ds_interview", "type": "dataset", "title": "编程面试题集", "icon": "🧩", "price": "¥9.9", "subject": "编程"},
    {"id": "ds_math", "type": "dataset", "title": "考研数学公式手册", "icon": "📐", "price": "免费", "subject": "数学"},
    {"id": "ds_gk", "type": "dataset", "title": "公考行测题库", "icon": "🏛", "price": "¥19.9", "subject": "考证"},
    {"id": "ds_history", "type": "dataset", "title": "世界史大事年表", "icon": "🌍", "price": "免费", "subject": "历史"},
    {"id": "ds_politics", "type": "dataset", "title": "考研政治考点手册", "icon": "📖", "price": "免费", "subject": "考证"},
    {"id": "ds_cet", "type": "dataset", "title": "四六级高频词组", "icon": "📇", "price": "免费", "subject": "英语"},
    {"id": "ds_n5", "type": "dataset", "title": "日语 N5 语法清单", "icon": "🗾", "price": "免费", "subject": "日语"},
    {"id": "ds_alg", "type": "dataset", "title": "算法模板速查", "icon": "⚙️", "price": "¥9.9", "subject": "编程"},
    {"id": "ds_accounting", "type": "dataset", "title": "会计从业基础题库", "icon": "🧾", "price": "¥12.9", "subject": "职业"},
    {"id": "ds_teacher", "type": "dataset", "title": "教师资格证简答题库", "icon": "🍎", "price": "免费", "subject": "考证"},
    {"id": "ds_topik", "type": "dataset", "title": "韩语 TOPIK 词汇", "icon": "🇰🇷", "price": "免费", "subject": "语言"},
    {"id": "ds_dynasty", "type": "dataset", "title": "中国历史朝代歌诀", "icon": "🏯", "price": "免费", "subject": "历史"},
]


def _market_items_with_added(items, existing_items):
    by_name = {it.get("filename"): it.get("id") for it in existing_items}
    out = []
    for it in items:
        fname = f"【技能包】{it['title']}.txt"
        aid = by_name.get(fname)
        out.append({**it, "added": aid is not None, "aid": aid})
    return out


@app.route("/api/market", methods=["GET"])
@login_required
def market():
    from archive_db import get_all_archive_items
    existing_items = get_all_archive_items()
    return jsonify({
        "ok": True,
        "packages": _market_items_with_added(MARKET_PACKAGES, existing_items),
        "datasets": _market_items_with_added(MARKET_DATASETS, existing_items),
    })


@app.route("/api/market_add", methods=["POST"])
@login_required
def market_add():
    data = request.get_json(silent=True) or {}
    item_id = data.get("id")
    item = next((x for x in MARKET_PACKAGES + MARKET_DATASETS if x["id"] == item_id), None)
    if not item:
        return jsonify({"ok": False, "error": "未找到该技能包"})
    from archive_db import get_all_archive_items, archive_file
    existing = {it.get("filename") for it in get_all_archive_items()}
    fname = f"【技能包】{item['title']}.txt"
    if fname in existing:
        return jsonify({"ok": True, "added": True, "already": True})
    skills_text = "\n".join(f"- {s}" for s in item.get("skills", []))
    doc_text = (
        f"# {item['title']}\n\n{item.get('desc', '')}\n\n"
        f"## 包含技能\n{skills_text}\n\n"
        "## 学习建议\n按技能顺序逐个掌握，每个技能安排 1-2 天，配合自测题、背诵卡片和每日任务巩固。"
    )
    save_path, new_aid = archive_file(
        item.get("subject", "技能包"),
        item["title"],
        doc_text.encode("utf-8"),
        fname,
        doc_text,
    )
    from vector_kb import add_archive_to_kb
    try:
        add_archive_to_kb(new_aid)
    except Exception as e:
        print(f"⚠️技能包入库知识库失败（归档已保存）：{e}")
    return jsonify({"ok": True, "added": True, "aid": new_aid})


@app.route("/api/stats", methods=["GET"])
@login_required
def stats():
    from review_scheduler import get_overall_stats
    return jsonify({"ok": True, **get_overall_stats()})


@app.route("/api/conversations", methods=["GET"])
@login_required
def conversations():
    from conversation_store import list_conversations
    return jsonify({"ok": True, "conversations": list_conversations()})


@app.route("/api/conversations", methods=["POST"])
@login_required
def create_conversation():
    from conversation_store import create_conversation
    conv = create_conversation()
    return jsonify({"ok": True, "conversation": conv})


@app.route("/api/conversations/<cid>", methods=["GET"])
@login_required
def conversation_detail(cid):
    from conversation_store import get_conversation
    conv = get_conversation(cid)
    if not conv:
        return jsonify({"ok": False, "error": "对话不存在"}), 404
    return jsonify({"ok": True, "conversation": conv})


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
    user = session.get("username")
    role = session.get("role", "")
    authed = bool(session.get("auth")) and bool(user)
    return jsonify({
        "ok": True,
        "need_login": bool(WEB_PASSWORD),
        "authed": authed,
        "username": user,
        "role": role,
        "is_admin": role == "admin",
    })


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    from user_auth import authenticate
    user = authenticate(username, password) if username else None
    # 兼容旧版：只填网页密码时按管理员登录
    if not user and password and WEB_PASSWORD and password == WEB_PASSWORD:
        user = {"username": ADMIN_USERNAME, "role": "admin"}
    if not user:
        print("📱网页版登录失败：用户名或密码错误")
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401
    session["auth"] = True
    session["username"] = user["username"]
    session["role"] = user["role"]
    print(f"📱网页版登录成功：{user['username']}（{user['role']}）")
    return jsonify({"ok": True, "username": user["username"], "role": user["role"], "is_admin": user["role"] == "admin"})


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    from user_auth import register as _register
    ok, msg = _register(username, password)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "msg": msg})


@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    session.pop("auth", None)
    session.pop("username", None)
    session.pop("role", None)
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


def _extract_study_topic(text: str) -> str:
    """从“我要学习德语语法，给我学习方案和视频链接”这类话里提取主题"""
    import re
    m = re.search(r"(?:学习|学)\s*([^，。,.!?！？\s]{1,15})(?:\s*(?:的)?(?:学习方案|学习计划|方案|计划|视频))?", text)
    if m:
        topic = m.group(1).strip()
        if topic:
            return topic
    return ""


def _do_submit(aid: int, raw_answers: str):
    """自动批改：客观题逐题比对，简答题用AI评分，错题统一进错题本"""
    from wrong_book import add_wrong_paper
    paper = _user_papers().get(aid)
    if not paper:
        return "请先通过「自测题」生成该归档的题目，再提交答案"
    result = grade_paper(paper, raw_answers)
    if not result["ok"]:
        return result["reply"]
    from study_service import grade_short_answers_text
    short_lines, short_wrong = grade_short_answers_text(result.get("short_items") or [])
    wrong_nos = list(result.get("wrong_nos") or []) + short_wrong

    if result["graded"] > 0:
        reply = f"📝【归档ID {aid}】客观题批改结果\n{result['reply']}"
    else:
        reply = f"📝【归档ID {aid}】批改结果\n（本卷没有可自动比对的客观题，简答题已用AI批改）"
    if short_lines:
        reply += f"\n\n📝 简答题批改：\n{short_lines}"
    if wrong_nos:
        add_wrong_paper(aid, paper["subject"], paper["q"], paper["a"], wrong_nos)
        reply += f"\n\n📕 已自动将 {len(wrong_nos)} 道错题记入错题本"
        return reply, [
            {"label": "📕 复习错题", "payload": {"step": "run", "cmd": f"/wrong id {aid}"}}
        ]
    if result["graded"] > 0 or short_lines:
        reply += "\n\n🎉 全部正确，继续保持！"
    return reply


def _explain_question(aid: int, qno: int) -> str:
    """AI老师讲解单题：RAG检索归档资料 + 结构化输出"""
    import json
    import re
    from wrong_book import split_numbered
    paper = _user_papers().get(aid)
    if not paper:
        return "请先通过「自测题」生成该归档的题目"
    q_map = {no: txt for no, txt in split_numbered(paper["q"])}
    q_text = q_map.get(qno)
    if not q_text:
        return f"没有找到第 {qno} 题，请确认题号"
    a_map = {no: txt for no, txt in split_numbered(paper["a"])}
    answer_text = a_map.get(qno, "")

    # RAG 检索相关教材片段，让讲解有依据、不瞎编
    ref_block = ""
    try:
        from vector_kb import query_knowledge
        kb = query_knowledge(q_text, top_k=3)
        if kb["chunks"]:
            ref_parts = []
            for txt, meta in zip(kb["chunks"], kb["meta"]):
                fn = meta.get("filename", "")
                ref_parts.append(f"（来源：{fn}）\n{txt}")
            ref_block = "\n\n".join(ref_parts)
    except Exception as e:
        print(f"⚠️讲题检索失败：{e}")

    prompt = f"""
你是耐心温和的AI老师，擅长用“循循善诱”的方式讲题。
请严格按下面的JSON格式输出，只输出JSON，不要任何额外说明：
{{"题型":"选择题/填空题/简答题等","题目分析":"拆解题干与选项的含义","考查知识点":["知识点1","知识点2"],"解题思路":"分步骤讲解，先易后难，引导学生自己思考","参考答案":"标准答案","易错点":"学生容易错在哪里"}}

【题目】
{q_text}

【参考答案参考】
{answer_text}

【教材参考资料】（仅作为讲解依据，不要照抄原文）
{ref_block or "（暂无检索到相关教材，请基于题目本身讲解）"}
"""
    from llm_summary import llm_request
    resp = llm_request(prompt, timeout=60)
    if resp.startswith("❌"):
        return resp
    m = re.search(r"\{.*\}", resp, re.DOTALL)
    if not m:
        return f"🤖【AI老师讲解】第{qno}题\n\n{resp}"
    try:
        data = json.loads(m.group())
    except Exception:
        data = None
    if not data:
        return f"🤖【AI老师讲解】第{qno}题\n\n{resp}"
    kps = data.get("考查知识点") or []
    if isinstance(kps, str):
        kps = [kps]
    lines = [
        f"🤖【AI老师讲解】第{qno}题",
        f"🧩 题型：{data.get('题型', '')}",
        f"📖 题目分析：{data.get('题目分析', '')}",
        "🎯 考查知识点：",
    ]
    for k in kps:
        lines.append(f"- {k}")
    lines.append(f"💡 解题思路：\n{data.get('解题思路', '')}")
    if data.get("参考答案"):
        lines.append(f"✅ 参考答案：{data.get('参考答案')}")
    if data.get("易错点"):
        lines.append(f"⚠️ 易错点：{data.get('易错点')}")
    return "\n".join(lines)


def _followup_explain(st: dict, question: str) -> str:
    """多轮追问：带上题目和上一轮讲解，保持上下文"""
    from wrong_book import split_numbered
    aid = st.get("aid")
    qno = st.get("qno")
    paper = _user_papers().get(aid)
    q_map = {no: txt for no, txt in split_numbered(paper["q"])} if paper else {}
    q_text = q_map.get(qno, "")
    history = st.get("history") or []
    last_explain = history[-1][1] if history else ""
    prompt = f"""
你是刚才给这位学生讲题的AI老师，请继续回答学生的追问。
语气温和耐心，结合刚才的讲解内容，先引导学生思考，再给出明确解释；如果学生的问题与本题无关，礼貌提醒回到本题。

【题目】
{q_text}

【刚才的讲解】
{last_explain}

【学生追问】
{question}

请直接输出你的回答，不需要JSON格式。
"""
    from llm_summary import llm_request
    resp = llm_request(prompt, timeout=60)
    st["history"].append((question, resp))
    if len(st["history"]) > 8:
        st["history"] = st["history"][-8:]
    return f"🤖【追问回复】\n{resp}"


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
        s = (it.get("subject") or "").strip() or "未分类"
        if s and s not in seen:
            seen.add(s)
            subjects.append(s)
    return subjects


def _list_docs(subject: str):
    """某科目下的归档文档列表 [(id, filename), ...]"""
    from archive_db import query_by_subject
    rows = query_by_subject(subject)
    return [(r["id"], r["filename"]) for r in rows]


def _parse_merge_sources(text: str) -> list:
    """从合并归档文本中解析【合并来源】列表"""
    import re
    m = re.search(r"【合并来源】\n(.*?)\n\n==========", text, re.DOTALL)
    if not m:
        return []
    origins = []
    for line in m.group(1).splitlines():
        if ". " in line:
            origins.append(line.split(". ", 1)[1].strip())
    return origins


def handle_web_command(text: str) -> str:
    """处理网页版指令/自由提问，返回回复文本"""
    if not text.startswith("/"):
        from vector_kb import rag_answer
        from chat_memory import add_turn, get_history
        key = user_context.current_user()
        topic = _extract_study_topic(text)
        if topic and ("视频" in text or ("学习" in text and ("方案" in text or "计划" in text))):
            from study_service import study_plan_and_videos_text
            reply = study_plan_and_videos_text(topic)
            add_turn(key, "user", text)
            add_turn(key, "assistant", reply)
            return reply
        history = get_history(key)
        reply = rag_answer(text, history)
        add_turn(key, "user", text)
        add_turn(key, "assistant", reply)
        return reply

    parts = text.split()
    cmd = parts[0].lower()

    if cmd in ("/clear", "/清空记忆"):
        from chat_memory import clear_history
        removed = clear_history("web")
        return "✅ 已清空对话记忆，开始新话题" + (f"（清理 {removed} 条消息）" if removed else "")

    if cmd in ("/video", "/视频"):
        from study_service import video_cmd_text
        return video_cmd_text(text)

    if cmd in ("/help", "/菜单", "/指令"):
        return HELP_TEXT

    if cmd == "/list":
        from archive_db import get_all_archive_summary
        return get_all_archive_summary()

    if cmd == "/today":
        from review_scheduler import get_today_learning_tasks
        return get_today_learning_tasks()

    if cmd == "/extra":
        from review_scheduler import get_extra_learning_tasks
        return get_extra_learning_tasks()

    if cmd == "/cards":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/cards id 归档ID"
        from study_service import cards_text
        return cards_text(aid)

    if cmd == "/test":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/test id 归档ID"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        only_questions = len(parts) >= 4 and parts[3] in ("仅题目", "只出题", "noanswer")
        from llm_summary import generate_test_questions
        q, a = generate_test_questions(row["file_text"], row["subject"], 20, with_answers=not only_questions)
        if only_questions:
            return q + f"\n\n💡需要答题/批改/讲题时，重新选择「答题模式」即可（会生成带答案版本）"
        answers = _user_answers()
        papers = _user_papers()
        if len(answers) >= 100:
            answers.pop(next(iter(answers)))
        answers[aid] = a
        papers[aid] = {"q": q, "a": a, "subject": row["subject"], "keys": _extract_answer_keys(a)}
        print(f"📱网页版已生成20道题并暂存答案：归档ID {aid}")
        return q + f"\n\n💡做完后点「✍️ 提交答案」，再直接输入答案（如 B,A,C,D）即可自动批改", [
            {"label": "📝 答题模式", "payload": {"step": "quiz", "aid": aid}},
            {"label": "🤔 请教AI讲题", "payload": {"step": "run", "cmd": f"/explain id {aid}"}},
            {"label": "✍️ 提交答案", "payload": {"step": "run", "cmd": f"/submit id {aid}"}},
            {"label": "📖 查看答案", "payload": {"step": "run", "cmd": f"/answer id {aid}"}}
        ]

    if cmd == "/submit":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/submit id 归档ID 你的答案\n示例：/submit id 3 B,A,C,D"
        paper = _user_papers().get(aid)
        if not paper:
            return "请先通过「自测题」生成该归档的题目，再提交答案"
        raw_answers = " ".join(parts[3:]).strip()
        if not raw_answers:
            _web_pending_submit[user_context.current_user()] = {"aid": aid}
            return "✍️ 请在输入框直接发送你的答案（如 B,A,C,D），我会自动批改。\n也支持：B A C D、BACD、1:B 2:A、第1题 B"
        return _do_submit(aid, raw_answers)

    if cmd == "/explain":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/explain id 归档ID [题号]\n示例：/explain id 3 1"
        if aid not in _user_papers():
            return (
                "🤔 需要先为该归档生成题目，才能讲题。",
                [
                    {"label": "📝 先生成题目", "payload": {"step": "run", "cmd": f"/test id {aid}"}},
                    {"label": "📕 错题本", "payload": {"step": "run", "cmd": f"/wrong id {aid}"}},
                ],
            )
        qno = None
        if len(parts) >= 4 and parts[1].lower() == "id" and parts[3].isdigit():
            qno = int(parts[3])
        elif len(parts) >= 3 and parts[2].isdigit():
            qno = int(parts[2])
        if qno is None:
            _web_pending_explain[user_context.current_user()] = {"aid": aid, "mode": "pick"}
            return "🤔 请发送要讲解的题号（如 1 或 1,3,5）"
        reply = _explain_question(aid, qno)
        if "没有找到" in reply:
            return reply
        _web_pending_explain[user_context.current_user()] = {"aid": aid, "mode": "followup", "qno": qno, "history": [("讲解", reply)]}
        return reply + "\n\n💬 还可以继续追问这道题，直接输入问题即可；发送其他指令则退出讲解"

    if cmd == "/answer":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/answer id 归档ID"
        ans = _user_answers().get(aid)
        if not ans:
            return "暂无该归档的答案，请先通过「自测题」生成题目"
        return f"📖【参考答案】\n{ans}", [
            {"label": "📕 有错题，记入错题本", "payload": {"step": "run", "cmd": f"/wrong add id {aid}"}}
        ]

    if cmd == "/plan":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/plan id 归档ID [days 7]"
        days = 5
        if len(parts) >= 5 and parts[3].lower() == "days":
            try:
                days = max(2, min(14, int(parts[4])))
            except ValueError:
                pass
        from study_service import plan_text
        return plan_text(aid, days, include_daily=False)

    if cmd == "/daily":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/daily id 归档ID [days 5] [每天60分钟]"
        days = 5
        if len(parts) >= 5 and parts[3].lower() == "days":
            try:
                days = max(2, min(14, int(parts[4])))
            except ValueError:
                pass
        import re as _re
        daily_minutes = None
        for tok in parts[4:]:
            if "分钟" in tok:
                nums = _re.findall(r"\d+", tok)
                if nums:
                    daily_minutes = max(15, min(240, int(nums[0])))
        from study_service import daily_text
        return daily_text(aid, days, daily_minutes)

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
        from study_service import done_text
        return done_text(aid, day_num)

    if cmd == "/report":
        from study_service import report_text
        return report_text()

    if cmd == "/goals":
        from study_service import goals_text
        return goals_text()

    if cmd == "/goal":
        from study_service import goal_cmd_text
        return goal_cmd_text(text)

    if cmd == "/wrong":
        from wrong_book import add_wrong_paper, clear_wrong, get_wrong, list_wrong
        sub = parts[1].lower() if len(parts) > 1 else ""
        aid = _parse_aid(parts)

        if sub in ("add", "done"):
            if aid is None:
                return f"用法：/wrong {sub} id 归档ID [题号,多个用逗号]"
            numbers = []
            for tok in parts[3:]:
                for n in tok.replace("，", ",").split(","):
                    n = n.strip()
                    if n.isdigit():
                        numbers.append(int(n))
            if sub == "add":
                paper = _web_papers.get(aid)
                if not paper:
                    return "📕 请先做该归档的自测题，才能标记错题"
                added = add_wrong_paper(aid, paper["subject"], paper["q"], paper["a"], numbers or None)
                if not added:
                    return "❌没有找到对应的题号，请检查后重试"
                return f"📕 已记录 {added} 道错题（归档ID {aid}）。\n复习：/wrong id {aid}\n掌握后可清除：/wrong done id {aid} 题号"
            removed = clear_wrong(aid, numbers or None)
            return f"✅ 已清除 {removed} 道错题" if removed else "没有找到可清除的错题"

        if aid is not None:
            items = get_wrong(aid)
            if not items:
                return "📕 该归档暂无错题"
            lines = [f"📕【归档ID {aid}】错题本（{len(items)} 道）"]
            for it in items:
                lines.append(f"\n❌第{it['no']}题：\n{clean_question_text(it.get('q', ''))}")
                if it.get("a"):
                    lines.append(f"📖参考：{it['a']}")
            lines.append(f"\n💡掌握后发送 /wrong done id {aid} 题号 清除")
            return "\n".join(lines)

        lst = list_wrong()
        if not lst:
            return "📕 错题本还是空的。\n做自测题 → 查看答案 → 点「有错题，记入错题本」即可记录"
        lines = ["📕【错题本总览】"]
        for x in lst:
            lines.append(f"归档ID {x['aid']}｜{x['subject']}｜{x['count']} 道错题")
        lines.append("\n💡查看：/wrong id 归档ID\n💡清除：/wrong done id 归档ID")
        return "\n".join(lines)

    if cmd == "/progress":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/progress id 归档ID"
        from study_service import progress_text
        return progress_text(aid)

    if cmd == "/del":
        from study_service import delete_text
        return delete_text(text)

    if cmd == "/polish":
        from study_service import polish_cmd_text
        return polish_cmd_text(text)

    if cmd == "/merge":
        subject = text[len(cmd):].strip()
        if not subject:
            return "用法：/merge 科目名"
        from archive_db import merge_subject_archives
        from vector_kb import add_archive_to_kb, remove_archive_from_kb
        result = merge_subject_archives(subject)
        if not result["ok"]:
            return result["error"]
        add_archive_to_kb(result["new_id"])
        for oid in result["old_ids"]:
            remove_archive_from_kb(oid)
        reply = (f"✅合并完成！\n"
                 f"科目：{result['subject']}\n"
                 f"合并了 {result['count']} 份文档 → 新归档ID：{result['new_id']}\n"
                 f"原记录 {result['old_ids']} 与原文件已清理\n\n"
                 f"继续学习：/cards id {result['new_id']}、/test id {result['new_id']}")
        return reply, [{"label": "📄 查看合并来源", "payload": {"step": "run", "cmd": f"/mergeinfo id {result['new_id']}"}}]

    if cmd in ("/mergeinfo", "/sources"):
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/mergeinfo id 归档ID"
        row = _get_archive(aid)
        if not row:
            return f"❌未找到归档ID {aid}"
        origins = _parse_merge_sources(row.get("file_text") or "")
        if not origins:
            return "该归档不是合并记录，或没有来源信息"
        return "该合并归档包含以下原文档：\n" + "\n".join(f"{i}. {name}" for i, name in enumerate(origins, 1))

    if cmd == "/rebuild":
        if session.get("role") != "admin":
            return "❌仅管理员可执行重建知识库"
        from study_service import rebuild_text
        from config import check_admin_password
        pwd = " ".join(parts[1:]).strip()
        if pwd:
            if not check_admin_password(pwd):
                return "❌ 二级密码错误，重建已取消"
            return rebuild_text()
        _web_pending_admin[user_context.current_user()] = {"action": "rebuild"}
        return "🔐 该操作需要二级密码确认：请在输入框发送密码，即可开始重建知识库（耗时较长）"

    return "未知指令，发送 /help 查看可用指令"


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id") or None
    print(f"📱网页版收到消息：{text[:200]}")
    if not text:
        return jsonify({"ok": False, "error": "消息为空"})
    _prune_tasks()
    task_id = uuid.uuid4().hex[:12]
    _finish_task(task_id, "running")
    threading.Thread(target=_run_task, args=(task_id, text, conversation_id, session.get("username")), daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id, "status": "running"})


@app.route("/api/task", methods=["GET"])
@login_required
def task_status():
    _prune_tasks()
    task_id = request.args.get("task_id", "")
    with _task_lock:
        res = _task_results.get(task_id)
    if not res:
        return jsonify({"ok": False, "error": "任务不存在或已过期"})
    if res["status"] == "running":
        return jsonify({"ok": True, "status": "running"})
    if res["status"] == "error":
        return jsonify({"ok": False, "error": res.get("error", "处理失败")})
    return jsonify({"ok": True, "status": "done", "reply": res.get("reply"), "options": res.get("options")})


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
        if next_cmd == "merge":
            options_list = [{
                "label": f"确认合并「{subject}」全部文档",
                "payload": {"step": "run", "cmd": f"/merge {subject}"}
            }]
            return jsonify({"ok": True, "prompt": f"② 确认合并（{subject}）：", "options": options_list})
        docs = _list_docs(subject)
        options_list = []
        for did, fname in docs:
            # 合并记录只显示合并内容名字，不显示原始文档列表
            label = fname if fname.startswith("【合并】") else f"ID{did} {fname}"
            if next_cmd == "delete":
                payload = {"step": "confirm", "next": "delete", "aid": did}
            elif next_cmd == "test":
                payload = {"step": "mode", "next": "test", "subject": subject, "aid": did}
            elif next_cmd in ("plan", "daily", "done"):
                payload = {"step": "days", "next": next_cmd, "subject": subject, "aid": did}
            else:
                payload = {"step": "run", "cmd": f"/{next_cmd} id {did}"}
            options_list.append({"label": label, "payload": payload})
        print(f"📱网页版选项：选择文档（{subject}，共{len(docs)}个）")
        return jsonify({"ok": True, "prompt": f"② 选择文档（{subject}）：", "options": options_list})

    if step == "mode":
        row = _get_archive(data.get("aid"))
        if not row:
            return jsonify({"ok": False, "error": "归档记录不存在，可能已被删除"})
        prompt = f"③ 选择「{row.get('filename', '')}」的学习模式："
        options_list = [
            {"label": "📝 答题模式（推荐）", "payload": {"step": "run", "cmd": f"/test id {row['id']}", "autoQuiz": row["id"]}},
            {"label": "🤔 AI 讲题", "payload": {"step": "run", "cmd": f"/explain id {row['id']}"}},
            {"label": "📄 仅生成题目（不生成答案）", "payload": {"step": "run", "cmd": f"/test id {row['id']} 仅题目"}},
            {"label": "📕 错题本（不生成）", "payload": {"step": "run", "cmd": f"/wrong id {row['id']}"}},
            {"label": "❌ 取消，不生成", "payload": {"step": "back"}},
        ]
        return jsonify({"ok": True, "prompt": prompt, "options": options_list})

    if step == "use":
        row = _get_archive(data.get("aid"))
        if not row:
            return jsonify({"ok": False, "error": "归档记录不存在，可能已被删除"})
        subject = row.get("subject", "")
        options_list = [
            {"label": "📝 自测题", "payload": {"step": "mode", "aid": row["id"]}},
            {"label": "📇 背诵卡片", "payload": {"step": "run", "cmd": f"/cards id {row['id']}"}},
            {"label": "📅 学习计划", "payload": {"step": "days", "next": "plan", "subject": subject, "aid": row["id"]}},
            {"label": "📆 每日任务", "payload": {"step": "days", "next": "daily", "subject": subject, "aid": row["id"]}},
            {"label": "✅ 打卡", "payload": {"step": "days", "next": "done", "subject": subject, "aid": row["id"]}},
            {"label": "📕 错题本", "payload": {"step": "run", "cmd": f"/wrong id {row['id']}"}},
        ]
        return jsonify({"ok": True, "prompt": f"📋 使用「{row.get('filename', '')}」：", "options": options_list})

    if step == "back":
        return jsonify({"ok": True, "prompt": "已取消，没有生成任何题目。", "options": []})

    if step == "confirm":
        row = _get_archive(data.get("aid"))
        if not row:
            return jsonify({"ok": False, "error": "归档记录不存在，可能已被删除"})
        fname = row.get("filename", "")
        options_list = [{
            "label": f"确认删除「{fname}」",
            "payload": {"step": "run", "cmd": f"/del id {row['id']}"}
        }]
        return jsonify({"ok": True, "prompt": "③ 确认删除（删除后不可恢复）：", "options": options_list})

    if step == "days":
        days = data.get("days")
        if next_cmd == "done":
            # 打卡不需要选周期：直接定位该归档最靠前未完成的一天
            row = _get_archive(data.get("aid"))
            if not row:
                return jsonify({"ok": False, "error": "归档记录不存在，可能已被删除"})
            from review_scheduler import get_archive_progress
            progress_rows = get_archive_progress(row["id"])
            first_unfinished = None
            for pr in progress_rows:
                if not pr["finished"]:
                    first_unfinished = pr["day_no"]
                    break
            if first_unfinished is None:
                return jsonify({"ok": True, "prompt": "🎉 该归档所有任务都已完成！可以看看「额外任务」拓展学习", "options": []})
            task_preview = ""
            for pr in progress_rows:
                if pr["day_no"] == first_unfinished:
                    task_preview = (pr["task_content"] or "").strip()[:60]
                    break
            prompt = f"📌 归档ID {row['id']} 第 {first_unfinished} 天：{task_preview}"
            return jsonify({
                "ok": True,
                "prompt": prompt,
                "options": [
                    {"label": f"✅ 打卡第 {first_unfinished} 天", "payload": {"step": "run", "cmd": f"/done id {row['id']} day {first_unfinished}"}}
                ]
            })
        if days is None:
            options_list = [
                {"label": f"{d} 天", "payload": {"step": "days", "next": next_cmd, "subject": subject, "aid": aid, "days": d}}
                for d in (3, 5, 7, 10, 14)
            ]
            return jsonify({"ok": True, "prompt": "③ 选择学习天数：", "options": options_list})
        if next_cmd == "daily":
            return jsonify({
                "ok": True,
                "prompt": "④ 选择每天学习时长：",
                "options": [
                    {"label": f"{m} 分钟", "payload": {"step": "minutes", "next": "daily", "subject": subject, "aid": aid, "days": days, "minutes": m}}
                    for m in (30, 60, 90, 120)
                ]
            })
        cmd = f"/{next_cmd} id {aid} days {days}"
        print(f"📱网页版选项完成，执行：{cmd}")
        return jsonify({"ok": True, "prompt": "", "options": [], "run": cmd})

    if step == "minutes":
        days = data.get("days")
        minutes = data.get("minutes")
        cmd = f"/daily id {aid} days {days} 每天{minutes}分钟"
        print(f"📱网页版选项完成，执行：{cmd}")
        return jsonify({"ok": True, "prompt": "", "options": [], "run": cmd})

    return jsonify({"ok": False, "error": "无效操作"})


@app.route("/api/quiz", methods=["GET"])
@login_required
def quiz_data():
    try:
        aid = int(request.args.get("aid", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "缺少归档ID"})
    paper = _user_papers().get(aid)
    if not paper:
        return jsonify({"ok": False, "error": "请先通过「自测题」生成该归档的题目"})
    from wrong_book import split_numbered
    questions = []
    for no, sec in split_numbered(paper["q"]):
        questions.append({"no": no, "text": sec, "options": _parse_quiz_options(sec)})
    print(f"📱答题模式加载：归档ID {aid}（{len(questions)} 题）")
    return jsonify({"ok": True, "aid": aid, "subject": paper.get("subject", ""), "questions": questions})


@app.route("/api/quiz_resume", methods=["GET"])
@login_required
def quiz_resume():
    try:
        aid = int(request.args.get("aid", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "缺少归档ID"})
    from quiz_store import get_record
    rec = get_record(aid)
    return jsonify({"ok": True, **rec})


@app.route("/api/quiz_save", methods=["POST"])
@login_required
def quiz_save():
    data = request.get_json(silent=True) or {}
    try:
        aid = int(data.get("aid"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "缺少归档ID"})
    from quiz_store import save_record
    save_record(aid, data.get("answers") or {}, data.get("marked") or [])
    print(f"💾答题进度已保存：归档ID {aid}")
    return jsonify({"ok": True})


@app.route("/api/quiz_submit", methods=["POST"])
@login_required
def quiz_submit():
    data = request.get_json(silent=True) or {}
    try:
        aid = int(data.get("aid"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "缺少归档ID"})
    paper = _user_papers().get(aid)
    if not paper:
        return jsonify({"ok": False, "error": "请先通过「自测题」生成该归档的题目"})
    answers = data.get("answers") or {}
    from wrong_book import split_numbered
    q_nos = [no for no, _ in split_numbered(paper["q"])]
    ordered = ",".join(str(answers.get(str(no)) or answers.get(no) or "X").strip() for no in q_nos)
    result = _do_submit(aid, ordered)
    if isinstance(result, tuple):
        reply, options = result
    else:
        reply, options = result, None
    print(f"📝提交批改：归档ID {aid}")
    return jsonify({"ok": True, "reply": reply, "options": options})


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
    _prune_tasks()
    task_id = uuid.uuid4().hex[:12]
    _finish_task(task_id, "running")
    threading.Thread(target=_run_upload_task, args=(task_id, filename, file_bytes, session.get("username")), daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id, "status": "running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
