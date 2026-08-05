import requests
import json
import time
from config import LLM_API_KEY, LLM_API_URL, LLM_MODEL

def llm_request(prompt: str, timeout: int = 30) -> str:
    """通用LLM请求，增加超时捕获、异常处理"""
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    try:
        resp = requests.post(LLM_API_URL, headers=headers, json=data, timeout=timeout)
        resp.raise_for_status()
        resp_json = resp.json()
        content = resp_json["choices"][0]["message"]["content"]
        return content.strip()
    except requests.exceptions.Timeout:
        return "❌大模型接口请求超时，请稍后重试"
    except Exception as e:
        print(f"LLM调用异常: {str(e)}")
        return f"❌LLM接口调用失败：{str(e)}"

def generate_knowledge_points(text: str, subject: str) -> str:
    prompt = f"""
你是专业课学习助手，处理{subject}课程文档，提取核心知识点，输出标准Markdown笔记：
1. 根据内容逻辑分小节整理，重要概念、公式使用**加粗**
2. 删除无关废话、冗余描述，只保留考点与核心原理
3. 公式采用单行LaTeX $...$ 格式
文档内容：
{text[:6000]}
"""
    return llm_request(prompt)

def generate_test_questions(text: str, subject: str, num: int = 10, with_answers: bool = True) -> tuple[str, str]:
    if with_answers:
        answer_section = """
第二部分：标准答案+简要解析
分割标记：===ANSWER===
"""
    else:
        answer_section = "本次只生成题目，不要输出任何答案或解析。"

    prompt = f"""
任务：基于下方{subject}学习资料生成{num}道自测习题，混合选择题+简答题。
⚠️严格输出规范，禁止额外开场白、总结！
第一部分：全部试题
分割标记：===QUESTION===
{answer_section}

格式要求：
1. 数学公式使用 $表达式$，禁止多层反斜杠
2. 使用Markdown排版，条理清晰
3. 选择题附带A/B/C/D选项
4. 简答题答案附上思路解析

参考资料：
{text[:6000]}
"""
    full_output = llm_request(prompt)

    # 健壮分割逻辑
    if not with_answers:
        return full_output.strip(), ""

    if "===QUESTION===" in full_output and "===ANSWER===" in full_output:
        seg_q = full_output.split("===QUESTION===")[-1]
        # 用 maxsplit=1 只按第一个标记分割，防止答案内容里再次出现标记导致解包失败
        q_part, a_part = seg_q.split("===ANSWER===", 1)
        question_part = q_part.strip()
        answer_part = a_part.strip()
        if not question_part or not answer_part:
            question_part = full_output
            answer_part = "⚠️AI输出未遵循分割标记，无法自动分离答案"
    else:
        question_part = full_output
        answer_part = "⚠️AI输出未遵循分割标记，无法自动分离答案"
    return question_part, answer_part

def extract_task_list(task_text: str) -> list:
    """从每日任务文本中提取任务清单（供网页版/飞书版共用）"""
    import re
    pattern = r"## Day(\d+)\n任务内容：(.*?)(?=\n## Day|$)"
    matches = re.findall(pattern, task_text, re.DOTALL)
    task_arr = []
    for _, content in matches:
        task_arr.append(content.strip())
    return task_arr

def format_msg(raw_text: str) -> str:
    text = raw_text.replace("\\\\(", "$")
    text = text.replace("\\\\)", "$")
    text = text.replace("\\\\begin", "\\begin")
    text = text.replace("\\\\end", "\\end")
    text = text.replace("\\\\\\\\", "\\\\")
    return text

def generate_study_plan(doc_content: str, subject: str):
    prompt = f"""
你是学习规划助手。基于下面课程文档内容，生成一份结构化学习计划。
要求：
1. 划分学习阶段（预习→精读→习题→复盘）
2. 合理分配每日任务，建议3~7天学习周期
3. 标出重点、难点、自测方式
4. 排版清晰，不要多余废话
科目：{subject}
文档内容：
{doc_content[:6000]}
"""
    res = llm_request(prompt)
    return format_msg(res)

