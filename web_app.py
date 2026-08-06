# -*- coding: utf-8 -*-
"""网页版学习助手：复用学习助手核心功能，提供手机/电脑可用的聊天界面（PWA）"""
import os
import functools
import threading
import uuid
import time
from flask import Flask, request, jsonify, session, send_file
from quiz_logic import extract_answer_keys as _extract_answer_keys
from quiz_logic import parse_quiz_options as _parse_quiz_options
from quiz_logic import grade_paper

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

# 自测题答案暂存：{归档ID: 答案文本}，点“查看答案”按钮时返回
_web_answers = {}
# 自测题整卷暂存：{归档ID: {q, a, subject}}，供错题本记录使用
_web_papers = {}
# 待提交答案状态：点「提交答案」后，下一条非指令消息自动当作答案批改
_web_pending_submit = {}
# 待讲解状态：点「请教AI讲题」后，下一条消息为题号或追问
_web_pending_explain = {}

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


def _run_task(task_id: str, text: str):
    """后台执行聊天指令"""
    try:
        if text.startswith("/"):
            # 任何新指令都会取消“待提交答案”状态
            _web_pending_submit.clear()
            _web_pending_explain.clear()
            reply = handle_web_command(text)
        elif _web_pending_submit:
            aid = _web_pending_submit.pop("aid", None)
            reply = _do_submit(aid, text) if aid is not None else handle_web_command(text)
        elif _web_pending_explain:
            import re as _re
            st = _web_pending_explain
            if st.get("mode") == "pick":
                nums = [int(x) for x in _re.findall(r"\d+", text)]
                if not nums:
                    reply = "请输入题号，例如：1 或 1,3,5"
                else:
                    parts_out = [_explain_question(st["aid"], n) for n in nums[:3]]
                    reply = "\n\n---\n\n".join(parts_out)
                    if len(nums) == 1:
                        st["mode"] = "followup"
                        st["qno"] = nums[0]
                        st["history"] = [("讲解", parts_out[0])]
                    else:
                        _web_pending_explain.clear()
            elif st.get("mode") == "followup":
                reply = _followup_explain(st, text)
            else:
                _web_pending_explain.clear()
                reply = handle_web_command(text)
        else:
            reply = handle_web_command(text)
        if isinstance(reply, tuple):
            reply_text, reply_options = reply
        else:
            reply_text, reply_options = reply, None
        print(f"📱网页版回复完成（{len(reply_text)} 字）")
        _finish_task(task_id, "done", reply=reply_text, options=reply_options)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"📱网页版处理异常：{e}")
        _finish_task(task_id, "error", error=f"处理失败：{e}")


def _run_upload_task(task_id: str, filename: str, file_bytes: bytes):
    """后台处理文件上传归档"""
    try:
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


def _do_submit(aid: int, raw_answers: str):
    """自动批改：客观题逐题比对，简答题用AI评分，错题统一进错题本"""
    from wrong_book import add_wrong_paper
    paper = _web_papers.get(aid)
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
    paper = _web_papers.get(aid)
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
    paper = _web_papers.get(aid)
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
        from llm_summary import generate_test_questions
        q, a = generate_test_questions(row["file_text"], row["subject"], 20)
        if len(_web_answers) >= 100:
            _web_answers.pop(next(iter(_web_answers)))
        _web_answers[aid] = a
        _web_papers[aid] = {"q": q, "a": a, "subject": row["subject"], "keys": _extract_answer_keys(a)}
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
        paper = _web_papers.get(aid)
        if not paper:
            return "请先通过「自测题」生成该归档的题目，再提交答案"
        raw_answers = " ".join(parts[3:]).strip()
        if not raw_answers:
            _web_pending_submit["aid"] = aid
            return "✍️ 请在输入框直接发送你的答案（如 B,A,C,D），我会自动批改。\n也支持：B A C D、BACD、1:B 2:A、第1题 B"
        return _do_submit(aid, raw_answers)

    if cmd == "/explain":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/explain id 归档ID [题号]\n示例：/explain id 3 1"
        if aid not in _web_papers:
            return "请先通过「自测题」生成该归档的题目"
        qno = None
        if len(parts) >= 4 and parts[1].lower() == "id" and parts[3].isdigit():
            qno = int(parts[3])
        elif len(parts) >= 3 and parts[2].isdigit():
            qno = int(parts[2])
        if qno is None:
            _web_pending_explain = {"aid": aid, "mode": "pick"}
            return "🤔 请发送要讲解的题号（如 1 或 1,3,5）"
        reply = _explain_question(aid, qno)
        _web_pending_explain = {"aid": aid, "mode": "followup", "qno": qno, "history": [("讲解", reply)]}
        return reply + "\n\n💬 还可以继续追问这道题，直接输入问题即可；发送其他指令则退出讲解"

    if cmd == "/answer":
        aid = _parse_aid(parts)
        if aid is None:
            return "用法：/answer id 归档ID"
        ans = _web_answers.get(aid)
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
                lines.append(f"\n❌第{it['no']}题：\n{it['q']}")
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
        from study_service import rebuild_text
        return rebuild_text()

    return "未知指令，发送 /help 查看可用指令"


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    print(f"📱网页版收到消息：{text[:200]}")
    if not text:
        return jsonify({"ok": False, "error": "消息为空"})
    _prune_tasks()
    task_id = uuid.uuid4().hex[:12]
    _finish_task(task_id, "running")
    threading.Thread(target=_run_task, args=(task_id, text), daemon=True).start()
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
            elif next_cmd in ("plan", "daily", "done"):
                payload = {"step": "days", "next": next_cmd, "subject": subject, "aid": did}
            else:
                payload = {"step": "run", "cmd": f"/{next_cmd} id {did}"}
            options_list.append({"label": label, "payload": payload})
        print(f"📱网页版选项：选择文档（{subject}，共{len(docs)}个）")
        return jsonify({"ok": True, "prompt": f"② 选择文档（{subject}）：", "options": options_list})

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
    paper = _web_papers.get(aid)
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
    paper = _web_papers.get(aid)
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
    threading.Thread(target=_run_upload_task, args=(task_id, filename, file_bytes), daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id, "status": "running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