def split_plan_to_daily_tasks(full_plan: str, subject: str, days: int = 5):
    prompt = f"""
你是学习拆解助手。
已有完整中长期学习规划，均衡拆分为【{days}天每日学习清单】
要求：
1. 每一天明确：学习内容、重点、自测任务、预估耗时
2. 难度循序渐进，前面预习精读，后面刷题复盘
3. 严格格式：
## Day1
任务内容：xxx
重点：xxx
自测：xxx

## Day2
任务内容：xxx
重点：xxx
自测：xxx
科目：{subject}
整体学习规划：
{full_plan[:6000]}
最后不要额外总结，只输出结构化内容。
"""
    resp = llm_request(prompt)
    return format_msg(resp)

def auto_extract_archive_info(pdf_text: str) -> dict:
    """AI识别文档科目和核心知识点（网页版/飞书版共用）"""
    import re
    import json
    short_text = pdf_text[:3000]
    prompt = f"""
你是专业课程资料归档助手，严格遵守下面所有规则，**只输出纯JSON字符串**，禁止输出任何前置说明、注释、markdown、思考过程、多余换行。
{{"subject":"科目名称","keypoint":"文档核心知识点概括，20～45字"}}
文档片段：
{short_text}
"""
    resp = llm_request(prompt)
    json_match = re.search(r"\{.*\}", resp, re.DOTALL)
    if not json_match:
        return {"subject": "未知科目", "keypoint": "未识别知识点"}
    try:
        data = json.loads(json_match.group())
        subject = str(data.get("subject", "未知科目")).strip()
        kp = str(data.get("keypoint", "未识别知识点")).strip()
        return {"subject": subject, "keypoint": kp}
    except Exception:
        return {"subject": "未知科目", "keypoint": "未识别知识点"}

def ai_simplify_filename(raw_name: str, subject: str) -> str:
    """AI精简文件名（网页版/飞书版共用）"""
    import re
    prompt = f"""精简文件名，只输出名称，不要任何解释。
原始名称：{raw_name}
科目：{subject}
去除.pdf、副本、扫描版、水印、多余括号，控制20字内。
"""
    try:
        short_name = llm_request(prompt).strip().replace("\n", "")
        if len(short_name) > 30:
            raise Exception("过长")
        return short_name
    except Exception:
        fallback = re.sub(r"[（(].*?[）)]", "", raw_name)
        fallback = re.sub(r"\.(pdf|docx|doc|pptx)", "", fallback)
        return fallback.strip()

def polish_text(text: str, lang: str = "") -> str:
    """润色德语/英语文本：让文本流畅自然，并遵循用户附加的修改要求"""
    lang_map = {
        "德语": "德语", "德": "德语", "de": "德语",
        "英语": "英语", "英": "英语", "en": "英语",
    }
    lang_desc = lang_map.get(lang.strip().lower(), "")
    if lang_desc:
        lang_line = f"目标语言：{lang_desc}（保持该语言输出，不要翻译成中文）"
    else:
        lang_line = "目标语言：根据原文自动判断（德语或英语），保持原文语言输出"

    prompt = f"""
你是专业的德语/英语文本润色助手。用户会给出待修改的文本，可能还包含修改要求。
任务：把文本修改得流畅、自然、符合语言习惯，同时满足用户附加的修改要求
（例如：更口语化、更正式、更简洁、更学术、控制字数、保持原意等）。
规则：
1. {lang_line}
2. 保持原意，不添加原文没有的内容，不删减核心信息
3. 修正语法、用词、表达不自然的地方，让整体读起来通顺
4. 如果用户没有明确要求，默认保持原风格，只做语言层面的润色
5. 只输出修改后的文本本身，不要任何解释、前缀、引号或 Markdown 标记

【用户提供的内容】
{text[:8000]}
"""
    return llm_request(prompt)

def generate_memory_cards(text: str, subject: str, num: int = 12) -> str:
    """把文档内容提炼成知识点和背诵卡片（结合用户语言习惯输出）"""
    prompt = f"""
你是学习助手。请把下面的{subject}课程资料提炼成「知识点」和「背诵卡片」，方便用户背诵复习。
要求：
1. 先用「📌 知识点」列出 3~5 条核心知识点（每条一句话，简洁明确）
2. 再用「📇 背诵卡片」生成 {num} 张卡片，每张卡片固定格式：
【卡片序号】主题
问题：xxx
答案：xxx
3. 语言规则：整体用中文输出；如果内容涉及德语/英语，卡片采用「原文/术语 + 中文释义 + 例句」的形式，便于背诵
4. 只输出以上两部分内容，不要任何开场白和额外解释

【文档内容】
{text[:8000]}
"""
    return llm_request(prompt)
